"""B 站收件箱：把站内的回复 / @ / 赞 / 私信念进账本（帧钩子与 bili_inbox 共用一套）。

用的是打桩的接口返回（形状照 2026-11-01 / 11-06 实测的真数据），不打真站。
用例钉四件事——只挑比游标新的、系统通知不算、念过的不再念、念不完的不推进游标。
"""
import importlib.util
import json
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

_PLUGIN_FILE = Path(__file__).resolve().parent.parent / "plugins/bilibili/plugin.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("bili_plugin_inbox_under_test", _PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _msg(sender: int, seqno: int, text: str, *, notify: str = "") -> dict:
    return {
        "sender_uid": sender, "msg_seqno": seqno, "msg_type": 1,
        "content": json.dumps({"content": text}, ensure_ascii=False),
        "notify_code": notify,
    }


def _reply_item(rpid: int, who: str, said: str, title: str, ts: int = 1) -> dict:
    return {
        "id": rpid, "reply_time": ts,
        "user": {"nickname": who},
        "item": {"title": title, "source_content": said, "desc": "被回复的那条"},
    }


class Dummy:
    """_inbox_seen / _inbox_advance 都被替换掉，这里只是个占位（不是真 session）。"""


async def test_only_new_items_are_read_and_system_notes_are_skipped():
    mod = _load_plugin()
    seen = {"reply": 100, "im:7": 50}

    async def fake_seen(db, agent_id):
        return dict(seen)

    async def fake_get(url, params=None, cookie=""):
        if url.endswith("/x/msgfeed/reply"):
            return {"items": [
                _reply_item(100, "旧的", "这条已经念过", "旧视频"),
                _reply_item(120, "书爱", "我也震惊了了。。。", "[重返未来1999]维尔汀：我是0？？！", ts=2),
            ]}
        if url.endswith("/x/msgfeed/at"):
            return {"items": []}
        if url.endswith("/x/msgfeed/like"):
            return {"items": []}
        if "get_sessions" in url:
            return {"session_list": [
                {"talker_id": 7, "unread_count": 2, "session_ts": 999},
                {"talker_id": 8, "unread_count": 0, "session_ts": 1000},
            ]}
        if "fetch_session_msgs" in url:
            assert params["talker_id"] == 7
            return {"messages": [
                _msg(7, 40, "这条念过了"),
                _msg(7, 60, "你好呀"),
                _msg(7, 61, "登录操作通知", notify="3_13"),
            ]}
        if "user/cards" in url:
            return [{"mid": 7, "name": "香草味的纳西妲喵"}]
        return {}

    mod._inbox_seen = fake_seen
    mod._get = fake_get

    counts, lines, cursors, _pending, rows = await mod._inbox_take(Dummy(), 31, "SESSDATA=x")

    assert counts == {"reply": 1, "dm": 1}, counts
    assert any("回复｜书爱｜" in l and "我也震惊了了" in l for l in lines), lines
    assert not any("这条已经念过" in l for l in lines), "游标之前的旧条目又被念了一遍"
    assert any("私信｜香草味的纳西妲喵｜" in l and "你好呀" in l for l in lines), lines
    assert not any("登录操作通知" in l for l in lines), "系统通知不该当成私信念"
    assert not any("这条念过了" in l for l in lines), "私信游标没生效"
    # 游标只前进到本次真念到的那些
    assert cursors == {"reply": 120, "im:7": 61}, cursors


async def test_nothing_new_means_no_lines():
    mod = _load_plugin()

    async def fake_seen(db, agent_id):
        return {"reply": 999}

    async def fake_get(url, params=None, cookie=""):
        if url.endswith("/x/msgfeed/reply"):
            return {"items": [_reply_item(120, "书爱", "早就念过", "视频")]}
        if url.endswith("/x/msgfeed/at") or url.endswith("/x/msgfeed/like"):
            return {"items": []}
        if "get_sessions" in url:
            return {"session_list": []}
        return {}

    mod._inbox_seen = fake_seen
    mod._get = fake_get
    counts, lines, cursors, _pending, rows = await mod._inbox_take(Dummy(), 31, "SESSDATA=x")
    assert (counts, lines, cursors) == ({}, [], {})
    assert rows, "看见的条目（哪怕已经念过）也该进流水，区间查询要靠它"


async def test_unread_ones_beyond_the_cap_keep_their_cursor():
    """一次记不下的就不推进游标：宁可下次再念一遍，也不把没写进账本的算成已读。"""
    mod = _load_plugin()

    async def fake_seen(db, agent_id):
        return {}

    async def fake_get(url, params=None, cookie=""):
        if url.endswith("/x/msgfeed/reply"):
            return {"items": [
                _reply_item(1000 + i, f"路人{i}", f"第{i}条", "视频", ts=i)
                for i in range(15)
            ]}
        if "get_sessions" in url:
            return {"session_list": []}
        return {}

    mod._inbox_seen = fake_seen
    mod._get = fake_get
    counts, lines, cursors, pending, rows = await mod._inbox_take(Dummy(), 31, "SESSDATA=x")

    assert counts == {"reply": mod._INBOX_MAX_LINES}, counts   # 只报真写进账本的
    assert pending == 15 - mod._INBOX_MAX_LINES, pending
    assert len(lines) == mod._INBOX_MAX_LINES
    # 停在真写进账本的最后一条，后面的下次还能再念到
    assert cursors == {"reply": 1000 + mod._INBOX_MAX_LINES - 1}, cursors

async def test_likes_come_from_both_latest_and_total():
    """赞的结构和回复不同：latest 会被「打开手机」清空，total 是聚合的历史。"""
    mod = _load_plugin()

    async def fake_seen(db, agent_id):
        return {"like": 100}

    async def fake_get(url, params=None, cookie=""):
        if url.endswith("/x/msgfeed/reply") or url.endswith("/x/msgfeed/at"):
            return {"items": []}
        if url.endswith("/x/msgfeed/like"):
            return {
                "latest": {"items": [
                    {
                        "id": 9, "like_time": 300, "counts": 3,
                        "users": [{"nickname": "白龙骑士加拉哈德"}, {"nickname": "书爱CFR"}],
                        "item": {"uri": "https://www.bilibili.com/video/BV1z54G6vEMR", "title": "🎣 人来！"},
                    },
                ]},
                "total": {"items": [
                    {
                        "id": 9, "like_time": 300, "counts": 3,
                        "users": [{"nickname": "白龙骑士加拉哈德"}, {"nickname": "书爱CFR"}],
                        "item": {"uri": "https://www.bilibili.com/video/BV1z54G6vEMR", "title": "🎣 人来！"},
                    },
                    {   # 已经念过的旧赞
                        "id": 8, "like_time": 50, "counts": 1,
                        "users": [{"nickname": "旧人"}],
                        "item": {"uri": "https://www.bilibili.com/video/BV1ba4g6LEak", "title": "旧"},
                    },
                    {   # 同一目标再被赞：id 相同也要算新（按时间，不按 id）
                        "id": 9, "like_time": 200, "counts": 2,
                        "users": [{"nickname": "书爱CFR"}],
                        "item": {"uri": "https://www.bilibili.com/video/BV1z54G6vEMR", "title": "🎣 人来！"},
                    },
                ]},
            }
        if "get_sessions" in url:
            return {"session_list": []}
        return {}

    mod._inbox_seen = fake_seen
    mod._get = fake_get
    counts, lines, cursors, pending, _rows = await mod._inbox_take(Dummy(), 31, "SESSDATA=x")

    assert counts == {"like": 2}, counts
    assert not any("旧人" in l for l in lines), "念过的旧赞又被念了一遍"
    assert any("赞｜白龙骑士加拉哈德 等 2 人｜BV1z54G6vEMR" in l for l in lines), lines
    assert cursors == {"like": 300}, cursors
    assert pending == 0


async def test_likes_are_cut_by_time_not_id():
    """同一目标的 id 不变、时间变新：按 id 会漏，按时间才看得见。"""
    mod = _load_plugin()

    async def fake_seen(db, agent_id):
        return {"like": 250}

    async def fake_get(url, params=None, cookie=""):
        if url.endswith("/x/msgfeed/reply") or url.endswith("/x/msgfeed/at"):
            return {"items": []}
        if url.endswith("/x/msgfeed/like"):
            return {"latest": {"items": []}, "total": {"items": [
                {
                    "id": 9, "like_time": 300, "counts": 4,
                    "users": [{"nickname": "新的人"}],
                    "item": {"uri": "https://www.bilibili.com/video/BV1z54G6vEMR", "title": "🎣"},
                },
            ]}}
        if "get_sessions" in url:
            return {"session_list": []}
        return {}

    mod._inbox_seen = fake_seen
    mod._get = fake_get
    counts, lines, cursors, pending, _rows = await mod._inbox_take(Dummy(), 31, "SESSDATA=x")
    assert counts == {"like": 1} and cursors == {"like": 300}, (counts, cursors)


def _feed_row(kind: str, ts: int, who: str, target: str, *, said: str = "",
              root: str = "", counts: int = 0, title: str = "") -> dict:
    """一条流水行的形状（与 _reply_row / _like_row / _dm_row 产出的键一致）。"""
    return {"kind": kind, "ts": ts, "who": who, "target": target, "title": title,
            "said": said, "root": root, "counts": counts}


async def test_scan_archives_old_items_too():
    """区间查询要有历史：一次扫描把窗口里**所有**条目都存下来，不只还没念的。"""
    mod = _load_plugin()

    async def fake_seen(db, agent_id):
        return {"reply": 120}

    async def fake_get(url, params=None, cookie=""):
        if url.endswith("/x/msgfeed/reply"):
            return {"items": [
                _reply_item(100, "旧的", "已经念过", "视频一", ts=1),
                _reply_item(120, "书爱", "刚说的", "视频二", ts=2),
                _reply_item(130, "路人", "还没念", "视频二", ts=3),
            ]}
        if url.endswith("/x/msgfeed/at") or url.endswith("/x/msgfeed/like"):
            return {"items": []}
        if "get_sessions" in url:
            return {"session_list": []}
        return {}

    mod._inbox_seen = fake_seen
    mod._get = fake_get
    counts, lines, cursors, _pending, rows = await mod._inbox_take(Dummy(), 31, "SESSDATA=x")

    assert [r["key"] for r in rows] == ["100", "120", "130"], rows
    assert counts == {"reply": 1}, counts            # 念的只有游标之后的（130）
    assert len(lines) == 1
    assert rows[0]["ts"] == 1 and rows[0]["said"] == "已经念过"


def test_like_line_carries_the_running_count():
    """赞的数要看得见：新赞是一条，"这条一共多少赞"是另一条数（46 就藏在这里）。"""
    mod = _load_plugin()
    line = mod._row_line(
        _feed_row("like", 300, "白龙骑士加拉哈德 等 2 人", "BV1z54G6vEMR", counts=54))
    assert line == "赞｜白龙骑士加拉哈德 等 2 人｜BV1z54G6vEMR｜共 54 赞", line


def test_rows_render_like_the_live_lines():
    mod = _load_plugin()
    reply = _feed_row("reply", 1, "书爱", "BV1ba4g6LEak", said="我也震惊了",
                      root="319113240673")
    assert mod._row_line(reply) == "回复｜书爱｜BV1ba4g6LEak#319113240673｜我也震惊了"
    dm = _feed_row("dm", 1, "香草味", "香草味", said="你好呀")
    assert mod._row_line(dm) == "私信｜香草味｜你好呀", "私信不该把对方名字写两遍"


def test_report_clusters_by_post_then_by_day():
    mod = _load_plugin()
    t1 = mod._parse_when("2026-11-05 09:00")
    t2 = mod._parse_when("2026-11-05 10:00")
    t3 = mod._parse_when("2026-11-06 10:00")
    rows = [
        _feed_row("reply", t1, "甲", "BV1", said="一句"),
        _feed_row("like", t2, "乙 等 2 人", "BV1", counts=3),
        _feed_row("reply", t3, "丙", "BV2", said="另一句"),
        _feed_row("dm", t2, "丁", "丁", said="私信一句"),
    ]

    heads = [l for l in mod._feed_report(rows, "帖子").splitlines() if l.startswith("· ")]
    assert heads == ["· BV2（回复 1）", "· BV1（回复 1 · 赞 1）", "· 私信 · 丁（私信 1）"], heads
    assert "  · 11-05 09:00 回复｜甲｜BV1｜一句" in mod._feed_report(rows, "帖子")

    # 被赞的是评论时带上那条评论的 rpid：同一个视频下被赞的两个评论才分得开
    likes = mod._like_row({"id": 9, "like_time": t2, "counts": 2, "users": [{"nickname": "戊"}],
                           "item": {"type": "reply", "item_id": 319030315761,
                                    "uri": "https://www.bilibili.com/video/BV1AaxC6hEct",
                                    "title": "某条评论"}})
    assert likes["root"] == "319030315761", likes
    assert mod._row_line(likes) == "赞｜戊｜BV1AaxC6hEct#319030315761｜共 2 赞", likes

    days = [l for l in mod._feed_report(rows, "天").splitlines() if l.startswith("· ")]
    assert days == ["· 2026-11-06（回复 1）", "· 2026-11-05（回复 1 · 赞 1 · 私信 1）"], days

    people = [l for l in mod._feed_report(rows, "人").splitlines() if l.startswith("· ")]
    assert len(people) == 4, people


def test_time_window_parsing():
    mod = _load_plugin()
    day = mod._parse_when("2026-11-05")
    assert mod._parse_when("2026-11-05", day_end=True) == day + 86400
    assert mod._parse_when("2026-11-05 12:30") == day + 12 * 3600 + 30 * 60
    assert mod._parse_when("11-05 12:30") == day + 12 * 3600 + 30 * 60, "只给月日按今年算"
    assert mod._parse_when("看不懂", 7) == 7

    assert mod._range_of({}) == (0, 0, ""), "不带区间就是同步，不是查询"

    since, until, by = mod._range_of({"days": 3, "by": "天"})
    assert abs((int(time.time()) - since) - 3 * 86400) < 5, (since, until)
    assert until >= since and by == "天"

    since, until, _by = mod._range_of({"since": "2026-11-01", "until": "2026-11-05"})
    assert until - since == 5 * 86400, "到 11-05 要把 11-05 一整天算进去"

