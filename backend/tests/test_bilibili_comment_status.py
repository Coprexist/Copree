"""B 站评论追认：定位、归属、通知合并。

2026-11-21 线上：9 条楼中楼回复在 pending 里空转到 tries=12 也没判出"已公开"，
因为查状态翻的是**视频的顶层评论列表**，而回复根本不在那张表上。用例钉五件事：
按 rpid 反查线程（顶层一次请求、楼中楼跳最后一页）、删别人的评论先说清是谁的、
追认成功合并成一条通知、失败逐条报。
"""
import importlib.util
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

_PLUGIN_FILE = Path(__file__).resolve().parent.parent / "plugins/bilibili/plugin.py"

ROOT = 319113399761      # 整栋楼的根评论
SUB = 320925890321       # 这栋楼里刚发的一条楼中楼（2026-11-21 实测值）
BVID = "BV1z54G6vEMR"
AID = 117552902577110
ME = "1766309357"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("bili_plugin_status_under_test", _PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_sub_reply_is_found_on_the_last_page():
    """楼中楼按时间正序排：第一页没有就直接跳最后一页，不从中间一页页翻。"""
    mod = _load_plugin()
    calls: list = []

    async def fake_get(url, params=None, cookie=""):
        p = params or {}
        calls.append(dict(p))
        root = {"rpid": ROOT, "member": {"mid": ME}}
        if p.get("root") == ROOT and p.get("pn") == 2:
            return {"root": root, "page": {"count": 21},
                    "replies": [{"rpid": SUB, "state": 0, "member": {"mid": ME}}]}
        return {"root": root, "page": {"count": 21}, "replies": []}

    mod._get = fake_get
    found = await mod._locate_comment(AID, SUB, "SESSDATA=x")
    assert found["thread"] == ROOT, "楼中楼要能反查到它所属的线程根"
    assert found["row"]["rpid"] == SUB
    assert found["row"]["state"] == 0
    assert [c.get("pn") for c in calls] == [1, 2], f"应只打两次：探测 + 最后一页，实际 {calls}"


async def test_top_level_comment_needs_one_request():
    """顶层评论自己就是线程根：一次请求拿到对象，不用再翻页。"""
    mod = _load_plugin()
    calls: list = []

    async def fake_get(url, params=None, cookie=""):
        calls.append(dict(params or {}))
        return {"root": {"rpid": ROOT, "state": 0, "member": {"mid": ME}},
                "page": {"count": 3}, "replies": []}

    mod._get = fake_get
    found = await mod._locate_comment(AID, ROOT, "SESSDATA=x")
    assert found["thread"] == ROOT
    assert found["row"]["rpid"] == ROOT
    assert len(calls) == 1


async def test_deleting_someone_elses_comment_says_whose_it_is():
    """删别人的评论：直接说清是谁的，别让模型对着一句 -403 猜。"""
    mod = _load_plugin()
    posted: list = []

    async def fake_get(url, params=None, cookie=""):
        return {"root": {"rpid": SUB, "member": {"mid": "409293335", "uname": "江西华龙兄弟"}},
                "page": {"count": 0}, "replies": []}

    async def fake_ctx(agent_id):
        return {"bili_jct": "jct", "nickname": "测试号", "bili_mid": ME}, "SESSDATA=x"

    async def fake_post(path, data, cookie="", *, referer=""):
        posted.append(path)
        return {}

    mod._get = fake_get
    mod._post = fake_post
    mod._write_ctx = fake_ctx
    async def fake_aid_of(bvid, cookie=""):
        return AID

    mod._aid_of = fake_aid_of
    mod._instance_of = lambda agent_id: "agent-49"

    out = await mod.BiliComment().run(49, {"action": "delete", "bvid": BVID, "rpid": SUB})
    assert out["ok"] is False
    assert "江西华龙兄弟" in out["error"], "要告诉它这条是谁的"
    assert "403" in out["error"], "顺带解释它为什么会撞上 403"
    assert posted == [], "不是自己的就别真去发删除请求"


def _rows(n: int, start_id: int = 1) -> list[dict]:
    now = int(time.time())
    return [{
        "id": start_id + i, "agent_id": 49, "group_id": None, "session_id": "1_120",
        "bvid": BVID, "aid": AID, "rpid": SUB + i, "message": f"第 {i} 条",
        "created_at": now - 600, "tries": 0,
    } for i in range(n)]


def _wire_pending(mod, rows: list[dict], statuses: dict):
    notices: list[str] = []
    dropped: list[int] = []

    async def fake_open(instance):
        return rows

    async def fake_status(aid, rpid, cookie, **kw):
        return statuses[rpid]

    async def fake_notify(agent_id, group_id, session_id, text, *, ref=""):
        notices.append(text)
        return True

    async def fake_drop(row_id):
        dropped.append(row_id)

    async def fake_touch(row_id, tries):
        return None

    async def fake_cfg(instance):
        return {"bili_jct": "jct"}

    async def fake_trail(instance, action, detail):
        return None

    async def fake_delay(seconds):
        return None

    mod._pending_open = fake_open
    mod._comment_status = fake_status
    mod._notify = fake_notify
    mod._pending_drop = fake_drop
    mod._pending_touch = fake_touch
    mod._instance_config = fake_cfg
    mod._cookie_of = lambda cfg: "SESSDATA=x"
    mod._trail_record = fake_trail
    import asyncio as _asyncio
    mod.asyncio = _asyncio
    return notices, dropped


def _channel(mod):
    ch = object.__new__(mod.BilibiliChannel)
    ch.instance = "agent-49"
    return ch


async def test_successes_collapse_into_one_notice():
    """十条都公开了只该有一条通知：成功没有增量信息，也没有可做的动作。"""
    mod = _load_plugin()
    rows = _rows(9)
    statuses = {row["rpid"]: {"found": True, "visible": True} for row in rows}
    notices, dropped = _wire_pending(mod, rows, statuses)

    await _channel(mod)._check_pending()

    assert len(notices) == 1, f"9 条成功应该合成 1 条，实际 {len(notices)} 条：{notices}"
    assert "9 条" in notices[0]
    assert len(dropped) == 9, "合并的是通知，账目还是要一条一条清掉"


async def test_failures_are_reported_one_by_one():
    """失败每条都不一样（隐藏/没确认），细节才是信息，所以保持逐条。"""
    mod = _load_plugin()
    rows = _rows(2)
    statuses = {row["rpid"]: {"found": True, "hidden": True, "visible": False} for row in rows}
    notices, _dropped = _wire_pending(mod, rows, statuses)

    await _channel(mod)._check_pending()

    assert len(notices) == 2
    assert all("没公开" in text for text in notices)


async def test_single_success_still_reads_like_one_comment():
    """只有一条时不能写成"1 条评论都已公开"——那是病句。"""
    mod = _load_plugin()
    rows = _rows(1)
    statuses = {rows[0]["rpid"]: {"found": True, "visible": True}}
    notices, _dropped = _wire_pending(mod, rows, statuses)

    await _channel(mod)._check_pending()

    assert len(notices) == 1
    assert "已经公开" in notices[0]
    assert "都已公开" not in notices[0]
