"""B 站楼中楼回复：root 与 parent 必须成对，且 root 必须是整栋楼的根。

2026-11-21 线上事故：只发 root、或把楼中楼的 rpid 当成 root 传上去时，B 站**不报错**
（code=0），而是把这条回复当成视频下的新评论——用户看到的是"又发了一堆新评论"，
回复其实谁都没收到。用例钉四件事：根评论原样、楼中楼反查到根、两个字段一起发、
以及行里给出"回我的那条"自己的 rpid（回 root 只会挂到根评论下，接不上人）。
"""
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

_PLUGIN_FILE = Path(__file__).resolve().parent.parent / "plugins/bilibili/plugin.py"

ROOT = 319113399761     # 整栋楼的根评论
SUB = 324706788336      # 同一栋楼里的一条楼中楼（2026-11-21 实测值）
BVID = "BV1z54G6vEMR"
AID = 117552902577110


def _load_plugin():
    spec = importlib.util.spec_from_file_location("bili_plugin_reply_under_test", _PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wire(mod, posted: list):
    """把外网与写库全换成打桩，只留「发出去的 body 长什么样」这一件可观察的事。"""
    async def fake_get(url, params=None, cookie=""):
        if url.endswith("/x/v2/reply/reply"):
            asked = int((params or {}).get("root") or 0)
            # 实测：问根评论原样回自己，问楼中楼回它所属的顶层
            return {"root": {"rpid": ROOT if asked in (ROOT, SUB) else asked}, "replies": []}
        return {}

    async def fake_post(path, data, cookie="", *, referer=""):
        posted.append((path, dict(data)))
        return {"rpid": 999}

    async def fake_write_ctx(agent_id):
        return {"bili_jct": "jct", "nickname": "测试号"}, "SESSDATA=x"

    async def fake_aid_of(bvid, cookie=""):
        return AID

    async def fake_pending_add(**kwargs):
        return None

    mod._get = fake_get
    mod._post = fake_post
    mod._write_ctx = fake_write_ctx
    mod._aid_of = fake_aid_of
    mod._pending_add = fake_pending_add
    mod._instance_of = lambda agent_id: "agent-49"


async def _reply(mod, **extra):
    posted: list = []
    _wire(mod, posted)
    args = {"action": "reply", "bvid": BVID, "message": "回你一句", **extra}
    out = await mod.BiliComment().run(49, args)
    assert out["ok"] is True, out
    return posted[-1]


async def test_root_comment_sends_root_and_parent_together():
    """回根评论：root 是它自己，parent 也必须发——少了它 B 站就丢到最顶层。"""
    mod = _load_plugin()
    path, body = await _reply(mod, rpid=ROOT)
    assert path == "/x/v2/reply/add"
    assert body["root"] == ROOT
    assert body["parent"] == ROOT, "只发 root 时 B 站把回复当成视频下的新评论"


async def test_sub_reply_rpid_is_resolved_to_the_thread_root():
    """模型拿到的是"别人回我的那条"：root 要换成整栋楼的根，parent 才是它自己。"""
    mod = _load_plugin()
    _path, body = await _reply(mod, rpid=SUB)
    assert body["root"] == ROOT, "把楼中楼 rpid 当 root 会挂错楼"
    assert body["parent"] == SUB


async def test_anchor_hands_back_the_pair_so_one_cannot_be_dropped():
    """一个 rpid 进，一对 (root, parent) 出：想只拿一个都拿不到，漏发写不出来。"""
    mod = _load_plugin()
    posted: list = []
    _wire(mod, posted)
    pair = await mod._reply_anchor(AID, SUB, "SESSDATA=x")
    assert isinstance(pair, tuple) and len(pair) == 2, pair
    assert pair == (ROOT, SUB)


def test_parent_is_not_a_model_facing_parameter():
    """root/parent 是接口细节：模型只说"回哪条"，成对推导只在一处负责。"""
    mod = _load_plugin()
    assert "parent" not in mod.BiliComment.parameters, "别把 B 站的字段名漏进工具 schema"
    assert "rpid" in mod.BiliComment.parameters


def test_comment_row_carries_the_thread_root():
    """楼中楼每一行都要带 root：模型照着 root 回，就不会漏掉 parent。"""
    mod = _load_plugin()
    item = {"rpid": ROOT, "member": {"uname": "甲"}, "content": {"message": "你好"},
            "like": 0, "rcount": 2, "ctime": 0}
    assert mod._comment_row(item)["root"] == ROOT, "顶层评论的 root 就是它自己"
    sub = mod._comment_row({**item, "rpid": SUB}, root=ROOT)
    assert sub["rpid"] == SUB
    assert sub["root"] == ROOT


def test_reply_row_points_at_the_reply_itself():
    """回我的那条自己有一个 rpid（source_id）：行里给这个，回 root 只能挂到根评论下。"""
    mod = _load_plugin()
    row = mod._reply_row("reply", {
        "id": 1207374715035650,          # 消息流水号，1e15 量级，不是 rpid
        "reply_time": 1,
        "user": {"nickname": "江西华龙兄弟"},
        "item": {
            "root_id": ROOT, "source_id": SUB,
            "source_content": "回你一句",
            "uri": f"https://www.bilibili.com/video/{BVID}",
        },
    })
    assert row["root"] == str(ROOT)
    assert row["src"] == str(SUB), "source_id 才是这条回复自己的 rpid"
    assert row["key"] == "1207374715035650", "流水号只当游标用"
    assert mod._row_line(row) == f"回复｜江西华龙兄弟｜{BVID}#{SUB}｜回你一句"


def test_row_without_source_id_falls_back_to_root():
    """旧账本里的行没有 src：退回 root，不能崩。"""
    mod = _load_plugin()
    row = {"kind": "reply", "key": "1", "ts": 1, "who": "甲", "target": BVID,
           "title": "", "said": "一句", "root": str(ROOT), "counts": 0}
    assert mod._row_line(row) == f"回复｜甲｜{BVID}#{ROOT}｜一句"
