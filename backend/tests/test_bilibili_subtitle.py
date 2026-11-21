"""B 站字幕稿：读一条视频里"人说了什么"。

打桩的是两个出口（_get 打播放器/详情接口，_raw_get 打稿子文件）：
用例只钉四件事——串台的稿子要丢掉重问、全都串台就如实说拿不到、
没有稿子时说没有、一次只回一段并在尾巴上告诉模型怎么接着读。
"""
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

_PLUGIN_FILE = Path(__file__).resolve().parent.parent / "plugins/bilibili/plugin.py"

AID = 1001
CID = 2002
BVID = "BV1xx411c7mD"
DURATION = 100


def _load_plugin():
    spec = importlib.util.spec_from_file_location("bili_plugin_subtitle_under_test", _PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._sub_cache.clear()
    mod._SUB_TRIES = 2          # 真机上 4 次；用例里 2 次就够验证"会重问"
    return mod


def _url(aid: int, cid: int, tag: str = "a") -> str:
    # 真站上的形状：bfs/ai_subtitle/prod/<aid><cid><hash>?auth_key=...
    return f"https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{aid}{cid}{tag}?auth_key=1"


def _track(url: str) -> dict:
    return {"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": url, "type": 1, "id": 7}


def _body(*lines: str) -> dict:
    return {
        "font_size": 0.4, "type": "json", "lang": "zh",
        "body": [{"content": text, "from": i * 2.0, "to": i * 2.0 + 2.0} for i, text in enumerate(lines)],
    }


class _Resp:
    """_raw_get 的替身：只要 status_code 与 json()。"""

    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


def _stub(mod, rounds, payloads):
    """rounds: 第 n 次问播放器时给哪些稿子；payloads: url 关键字 → body。"""
    calls = {"n": 0}

    async def fake_cookie(agent_id):
        return "SESSDATA=x"

    async def fake_get(url, params=None, cookie=""):
        if url.endswith("/x/web-interface/view"):
            return {
                "aid": AID, "bvid": BVID, "duration": DURATION,
                "pages": [{"cid": CID, "duration": DURATION}],
            }
        if "/x/player/v2" in url:
            assert params["aid"] == AID and params["cid"] == CID
            got = rounds[min(calls["n"], len(rounds) - 1)]
            calls["n"] += 1
            return {"subtitle": {"subtitles": got}}
        raise AssertionError(f"没打桩的接口：{url}")

    async def fake_raw_get(url, params=None, cookie=""):
        for key, payload in payloads.items():
            if key in url:
                return _Resp(payload)
        raise AssertionError(f"没打桩的稿子地址：{url}")

    mod._agent_cookie = fake_cookie
    mod._get = fake_get
    mod._raw_get = fake_raw_get
    return calls


async def test_mixed_up_transcript_is_dropped_and_asked_again():
    mod = _load_plugin()
    good, wrong = _url(AID, CID), _url(9, 9)
    _stub(
        mod,
        [
            [_track(wrong)],     # 第一次：B 站把别人的稿子挂在这条视频上
            [_track(good)],      # 重问一次拿到对的
        ],
        {
            f"{AID}{CID}": _body("这条视频里人说的话"),
            f"99": _body("别人的视频在讲火锅"),
        },
    )

    out = await mod.BiliSubtitle().run(49, {"bvid": BVID})

    assert out["ok"] is True, out
    assert out["text"] == "这条视频里人说的话", out
    assert "火锅" not in out["text"]


async def test_all_attempts_mixed_up_says_so_instead_of_guessing():
    mod = _load_plugin()
    wrong = _url(9, 9)
    _stub(mod, [[_track(wrong)]], {f"99": _body("别人的视频在讲火锅")})

    out = await mod.BiliSubtitle().run(49, {"bvid": BVID})

    assert out["ok"] is False, out
    assert "对不上" in out["error"], out
    assert "text" not in out


async def test_no_subtitle_at_all_is_reported_as_such():
    mod = _load_plugin()
    _stub(mod, [[]], {})

    out = await mod.BiliSubtitle().run(49, {"bvid": BVID})

    assert out["ok"] is False, out
    assert "没有字幕稿" in out["error"], out


async def test_one_read_is_capped_and_points_at_the_next_chunk():
    mod = _load_plugin()
    lines = [f"第{i}句话" for i in range(400)]   # 每句 5 字左右，总量远超一次的上限
    _stub(mod, [[_track(_url(AID, CID))]], {f"{AID}{CID}": _body(*lines)})

    out = await mod.BiliSubtitle().run(49, {"bvid": BVID})

    assert out["ok"] is True, out
    assert len(out["text"]) <= mod._SUB_CHARS + 8, len(out["text"])
    assert "from=" in out["还有"], out
    assert out["text"].startswith("第0句话")
    # 提示里的起点必须真的是下一句的时间，接上去读不能重、也不能跳
    nxt = int(out["还有"].split("from=")[1].split()[0])
    assert nxt == int(out["count"] * 2), (nxt, out["count"])


async def test_window_reads_exactly_that_stretch():
    mod = _load_plugin()
    lines = [f"第{i}句话" for i in range(30)]      # 每句 2 秒
    _stub(mod, [[_track(_url(AID, CID))]], {f"{AID}{CID}": _body(*lines)})

    out = await mod.BiliSubtitle().run(49, {"bvid": BVID, "from": "00:20", "to": 30})

    assert out["ok"] is True, out
    assert out["范围"] == "00:20–00:30", out
    assert out["text"] == "".join(lines[10:15]), out["text"]


async def test_page_out_of_range_is_an_error_not_a_silent_switch():
    mod = _load_plugin()
    _stub(mod, [[_track(_url(AID, CID))]], {f"{AID}{CID}": _body("唯一的一 P")})

    out = await mod.BiliSubtitle().run(49, {"bvid": BVID, "p": 3})

    assert out["ok"] is False, out
    assert "分 P" in out["error"], out
