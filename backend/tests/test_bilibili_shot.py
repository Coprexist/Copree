"""B 站取帧：把"某几秒的画面"取成图片。

打桩的是接口与稿子下载（_get / _raw_get），拼图用 Pillow 现画一张：
每格涂不同颜色，所以"第 i 张的像素来自第 i 格"是被真验过的，不是看个数。
"""
import base64
import importlib.util
import io
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

_PLUGIN_FILE = Path(__file__).resolve().parent.parent / "plugins/bilibili/plugin.py"

AID = 1001
CID = 2002
BVID = "BV1xx411c7mD"
DURATION = 120.0
TIMES = [0.0, 0.0, 5.0, 10.0, 20.0, 30.0, 40.0, 50.0]


def _load_plugin():
    spec = importlib.util.spec_from_file_location("bili_plugin_shot_under_test", _PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._shot_cache.clear()
    mod._sprite_cache.clear()
    return mod


def _sheet(times_count: int):
    """画一张 10 列的拼图：第 n 格整块涂 (n*20, 0, 0)，方便反查格子。"""
    from PIL import Image

    cols = 10
    rows = max(1, (times_count + cols - 1) // cols)
    img = Image.new("RGB", (cols * 480, rows * 270), (0, 0, 0))
    for n in range(times_count):
        col, row = n % cols, n // cols
        color = (min(255, 20 + n * 20), 0, 0)
        img.paste(color, (col * 480, row * 270, col * 480 + 480, row * 270 + 270))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, content: bytes) -> None:
        self.status_code = 200
        self.content = content


def _stub(mod, times=None, sheets=None):
    times = TIMES if times is None else times
    sprite = _sheet(len(times))
    sheets = sheets if sheets is not None else ["//bimp.hdslb.com/videoshotpvhdboss/1001_2002-0001.jpg"]

    async def fake_cookie(agent_id):
        return "SESSDATA=x"

    async def fake_get(url, params=None, cookie=""):
        if url.endswith("/x/web-interface/view"):
            return {
                "aid": AID, "bvid": BVID, "duration": DURATION,
                "pages": [{"cid": CID, "duration": DURATION}],
            }
        if "videoshot" in url:
            return {"index": times, "image": sheets}
        raise AssertionError(f"没打桩的接口：{url}")

    async def fake_raw_get(url, params=None, cookie=""):
        return _Resp(sprite)

    mod._agent_cookie = fake_cookie
    mod._get = fake_get
    mod._raw_get = fake_raw_get


def _pixel(mod, item: dict) -> tuple[int, int, int]:
    from PIL import Image

    raw = base64.b64decode(item["url"].split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        return img.convert("RGB").getpixel((10, 10))


async def test_time_takes_the_frame_before_and_after():
    mod = _load_plugin()
    _stub(mod)

    out = await mod.BiliShot().run(49, {"bvid": BVID, "at": [26]})

    assert out["ok"] is True, out
    assert [f["画面"] for f in out["frames"]] == ["00:20", "00:30"], out["frames"]
    assert [f["你要的"] for f in out["frames"]] == ["00:26", "00:26"]
    # 第 4 格涂的是 (100,0,0)，第 5 格 (120,0,0)：图从对的格子裁下来的
    assert [_pixel(mod, it)[0] for it in out["images"]] == [100, 120]
    assert [it["label"] for it in out["images"]] == ["00:20", "00:30"]


async def test_no_time_takes_two_distinct_head_frames():
    mod = _load_plugin()
    _stub(mod)

    out = await mod.BiliShot().run(49, {"bvid": BVID})

    assert [f["画面"] for f in out["frames"]] == ["00:00", "00:05"], out["frames"]
    assert "你要的" not in out["frames"][0]
    assert len(out["images"]) == 2


async def test_time_beyond_the_video_is_an_error():
    mod = _load_plugin()
    _stub(mod)

    out = await mod.BiliShot().run(49, {"bvid": BVID, "at": [999]})

    assert out["ok"] is False, out
    assert "超出片长" in out["error"], out


async def test_page_out_of_range_is_an_error():
    mod = _load_plugin()
    _stub(mod)

    out = await mod.BiliShot().run(49, {"bvid": BVID, "p": 2})

    assert out["ok"] is False, out
    assert "分 P" in out["error"], out


async def test_two_times_give_four_frames_in_time_order():
    mod = _load_plugin()
    _stub(mod)

    out = await mod.BiliShot().run(49, {"bvid": BVID, "at": ["00:12", "00:45"]})

    assert [f["画面"] for f in out["frames"]] == ["00:10", "00:20", "00:40", "00:50"], out["frames"]
    assert len(out["images"]) == 4
