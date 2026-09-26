"""世界下载内容审核 — 违规内容拦截（色情 / 暴力 / 违法）

世界 AI 不得下载色情、暴力、反动内容，平台侧做检查。
**能力边界（必须如实告知，别当成完整审核）**：
- 本地词表只能挡「明显」违规（URL / 文件名 / 文本正文命中），语义级审核做不到；
- 图片 / 音视频内容无法本地判定，靠提示词约束 + 审计日志事后回溯；
- 想接真正的审核服务，在 `_external_check` 里实现并配 env `WORLD_MODERATION_ENDPOINT` 即可
  （默认不启用，所以不预先写死任何第三方协议）。

单一入口：inspect(url, path, content) → 违规原因（中文，可直接回给 AI）或 None（放行）。
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# 分类词表：短词按整词匹配（避免 gore/gorgeous、jav/java 这类误伤），长词按子串匹配
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "色情": (
        "porn", "pornhub", "xvideos", "xnxx", "hentai", "nsfw", "rule34",
        "onlyfans", "brazzers", "camgirl", "escort", "nude", "nudity",
    ),
    "暴力": ("gore", "beheading", "snuff", "massacre", "torture", "decapitation"),
    "违法": ("nazi", "isis", "jihad", "childporn", "counterfeit", "darkweb"),
}
_SHORT_WORD_LEN = 6          # 短于 6 个字符的词按整词匹配，避免子串误伤

# 额外兜底：env 追加关键词（逗号分隔），给运营留一条不改代码的路
_EXTRA_KEYWORDS = tuple(
    k.strip().lower()
    for k in os.environ.get("WORLD_MODERATION_EXTRA_KEYWORDS", "").split(",")
    if k.strip()
)


def _hits(text: str) -> list[str]:
    """文本命中的违规词（返回 ["分类:词", ...]，去重保序）"""
    low = text.lower()
    tokens = set(re.findall(r"[a-z0-9]+", low))
    found: list[str] = []
    for category, words in _KEYWORDS.items():
        for word in (*words, *_EXTRA_KEYWORDS):
            hit = word in tokens if len(word) < _SHORT_WORD_LEN else word in low
            if hit:
                found.append(f"{category}:{word}")
    return list(dict.fromkeys(found))


def inspect(url: str, path: str = "", content: bytes | None = None) -> str | None:
    """下载审核唯一入口。

    url/path 在下载前查（便宜）；content 在下载后查（只扫文本，二进制跳过）。
    返回违规原因（中文）或 None。
    """
    hits = _hits(f"{url} {path}")
    if hits:
        return f"该链接/文件名命中违规内容（{', '.join(hits)}），平台禁止下载色情/暴力/违法内容"
    if content:
        try:
            text = content[:200_000].decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return None                       # 二进制（图片/音视频）本地判不了，交给提示词约束 + 审计
        hits = _hits(text)
        if hits:
            return f"文件正文命中违规内容（{', '.join(hits)}），已拦截且未落盘"
    return None


def audit(world_id: int, url: str, path: str, reason: str) -> None:
    """违规拦截留痕（排查/举证用；平台日志属于运维数据，不写入世界目录）"""
    logger.warning(f"🚫 世界 #{world_id} 下载被拦截: {url[:100]} → {path or '（自动命名）'}｜{reason}")
