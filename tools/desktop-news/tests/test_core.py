"""离线单元测试：不联网、不需要 tkinter，直接 python -m unittest discover tests 即可跑。

覆盖三个最容易回归的地方：时间窗口（决定抓哪一段）、去重聚类（决定卡片质量）、
AI 失败时的降级路径（决定教室屏幕上会不会空屏）。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newscast import classify, config, pipeline, sources, textutil  # noqa: E402
from newscast.models import NewsCard, RawItem, SourceStatus  # noqa: E402
from newscast.store import Store  # noqa: E402

RSS_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>示例源</title>
<item>
  <title><![CDATA[我国成功发射试验二十八号卫星]]></title>
  <link>https://example.com/a1</link>
  <description>&lt;p&gt;卫星进入预定轨道。&lt;/p&gt;</description>
  <pubDate>Wed, 30 Sep 2026 02:30:00 +0800</pubDate>
</item>
<item>
  <title>没有时间的条目</title>
  <link>https://example.com/a2</link>
  <description>普通描述</description>
</item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>示例 Atom</title>
  <entry>
    <title>开源大模型推理成本下降</title>
    <link rel="alternate" href="https://example.com/atom1"/>
    <summary>成本下降九成</summary>
    <published>2026-09-30T07:10:00+08:00</published>
  </entry>
</feed>"""


class TestTextUtil(unittest.TestCase):
    def test_clean_text_strips_html_and_entities(self):
        raw = "<p>你好&nbsp;&nbsp;世界</p><script>var a=1;</script>"
        self.assertEqual(textutil.clean_text(raw), "你好 世界")

    def test_normalize_title_ignores_punctuation_and_prefix(self):
        a = textutil.normalize_title("【视频】国足1-2不敌韩国，无缘决赛！")
        b = textutil.normalize_title("国足 1:2 不敌韩国 无缘决赛")
        self.assertEqual(a, b)

    def test_fingerprint_stable_and_url_independent(self):
        first = textutil.fingerprint("同一标题", "https://a.com/1?utm=x")
        second = textutil.fingerprint("同一标题", "https://b.com/2")
        self.assertEqual(first, second)

    def test_parse_time_formats(self):
        self.assertIsNotNone(textutil.parse_time("Wed, 30 Sep 2026 02:30:00 +0800"))
        self.assertIsNotNone(textutil.parse_time("2026-09-30T07:10:00+08:00"))
        self.assertIsNotNone(textutil.parse_time("2026-09-30 07:10:00"))
        self.assertIsNotNone(textutil.parse_time("2026年09月30日 07:10"))
        relative = textutil.parse_time("3小时前")
        self.assertIsNotNone(relative)
        self.assertLess(abs((textutil.now_utc() - relative).total_seconds() - 3 * 3600), 60)
        self.assertIsNone(textutil.parse_time(""))
        self.assertIsNone(textutil.parse_time("不是时间"))

    def test_similarity_separates_same_and_different_events(self):
        """阈值 0.42 是按真实标题对校准的：同事件必须过线，不同事件必须不过线。"""
        same = [
            ("试验二十八号卫星发射成功", "我国成功发射试验二十八号卫星 用于空间环境探测"),
            ("某地通报一起交通事故 已开展救援", "某地通报一起交通事故 已开展救援"),
            ("教育部部署新学期中小学生作业管理工作", "教育部：新学期严格控制中小学生作业总量"),
        ]
        different = [
            ("人工智能大模型开源生态迎来新进展", "教育部部署新学期中小学生作业管理工作"),
            ("罕见！野生大熊猫现身村民院坝", "某地通报一起交通事故 已开展救援"),
            ("苹果发布新款手机 售价创新高", "苹果价格上涨 果农增收"),
        ]
        for a, b in same:
            self.assertTrue(textutil.is_same_event(a, b), f"应判为同一事件：{a} / {b}")
        for a, b in different:
            self.assertFalse(textutil.is_same_event(a, b), f"不应判为同一事件：{a} / {b}")


class TestSources(unittest.TestCase):
    def test_parse_rss(self):
        items = sources.parse_feed(RSS_SAMPLE, {"name": "示例源", "hint": "top"})
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "我国成功发射试验二十八号卫星")
        self.assertEqual(items[0].url, "https://example.com/a1")
        self.assertEqual(items[0].summary, "卫星进入预定轨道。")
        self.assertIsNotNone(items[0].published)
        self.assertIsNone(items[1].published)

    def test_parse_atom(self):
        items = sources.parse_feed(ATOM_SAMPLE, {"name": "Atom源"})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://example.com/atom1")
        self.assertIsNotNone(items[0].published)

    def test_parse_baidu_hot_nested(self):
        payload = {"data": {"cards": [{"content": [
            {"content": [{"word": "热词A", "url": "https://x/1", "hotScore": "1234567"},
                         {"word": "热词B"}]}
        ]}]}}
        items = sources.parse_baidu_hot(payload, {"name": "百度热搜"})
        self.assertEqual([i.title for i in items], ["热词A", "热词B"])
        self.assertIn("1234567", items[0].extra)

    def test_parse_toutiao_hot(self):
        payload = {"data": [{"Title": "头条词条", "Url": "https://t/1", "HotValue": "999"}]}
        items = sources.parse_toutiao_hot(payload, {"name": "头条热榜"})
        self.assertEqual(items[0].title, "头条词条")
        self.assertEqual(items[0].source, "头条热榜")

    def test_bad_xml_raises(self):
        with self.assertRaises(ValueError):
            sources.parse_feed("<html>不是 feed</html>", {"name": "坏源"})


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config("/nonexistent/config.json")
        self.categories = self.cfg["categories"]

    def test_guess_category_by_keywords(self):
        tech = RawItem(title="国产芯片取得新进展", summary="半导体制造工艺突破")
        self.assertEqual(classify.guess_category(tech, self.categories), "tech")
        top = RawItem(title="某地发生地震 救援展开", summary="已启动应急响应")
        self.assertEqual(classify.guess_category(top, self.categories), "top")

    def test_cluster_merges_same_event_across_sources(self):
        now = textutil.now_utc()
        items = [
            RawItem(title="我国成功发射试验二十八号卫星", url="https://a/1", source="新华网",
                    summary="卫星进入预定轨道", published=now),
            RawItem(title="试验二十八号卫星发射成功", url="https://b/2", source="人民网",
                    summary="开展空间环境探测", published=now),
            RawItem(title="教育部部署新学期作业管理", url="https://c/3", source="人民网",
                    summary="控制作业总量", published=now),
        ]
        clusters = classify.cluster_items(items, self.categories, __import__("logging").getLogger("t"))
        self.assertEqual(len(clusters), 2)
        satellite = next(c for c in clusters if "卫星" in c.title)
        self.assertEqual(len(satellite.sources), 2)
        self.assertEqual(satellite.item_count, 2)
        # 多源报道的分数必须高于单源，这是「大事件」排序的依据
        education = next(c for c in clusters if "教育部" in c.title)
        self.assertGreater(satellite.score, education.score)

    def test_heuristic_card_never_empty_summary(self):
        now = textutil.now_utc()
        items = [RawItem(title="某热榜词条", source="头条热榜", published=now)]
        clusters = classify.cluster_items(items, self.categories, __import__("logging").getLogger("t"))
        card = classify.heuristic_card(clusters[0])
        self.assertTrue(card.summary)
        self.assertTrue(card.angle)
        self.assertFalse(card.from_ai)

    def test_angle_prefers_topic_keyword(self):
        now = textutil.now_utc()
        items = [RawItem(title="国产大模型开源生态迎来新进展", source="IT之家", published=now,
                         summary="推理成本下降")]
        clusters = classify.cluster_items(items, self.categories, __import__("logging").getLogger("t"))
        angle = classify.angle_for(clusters[0])
        self.assertIn("科技自立", angle)


class TestStore(unittest.TestCase):
    def test_state_roundtrip_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            now = textutil.now_utc()
            old = textutil.iso(now - dt.timedelta(days=40))
            state = {"last_run": textutil.iso(now), "seen": {"fresh": textutil.iso(now), "stale": old}}
            store.save_state(state)
            self.assertIsNotNone(store.last_run())
            pruned = store.prune_seen(store.load_state(), keep_days=30)
            self.assertIn("fresh", pruned["seen"])
            self.assertNotIn("stale", pruned["seen"])

    def test_cache_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            now = textutil.now_utc()
            cache = {"a": {"title": "t", "at": textutil.iso(now)},
                     "b": {"title": "t", "at": textutil.iso(now - dt.timedelta(days=30))}}
            store.save_cache(cache, keep_days=10)
            saved = store.load_cache()
            self.assertIn("a", saved)
            self.assertNotIn("b", saved)

    def test_corrupt_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.latest_path.write_text("{坏掉的 JSON", encoding="utf-8")
            self.assertIsNone(store.load_latest())
            self.assertEqual(store.load_state()["runs"], 0)


class FakeAIClient:
    """替身：把候选原样包装成卡片，用来验证有 AI 时的装配与缓存路径。"""

    instances = 0

    def __init__(self, cfg, logger):
        FakeAIClient.instances += 1
        self.available = True
        self.cfg = {}

    def unavailable_reason(self):
        return ""

    def summarize(self, clusters, window, categories, cache):
        out = []
        for cluster in clusters[:3]:
            card = NewsCard(title="AI:" + cluster.title, summary="AI 概括内容。" + cluster.summary,
                            angle="AI 角度", category=cluster.category, sources=cluster.sources,
                            urls=cluster.urls, published=cluster.published,
                            item_count=cluster.item_count, score=cluster.score + 1, from_ai=True)
            out.append((card, [cluster]))
        return out, True, ""


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = config.load_config("/nonexistent/config.json")
        self.cfg["app"]["data_dir"] = self.tmp.name
        self.cfg["app"]["log_dir"] = self.tmp.name
        self.cfg["ai"]["api_key"] = ""
        self.store = Store(Path(self.tmp.name))
        self.logger = __import__("logging").getLogger("test")
        self.now = textutil.now_utc()

    def tearDown(self):
        self.tmp.cleanup()

    def _items(self):
        return [
            RawItem(title="我国成功发射试验二十八号卫星", url="https://a/1", source="新华网",
                    summary="卫星进入预定轨道，开展空间环境探测。", published=self.now - dt.timedelta(minutes=30)),
            RawItem(title="试验二十八号卫星发射成功", url="https://b/2", source="人民网",
                    summary="发射任务取得圆满成功。", published=self.now - dt.timedelta(minutes=25)),
            RawItem(title="开源大模型推理成本下降", url="https://c/3", source="IT之家",
                    summary="多家机构发布开源模型。", published=self.now - dt.timedelta(hours=2)),
            RawItem(title="野生大熊猫现身村民院坝", url="https://d/4", source="中国新闻网",
                    summary="专家称健康状况良好。", published=self.now - dt.timedelta(hours=3)),
            RawItem(title="去年旧闻不应出现", url="https://e/5", source="人民网",
                    summary="陈旧内容", published=self.now - dt.timedelta(days=10)),
        ]

    def test_window_first_run_uses_default_hours(self):
        start, end = pipeline.resolve_window(self.cfg, None, self.now)
        self.assertAlmostEqual((end - start).total_seconds() / 3600, 24, places=2)

    def test_window_clamped_for_long_gap(self):
        start, end = pipeline.resolve_window(self.cfg, self.now - dt.timedelta(days=30), self.now)
        self.assertAlmostEqual((end - start).total_seconds() / 3600, 96, places=2)

    def test_window_uses_last_run(self):
        last = self.now - dt.timedelta(hours=6)
        start, end = pipeline.resolve_window(self.cfg, last, self.now)
        self.assertEqual(start, last)

    def test_in_window_rules(self):
        start = self.now - dt.timedelta(hours=1)
        keep = RawItem(title="新条目", published=self.now - dt.timedelta(minutes=10))
        self.assertTrue(pipeline._in_window(keep, start, self.now, {}))
        late = RawItem(title="略微早于起点但没见过", published=start - dt.timedelta(hours=3))
        self.assertTrue(pipeline._in_window(late, start, self.now, {}))
        self.assertFalse(pipeline._in_window(late, start, self.now, {late.fingerprint: "x"}))
        too_old = RawItem(title="太老了没见过", published=start - dt.timedelta(hours=20))
        self.assertFalse(pipeline._in_window(too_old, start, self.now, {}))
        future = RawItem(title="未来时间", published=self.now + dt.timedelta(hours=3))
        self.assertFalse(pipeline._in_window(future, start, self.now, {}))
        undated = RawItem(title="没有时间的新条目")
        self.assertTrue(pipeline._in_window(undated, start, self.now, {}))
        self.assertFalse(pipeline._in_window(undated, start, self.now, {undated.fingerprint: "x"}))

    def test_run_once_without_ai_produces_cards(self):
        with mock.patch.object(sources, "fetch_all", return_value=(self._items(), [SourceStatus("新华网", True, 5)])):
            result = pipeline.run_once(self.cfg, self.store, self.logger, use_ai=False)
        self.assertTrue(result.has_content)
        self.assertEqual(result.kept, 4, "超出窗口的旧闻应被过滤")
        self.assertFalse(result.ai_used)
        self.assertTrue(all(not c.from_ai for b in result.blocks for c in b.cards))
        self.assertIsNotNone(self.store.load_latest())

    def test_state_advances_and_second_run_is_empty(self):
        with mock.patch.object(sources, "fetch_all", return_value=(self._items(), [])):
            pipeline.run_once(self.cfg, self.store, self.logger, use_ai=False)
            second = pipeline.run_once(self.cfg, self.store, self.logger, use_ai=False)
        self.assertEqual(second.kept, 0)
        # 关键行为：没有新内容时必须继续显示上一次的结果，而不是把屏幕清空
        self.assertTrue(second.has_content)
        self.assertIn("继续显示上次内容", second.note)

    def test_no_new_items_without_history_is_empty(self):
        with mock.patch.object(sources, "fetch_all", return_value=([], [])):
            result = pipeline.run_once(self.cfg, self.store, self.logger, use_ai=False)
        self.assertFalse(result.has_content)
        self.assertIn("没有新条目", result.note)

    def test_ai_path_and_cache(self):
        with mock.patch.object(sources, "fetch_all", return_value=(self._items(), [])), \
                mock.patch.object(pipeline, "AIClient", FakeAIClient):
            first = pipeline.run_once(self.cfg, self.store, self.logger, use_ai=True)
        self.assertTrue(first.ai_used)
        self.assertGreater(first.ai_cards, 0)
        self.assertTrue(self.store.cache_path.exists())
        self.assertGreater(len(self.store.load_cache()), 0)

        # 第二次运行同一批新闻：应命中缓存，不再调用 AI
        FakeAIClient.instances = 0
        with mock.patch.object(sources, "fetch_all", return_value=(self._items(), [])), \
                mock.patch.object(pipeline, "AIClient", FakeAIClient):
            second = pipeline.run_once(self.cfg, self.store, self.logger, use_ai=True)
        self.assertEqual(FakeAIClient.instances, 0, "全部命中缓存时不应实例化 AI 调用")

    def test_ai_failure_falls_back_to_rules(self):
        class BrokenAI(FakeAIClient):
            def __init__(self, cfg, logger):
                super().__init__(cfg, logger)
                self.available = True

            def summarize(self, clusters, window, categories, cache):
                return [], False, "接口超时"

        with mock.patch.object(sources, "fetch_all", return_value=(self._items(), [])), \
                mock.patch.object(pipeline, "AIClient", BrokenAI):
            result = pipeline.run_once(self.cfg, self.store, self.logger, use_ai=True)
        self.assertTrue(result.has_content, "AI 挂掉时仍必须有卡片")
        self.assertIn("超时", result.ai_error)

    def test_category_limit_respected(self):
        many = [RawItem(title=f"科技新闻第{i}条 芯片突破", url=f"https://x/{i}", source=f"源{i}",
                        summary="半导体", published=self.now - dt.timedelta(minutes=i))
                for i in range(20)]
        with mock.patch.object(sources, "fetch_all", return_value=(many, [])):
            result = pipeline.run_once(self.cfg, self.store, self.logger, use_ai=False)
        tech = next(b for b in result.blocks if b.key == "tech")
        self.assertLessEqual(len(tech.cards), 4)


class TestAIJsonParsing(unittest.TestCase):
    def test_parse_plain_json(self):
        from newscast.ai import parse_ai_json
        data = parse_ai_json('{"cards": [{"ids": [1], "title": "t"}]}')
        self.assertEqual(data["cards"][0]["ids"], [1])

    def test_parse_fenced_json(self):
        from newscast.ai import parse_ai_json
        text = '```json\n{"cards": []}\n```'
        self.assertEqual(parse_ai_json(text), {"cards": []})

    def test_parse_json_with_preamble(self):
        from newscast.ai import parse_ai_json
        text = '好的，这是结果：\n{"cards": [{"ids": [2]}]}\n希望有帮助。'
        self.assertEqual(parse_ai_json(text)["cards"][0]["ids"], [2])

    def test_parse_invalid_raises(self):
        from newscast.ai import parse_ai_json
        with self.assertRaises(ValueError):
            parse_ai_json("完全不是 JSON")


class TestConfig(unittest.TestCase):
    def test_default_config_is_self_consistent(self):
        cfg = config.load_config("/nonexistent/config.json")
        keys = {c["key"] for c in cfg["categories"]}
        self.assertEqual(len(keys), len(cfg["categories"]), "分类 key 不能重复")
        for source in cfg["sources"]:
            self.assertIn(source["hint"], keys | {""}, f"{source['name']} 的 hint 必须是有效分类")
        self.assertTrue(any(s["enabled"] for s in cfg["sources"]), "至少要有一个启用的源")

    def test_user_override_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"app": {"refresh_hour": 7}, "ui": {"alpha": "0.5"}}),
                            encoding="utf-8")
            cfg = config.load_config(path)
            self.assertEqual(cfg["app"]["refresh_hour"], 7)
            self.assertEqual(cfg["app"]["log_level"], "INFO", "未覆盖的键应保留默认值")
            self.assertAlmostEqual(cfg["ui"]["alpha"], 0.5, msg="字符串数字应被纠正")

    def test_sources_list_replaced_not_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"sources": [{"name": "只有这个", "type": "rss",
                                                     "url": "https://x", "enabled": True}]}),
                            encoding="utf-8")
            cfg = config.load_config(path)
            self.assertEqual(len(cfg["sources"]), 1)
            self.assertEqual(len(config.active_sources(cfg)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
