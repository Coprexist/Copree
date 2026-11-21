"""B 站私信工具：列会话、读最近几条、真发一条。

全部打桩：不打真站，更不往真人发消息——私信发出去收不回，用例只钉参数形状、
方向判定，以及"发之前"的几道闸（先过创建者开关、不给自己发、字数上限）。
"""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

_PLUGIN_FILE = Path(__file__).resolve().parent.parent / "plugins/bilibili/plugin.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("bili_plugin_dm_under_test", _PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _msg(sender: int, text: str, *, ts: int = 0, seqno: int = 0) -> dict:
    return {
        "sender_uid": sender, "msg_seqno": seqno, "timestamp": ts,
        "content": json.dumps({"content": text}, ensure_ascii=False),
    }


def _last_msg(text: str, *, notify: str = "", sender: int = 7) -> dict:
    return {"sender_uid": sender, "notify_code": notify,
            "content": json.dumps({"content": text}, ensure_ascii=False)}


async def test_list_orders_by_latest_skips_system_and_reads_last_line():
    mod = _load_plugin()
    fetched: list[int] = []

    async def fake_sessions(cookie):
        # 真数据里 session_ts 是**微秒**，最后一条就挂在会话的 last_msg 上
        return [
            {"talker_id": 7, "unread_count": 2, "session_ts": 1792593024278585,
             "last_msg": _last_msg("来自7的最后一句", sender=7)},
            {"talker_id": 8, "unread_count": 0, "session_ts": 1792680760291511,
             "last_msg": _last_msg("来自8的最后一句", sender=8)},
            {"talker_id": 9, "unread_count": 0, "session_ts": 1793000000000000,
             "last_msg": _last_msg("你的账号在新设备登录成功", notify="3_13")},
        ]

    async def fake_msgs(talker, cookie, size=5):
        fetched.append(talker)
        return [_msg(talker, f"兜底来自{talker}", ts=1000)]

    async def fake_names(uids, cookie):
        return {7: "香草味的纳西妲喵", 8: "书爱"}

    mod._dm_sessions = fake_sessions
    mod._dm_msgs = fake_msgs
    mod._dm_names = fake_names

    out = await mod.BiliDm()._list("SESSDATA=x", 5, {"bili_mid": "7"})

    assert out["ok"] and out["count"] == 2, out
    assert [i["uid"] for i in out["items"]] == [8, 7], "会话没按最近说话排序"
    assert [i["uid"] for i in out["items"]] == [8, 7] and 9 not in [i["uid"] for i in out["items"]], \
        "系统通知不该当成一个会话"
    assert out["items"][0]["昵称"] == "书爱"
    assert out["items"][0]["最后一条"] == {"谁": "对方", "text": "来自8的最后一句"}
    assert out["items"][1]["最后一条"]["谁"] == "我", "自己发的最后一句被读成了对方说的"
    assert out["items"][0]["时间"], "微秒时间戳没归一成秒"
    assert out["items"][1]["未读"] == 2
    assert fetched == [], "会话里已经带了最后一条，不该再逐个会话拉消息"


async def test_read_marks_direction_and_skips_empty_bodies():
    mod = _load_plugin()

    async def fake_msgs(talker, cookie, size=5):
        return [_msg(7, "你还在吗", ts=10), _msg(120, "在", ts=20),
                {"sender_uid": 7, "content": "{}"}]

    async def fake_names(uids, cookie):
        return {7: "香草味的纳西妲喵"}

    mod._dm_msgs = fake_msgs
    mod._dm_names = fake_names

    out = await mod.BiliDm()._read(7, "SESSDATA=x", 5, {"bili_mid": "120"})

    assert out["ok"] and out["昵称"] == "香草味的纳西妲喵"
    assert [(m["方向"], m["text"]) for m in out["messages"]] == [("对方", "你还在吗"), ("我", "在")]


async def test_who_resolves_to_existing_session_first():
    mod = _load_plugin()
    searched: list[str] = []

    async def fake_sessions(cookie):
        return [{"talker_id": 7, "session_ts": 1}]

    async def fake_names(uids, cookie):
        return {7: "香草味的纳西妲喵"}

    async def fake_search(kind, keyword, cookie="", order=""):
        searched.append(keyword)
        return []

    mod._dm_sessions = fake_sessions
    mod._dm_names = fake_names
    mod._search_rows = fake_search

    assert await mod._dm_uid_of("7", "x") == 7
    assert await mod._dm_uid_of("香草味的纳西妲喵", "x") == 7
    assert await mod._dm_uid_of("纳西妲", "x") == 7, "会话里的昵称应该能按片段认出来"
    with pytest.raises(mod.BiliError):
        await mod._dm_uid_of("查无此人", "x")
    assert searched == ["查无此人"], "会话里找不到才去搜人"


async def test_send_passes_gates_then_posts_once():
    mod = _load_plugin()
    sent: list[tuple] = []

    async def fake_send(uid, text, cookie, *, csrf, sender_uid):
        sent.append((uid, text, csrf, sender_uid))
        return {}

    async def fake_names(uids, cookie):
        return {9: "书爱"}

    mod._dm_send = fake_send
    mod._dm_names = fake_names
    mod._csrf_of = lambda cfg: "CSRF"

    tool = mod.BiliDm()
    cfg = {"bili_mid": "120", "allow_interact": True}

    out = await tool._send(120, {"text": "在"}, cfg, "SESSDATA=x")
    assert not out["ok"] and "自己" in out["error"], "不能给自己发私信"

    out = await tool._send(9, {}, cfg, "SESSDATA=x")
    assert not out["ok"] and "text" in out["error"]

    out = await tool._send(9, {"text": "x" * (mod._DM_CHARS + 1)}, cfg, "SESSDATA=x")
    assert not out["ok"] and "字" in out["error"]
    assert sent == [], "被拦下的请求不该发到接口"

    with pytest.raises(mod.BiliError):
        await tool._send(9, {"text": "在吗"}, {"bili_mid": "120"}, "SESSDATA=x")
    assert sent == [], "创建者没开互动时不该发出去"

    out = await tool._send(9, {"text": "在吗"}, cfg, "SESSDATA=x")
    assert out["ok"] and out["sent"] and out["昵称"] == "书爱"
    assert sent == [(9, "在吗", "CSRF", "120")]


def test_frame_declares_dm_tool():
    manifest = json.loads((Path(_PLUGIN_FILE).parent / "plugin.json").read_text(encoding="utf-8"))
    tools = manifest["frames"]["bilibili_browse"]["tools"]
    assert "bili_dm" in tools
    # 帧里一轮能跑 50 次调用，长起来得让它自己提前压：白名单裁工具，缺了它就调不动
    assert "compress_context" in tools
    mod = _load_plugin()
    assert mod.BiliDm.name == "bili_dm"
