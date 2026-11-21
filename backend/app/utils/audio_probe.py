"""音频时长探测 —— 不引第三方库，只读文件头。

为什么需要它：会话列表那一行预览要写成「[语音 0'42"]」，而 attachments 快照里只有
文件名与大小。时长是**文件的不可变属性**（内容定了就不会变），所以存进 file_metadata
一次、随消息快照走，列表就不用为每条会话再查一次库。

为什么能不用 ffmpeg/mutagen：mp3 的时长两种算法都只依赖头信息——
- 定码率（CBR）：字节数 × 8 ÷ 码率；
- 变码率（VBR）：首帧的 Xing/Info 里写着总帧数，帧数 × 每帧采样数 ÷ 采样率。
帧头（同步字 + 版本/层/码率/采样率/填充）解析出来就够了，几十行，比拉一个解码器轻。

认不出就返回 None——预览退回「[语音]」，不猜、不报错。
"""
from __future__ import annotations

# MPEG 帧头里的码率表（kbps）。Layer III 是语音场景会遇到的唯一一档
_BITRATES = {
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_SAMPLE_RATES = {
    3: (44100, 48000, 32000),   # MPEG1
    2: (22050, 24000, 16000),   # MPEG2
    0: (11025, 12000, 8000),    # MPEG2.5
}
_SAMPLES_PER_FRAME = {3: 1152, 2: 576, 0: 576}


def _skip_id3(data: bytes) -> int:
    """跳过开头的 ID3v2 标签（大小是 syncsafe 的 7 位编码，不是普通 int）"""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    size = 0
    for byte in data[6:10]:
        size = (size << 7) | (byte & 0x7F)
    footer = 10 if data[5] & 0x10 else 0
    return 10 + size + footer


def _find_frame(data: bytes, start: int) -> int:
    """第一个合法帧头的位置（跳过垃圾字节；认不出就 -1）"""
    i = start
    limit = len(data) - 4
    while i < limit:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            if _frame_info(data, i):
                return i
        i += 1
    return -1


def _frame_info(data: bytes, i: int) -> tuple[int, int, int, int] | None:
    """帧头 → (版本, 码率kbps, 采样率, 帧长)；不合法返回 None"""
    b1 = data[i + 1]
    b2 = data[i + 2]
    b3 = data[i + 3]
    version = (b1 >> 3) & 0x03          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5, 1=保留
    layer = (b1 >> 1) & 0x03            # 1=Layer III
    if version == 1 or layer != 1:
        return None
    bitrate_index = (b2 >> 4) & 0x0F
    rate_index = (b2 >> 2) & 0x03
    padding = (b2 >> 1) & 0x01
    table = _BITRATES.get((1, 3)) if version == 3 else _BITRATES.get((2, 3))
    if bitrate_index == 0 or bitrate_index >= len(table) or rate_index == 3:
        return None
    bitrate = table[bitrate_index]
    sample_rate = _SAMPLE_RATES[version][rate_index]
    # Layer III 的填充就是 1 字节
    frame_len = (144 if version == 3 else 72) * bitrate * 1000 // sample_rate + padding
    if frame_len <= 4:
        return None
    return version, bitrate, sample_rate, frame_len


def _vbr_frames(data: bytes, i: int, version: int) -> int | None:
    """首帧里的 Xing/Info → 总帧数（有就按它算，比按字节数估准）"""
    frame_end = min(len(data), i + 200)
    chunk = data[i:frame_end]
    for tag in (b"Xing", b"Info"):
        pos = chunk.find(tag)
        if pos < 0:
            continue
        if pos + 8 > len(chunk):
            return None
        flags = int.from_bytes(chunk[pos + 4:pos + 8], "big")
        if not flags & 0x01 or pos + 12 > len(chunk):
            return None
        frames = int.from_bytes(chunk[pos + 8:pos + 12], "big")
        return frames or None
    return None


def mp3_duration_ms(data: bytes) -> int | None:
    """mp3 字节 → 毫秒；认不出返回 None"""
    if not data:
        return None
    start = _find_frame(data, _skip_id3(data))
    if start < 0:
        return None
    info = _frame_info(data, start)
    if info is None:
        return None
    version, bitrate, sample_rate, _frame_len = info
    per_frame = _SAMPLES_PER_FRAME[version]

    frames = _vbr_frames(data, start, version)
    if frames:
        return int(round(frames * per_frame * 1000 / sample_rate))

    # 定码率：按整段音频字节数算（ID3 与首帧前的垃圾已经排除）
    audio_bytes = len(data) - start
    if audio_bytes <= 0:
        return None
    return int(round(audio_bytes * 8 / (bitrate * 1000) * 1000))


def wav_duration_ms(data: bytes) -> int | None:
    """wav 字节 → 毫秒（RIFF + fmt 的 byte_rate 足够）"""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos = 12
    byte_rate = None
    data_size = None
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if chunk_id == b"fmt " and pos + 8 + 16 <= len(data):
            byte_rate = int.from_bytes(data[pos + 16:pos + 20], "little")
        elif chunk_id == b"data":
            data_size = size
            break
        pos += 8 + size + (size & 1)
    if not byte_rate or data_size is None:
        return None
    return int(round(data_size * 1000 / byte_rate))


def audio_duration_ms(data: bytes, mime_type: str | None = None) -> int | None:
    """按 mime 挑解析器；认不出返回 None（调用方保持"没时长"的语义）"""
    mime = (mime_type or "").lower()
    if mime == "audio/wav" or mime == "audio/x-wav" or data[:4] == b"RIFF":
        return wav_duration_ms(data)
    return mp3_duration_ms(data)
