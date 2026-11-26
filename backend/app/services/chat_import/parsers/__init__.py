"""
解析器注册表 —— 「外部 payload → 统一结构」的唯一入口。

解析器是纯函数：不起网络、不落库、不认识数据库。抓取与解析分开，
是为了让「不合规的那一段」只出现在插件的网络层，而解析逻辑可离线测试。

识别顺序：每个解析器自己报置信度（detect 返回 0~1），取最高分；都不认就退回粘贴解析器
（它对任意文本都给一个基础分——用户明确说了「粘贴全文」，我们不该回一句「格式不支持」）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.services.chat_import.schema import Transcript

ParserFn = Callable[[str, str | None], Transcript]
DetectFn = Callable[[str, str | None], float]


@dataclass(frozen=True)
class Parser:
    name: str            # 稳定标识，写进 transcript.origin.parser
    platform: str        # 展示用平台名
    version: int
    parse: ParserFn
    detect: DetectFn


_registry: list[Parser] = []


def register(parser: Parser) -> None:
    """注册一个解析器。重复注册同名用后者覆盖（热更新安全）。"""
    global _registry
    _registry = [p for p in _registry if p.name != parser.name] + [parser]


def available() -> list[dict]:
    """给前端用的清单：认哪些平台、哪些格式。"""
    return [
        {"name": p.name, "platform": p.platform, "version": p.version}
        for p in _registry
    ]


def detect(payload: str, filename: str | None = None) -> Parser | None:
    best: tuple[float, Parser] | None = None
    for p in _registry:
        try:
            score = float(p.detect(payload, filename))
        except Exception:  # 解析器自己的判断出错不该拖垮整次导入
            score = 0.0
        if score > 0 and (best is None or score > best[0]):
            best = (score, p)
    return best[1] if best else None


def parse(payload: str, *, filename: str | None = None, parser: str | None = None) -> Transcript:
    """把 payload 解析成统一结构。

    :param parser: 指定解析器（走「内部指定」而不是用户选择）；None = 自动识别
    """
    if parser:
        for p in _registry:
            if p.name == parser:
                return p.parse(payload, filename)
        raise ValueError(f"没有叫 {parser} 的解析器")
    chosen = detect(payload, filename)
    if chosen is None:
        raise ValueError("认不出这段内容的格式")
    return chosen.parse(payload, filename)


# 内置解析器在包末尾导入，它们通过上面的 register() 挂进来（顺序无关，按置信度选）
from app.services.chat_import.parsers import pasted, deepseek  # noqa: E402,F401
