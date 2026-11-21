"""音频时长探测（utils/audio_probe）。

为什么自己解析：为一行「[语音 0'42"]」引 ffmpeg/mutagen 不值当——mp3 的定时长只需要
帧头里的码率/采样率（CBR）或首帧 Xing 里的总帧数（VBR）。这里用**手工构造的合法帧**
验证解析器，不依赖磁盘上的样例音频。
"""
from app.utils.audio_probe import audio_duration_ms, mp3_duration_ms, wav_duration_ms

# MPEG1 Layer III / 128kbps / 44100Hz / 无填充：帧长 417 字节，每帧 1152 采样 ≈ 26.1ms
FRAME = b"\xff\xfb\x90\x00" + b"\x00" * (417 - 4)


def test_cbr_mp3_duration():
    ms = mp3_duration_ms(FRAME * 10)
    assert ms is not None and 250 <= ms <= 272, ms


def test_id3_tag_is_skipped():
    tag = b"ID3\x03\x00\x00" + bytes([0, 0, 0, 20 & 0x7F]) + b"\x00" * 20
    assert mp3_duration_ms(tag + FRAME * 10) == mp3_duration_ms(FRAME * 10)


def test_vbr_frame_count_wins():
    """首帧带 Xing 的帧数时按帧数算（CBR 公式会算偏）"""
    head = b"\xff\xfb\x90\x00"
    xing = b"Xing" + (1).to_bytes(4, "big") + (100).to_bytes(4, "big")
    frame = head + b"\x00" * 20 + xing
    ms = mp3_duration_ms(frame + FRAME * 5)
    assert ms is not None and 2600 <= ms <= 2700, ms      # 100 帧 × 26.12ms ≈ 2612ms


def test_garbage_is_not_a_duration():
    assert mp3_duration_ms(b"not an mp3 at all") is None
    assert mp3_duration_ms(b"") is None


def test_wav_duration():
    data = b"\x00" * 88200                                    # 44100Hz × 16bit × 单声道 × 1s
    fmt = b"fmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little") \
        + (1).to_bytes(2, "little") + (44100).to_bytes(4, "little") \
        + (88200).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
    body = fmt + b"data" + len(data).to_bytes(4, "little") + data
    riff = b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WAVE" + body
    assert wav_duration_ms(riff) == 1000
    assert audio_duration_ms(riff, "audio/wav") == 1000
