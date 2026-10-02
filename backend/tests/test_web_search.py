"""web_search 的规划、相关性闸门、多引擎并发与排序契约（纯函数 + 假后端，不发真实请求）。"""
import pytest

from app.tools.file_operations.search.authority import authority, domain_of
from app.tools.file_operations.search.backends import BACKENDS
from app.tools.file_operations.search.plan import plan_queries, quoted_phrases
from app.tools.file_operations.search.rank import (
    dedupe, fuse_engines, has_gate_signal, rank, relevant, score,
)

pytestmark = pytest.mark.anyio


def test_plan_splits_aliases_and_glued_names_out_of_a_sentence():
    """模型只给原话时靠机械改写兜底：去括号、拆粘连词、单拉丁词元垫底"""
    got = plan_queries("Copree（AIsChat）类似平台的发展和发布")
    assert got[0] == "Copree AIsChat 类似平台的发展和发布"
    assert "Copree AIsChat" in got
    assert "AIsChat" in got
    assert "CopreeAIsChat 发布" in plan_queries("CopreeAIsChat 发布") or         "Copree AIs Chat 发布" in plan_queries("CopreeAIsChat 发布")


def test_plan_keeps_quotes_so_the_engine_and_gate_can_use_them():
    """引号是「整串必须出现」的意思：抹掉等于把调用方的精度要求丢掉"""
    got = plan_queries('"赛博生命" AI 群聊')
    assert got and '"赛博生命"' in got[0], got
    assert quoted_phrases('"赛博生命" 和 “数字生命”') == ["赛博生命", "数字生命"]


async def _with_fake_fetch(fetcher, coro_factory):
    """编排层测试不碰网络：把取数函数换掉，其余（并发调度/闸门/去重/排序/提示）走真实代码。"""
    from app.tools.file_operations.search import orchestrator

    original = orchestrator._fetch_one
    orchestrator._fetch_one = fetcher
    try:
        return await coro_factory()
    finally:
        orchestrator._fetch_one = original


async def test_orchestrator_fans_out_to_every_backend_and_fuses_consensus():
    """一轮里所有后端都被并发问过（不是「第一家出结果就停」），同一 URL 的共识记进 engines"""
    from app.tools.file_operations.search import orchestrator

    asked: list[str] = []

    async def fake_fetch(client, backend, query, count, exclude, budget, require_adjacent=False):
        asked.append(backend.name)
        items = [
            {"title": "狼蛛电竞产品在线驱动", "url": "https://aula.example/x", "snippet": "驱动下载"},
            {"title": "Copree 官方发布", "url": "https://copree.ai/", "snippet": "Copree AIsChat 是一个 AI 群聊平台"},
        ]
        kept = [dict(i, provider=backend.name) for i in items
                if relevant(query, i["title"], i["snippet"])]
        return kept, None, {"provider": backend.name, "query": query, "parsed": len(items), "kept": len(kept)}

    out = await _with_fake_fetch(fake_fetch, lambda: orchestrator.search(["Copree AIsChat"], limit=5))
    assert sorted(set(asked)) == sorted(b.name for b in BACKENDS), asked   # 并发铺开到全部后端
    assert [r["url"] for r in out["results"]] == ["https://copree.ai/"], out
    assert out["results"][0]["engines"] == [b.name for b in BACKENDS], out["results"][0]
    assert out["provider_used"] == sorted(b.name for b in BACKENDS), out


async def test_orchestrator_drops_engine_filler_and_tells_the_truth_in_hint():
    """搜索引擎对冷门词返回无关填充——判为 0 结果，并在提示里写清是谁说的、为什么"""
    from app.tools.file_operations.search import orchestrator

    async def fake_fetch(client, backend, query, count, exclude, budget, require_adjacent=False):
        kept = [{"title": "Copree 官方发布", "url": "https://copree.ai/",
                 "snippet": "Copree 是一个 AI 群聊平台", "provider": backend.name}]
        return kept, None, {"provider": backend.name, "query": query, "parsed": 10, "kept": 1}

    async def junk_fetch(client, backend, query, count, exclude, budget, require_adjacent=False):
        return [], None, {"provider": backend.name, "query": query, "parsed": 0, "kept": 0}

    out_excluded = await _with_fake_fetch(
        fake_fetch, lambda: orchestrator.search(["Copree AIsChat"], exclude=["群聊"], limit=5))
    assert out_excluded["count"] == 0 and "hint" in out_excluded, out_excluded
    # 提示语会被模型转述给用户，所以要写事实：哪几家引擎、取回几条、是引擎没给还是被拦下
    assert "取回" in out_excluded["hint"] and "bing" in out_excluded["hint"], out_excluded["hint"]
    assert "没有一条命中检索式里的实体词" in out_excluded["hint"], out_excluded["hint"]

    empty = await _with_fake_fetch(junk_fetch, lambda: orchestrator.search(["Copree AIsChat"], limit=5))
    assert empty["success"] and empty["count"] == 0 and "hint" in empty, empty
    assert "引擎这一轮没给东西" in empty["hint"], empty["hint"]
    # 每一轮的实况要留在结果里：搜不到时能分清「解析为 0」和「解析到但不相关」
    assert empty["attempts"] and empty["attempts"][0]["parsed"] == 0, empty["attempts"]


async def test_orchestrator_names_generic_queries_instead_of_blaming_the_web():
    """全是泛词的检索式判不动——提示里要说「是你这条检索式没有实体词」"""
    from app.tools.file_operations.search import orchestrator

    async def junk_fetch(client, backend, query, count, exclude, budget, require_adjacent=False):
        return [], None, {"provider": backend.name, "query": query, "parsed": 8, "kept": 0}

    out = await _with_fake_fetch(junk_fetch, lambda: orchestrator.search(["项目 开源 平台"], limit=5))
    assert out["count"] == 0 and "没有可判定的实体词" in out["hint"], out.get("hint")


async def test_orchestrator_switches_queries_when_the_gate_rejected_everything():
    """引擎给了但全被闸门拦下 = 检索式不对：换机械改写候选，而不是换个引擎重跑同一句"""
    from app.tools.file_operations.search import orchestrator

    seen: list[tuple[str, bool]] = []

    async def fake_fetch(client, backend, query, count, exclude, budget, require_adjacent=False):
        seen.append((query, require_adjacent))
        if not require_adjacent:                     # 调用方给的那条：引擎有结果、闸门全否
            return [], None, {"provider": backend.name, "query": query, "parsed": 10, "kept": 0}
        return ([{"title": "Copree 官方", "url": "https://copree.ai/", "provider": backend.name,
                  "snippet": "Copree 平台"}], None,
                {"provider": backend.name, "query": query, "parsed": 10, "kept": 1})

    out = await _with_fake_fetch(
        fake_fetch, lambda: orchestrator.search(["Copree AIsChat"], fallback_raw="Copree"))
    assert out["count"] == 1, out
    assert all(adj is False for _q, adj in seen[:4]), seen          # 第一轮用调用方给的检索式
    assert any(adj is True for _q, adj in seen[4:]), seen           # 第二轮换成机械改写候选
    assert all(q != "Copree AIsChat" for q, _adj in seen[4:]), seen
    assert any(a["query"] != "Copree AIsChat" and a["kept"] for a in out["attempts"]), out["attempts"]


async def test_orchestrator_retries_the_same_queries_when_the_engines_gave_nothing():
    """引擎自己空手（解析 0 / 超时）= 换后端重试同一句；判据含糊的话回退只是在烧请求"""
    from app.tools.file_operations.search import orchestrator

    seen: list[str] = []

    async def junk_fetch(client, backend, query, count, exclude, budget, require_adjacent=False):
        seen.append(query)
        return [], None, {"provider": backend.name, "query": query, "parsed": 0, "kept": 0}

    await _with_fake_fetch(junk_fetch, lambda: orchestrator.search(["Copree AIsChat"]))
    assert len(seen) == 2 * len(BACKENDS), seen
    assert set(seen) == {"Copree AIsChat"}, seen


def test_relevance_gate_and_authority():
    """相关性靠字面重合（拉丁词元 / 中文词块）；权威只决定相关结果之间的先后"""
    assert relevant("Copree AIsChat", "狼蛛电竞产品在线驱动", "AULA 驱动下载") is False
    assert relevant("清华大学 官网", "清华大学", "学校概况") is True
    assert relevant("Copree AIsChat", "Copree 官方文档", "AIsChat 群聊平台") is True
    assert authority(domain_of("https://copree.ai/docs"), ["Copree"]) == 1.0
    assert authority("github.com", ["Copree"]) == 0.85
    assert authority("hao123.com", ["Copree"]) == 0.1


def test_relevance_requires_every_entity_term():
    """只命中一个词不算相关：AIs 会命中 AIS 船舶系统，github 会命中任意仓库页（实测踩过）"""
    assert relevant("AIs Chat", "船讯网-AIS", "AIS 信号收发能力，船舶自动识别系统") is False
    assert relevant("AIsChat github", "GitHub - foo/bar: 三毛机场", "本仓库用于整理机场节点信息") is False
    assert relevant("AIsChat", "AIsChat 官方文档", "AIsChat 是一个 AI 群聊平台") is True
    # 机械拆词派生的候选要相邻命中：AIs Chat 钓到过 watchaichat 这种同名站
    assert relevant("AIs Chat", "Watch AI Chat - Two AIs Talking 24/7 Live",
                    "Chat with AIs", require_adjacent=True) is False
    assert relevant("AIs Chat", "AIs Chat 官方文档", "AIs Chat 是一个 AI 群聊平台",
                    require_adjacent=True) is True


def test_gate_blocks_generic_cjk_and_keeps_the_real_hits():
    """中文侧按非泛词汉字块判：命中一个泛词二元组不算（真机把阿拉伯数字工具当成了数字生命）"""
    assert relevant("数字生命 AI 群聊 项目 开源", "阿拉伯数字 0、1、2 书写规范", "数字的写法") is False
    assert relevant("数字生命 AI 群聊 项目 开源", "数字转大写 - 在线工具", "把数字转成中文大写") is False
    assert relevant("数字生命 AI 群聊 项目 开源", "数字生命服务器开源项目常见问题", "数字生命 项目") is True
    # 「赛博生命」钓到赛博加速器 / 赛博朋克
    assert relevant("赛博生命 AI 群聊 项目", "客户端下载_赛博加速器", "赛博加速器官方下载") is False
    # 模型顺手音译的外文名不再一票否决中文结果（真机就是这条把 10 条全丢了）
    assert relevant("赛博生命 CyberLife 数字生命 AI 社区 项目",
                    "赛博生命：一个 AI 群聊项目", "赛博生命体在这里生活") is True
    # 中文侧全是泛词时，实体词照旧管用
    assert relevant("AstrBot 开源 项目", "GitHub - AstrBotDevs/AstrBot", "开源机器人平台") is True
    # 引号里的中文短语要整串出现，且它本身就是判据
    assert relevant('"群聊 平台" AI', "AI 群聊 平台 开源", "多智能体") is True
    assert relevant('"群聊 平台" AI', "AI 群聊，平台级工具", "多智能体") is False
    # 一块都判不动（全是泛词）→ 全否，原因由 hint 说明
    assert relevant("项目 开源 平台", "开源项目导航平台", "收录各类开源项目") is False
    assert has_gate_signal("项目 开源 平台") is False
    assert has_gate_signal("数字生命 AI 群聊") is True


def test_fuse_engines_marks_consensus_and_scores_it():
    """同一 URL 被两家引擎给出 = 共识：并成一条、记下 engines、排序时加分"""
    a = {"title": "Copree 官方", "url": "https://copree.ai/", "snippet": "Copree 平台", "provider": "bing"}
    b = {"title": "Copree 官方", "url": "https://copree.ai/", "snippet": "Copree 是一个 AI 群聊平台",
         "provider": "so360"}
    fused = fuse_engines([a, b])
    assert len(fused) == 1 and fused[0]["engines"] == ["bing", "so360"], fused
    assert "AI 群聊平台" in fused[0]["snippet"], fused[0]      # 摘要取更全的那份
    assert score(dict(a, engines=["bing", "so360"]), ["Copree"]) > score(dict(a, engines=["bing"]), ["Copree"])


def test_rank_puts_the_entity_domain_first_and_caps_each_domain():
    items = [
        {"title": "Copree 报道一", "url": "https://news.example.com/a", "snippet": "Copree 平台"},
        {"title": "Copree 官方", "url": "https://copree.ai/", "snippet": "Copree 平台"},
        {"title": "Copree 报道二", "url": "https://news.example.com/b", "snippet": "Copree 平台"},
        {"title": "Copree 报道三", "url": "https://news.example.com/c", "snippet": "Copree 平台"},
        {"title": "Copree 报道四", "url": "https://news.example.com/d", "snippet": "Copree 平台"},
    ]
    got = rank(items, entity_terms=["Copree"], per_domain=3, limit=8)
    assert got[0]["url"] == "https://copree.ai/", got
    # 只给事实性的来源权重，不下「这是不是官网」的结论——判断权归模型
    assert got[0]["authority"] == 1.0 and got[1]["authority"] == 0.5, got
    assert sum(1 for i in got if "news.example.com" in i["url"]) == 3, got


def test_dedupe_collapses_reposts_by_content_fingerprint():
    """同一篇稿子被转载：URL 不同、标题带尾巴，靠标题/指纹判重"""
    a = {"title": "Copree 发布 AI 群聊平台", "url": "https://a.example/x",
         "snippet": "Copree 今天发布了一个 AI 群聊平台，支持多智能体协作与群视界。"}
    b = {"title": "Copree 发布 AI 群聊平台（转载）", "url": "https://b.example/y",
         "snippet": "Copree 今天发布了一个 AI 群聊平台，支持多智能体协作与群视界。"}
    assert len(dedupe([a, b])) == 1, dedupe([a, b])
