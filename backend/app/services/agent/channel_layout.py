"""一条通道的 system 摆放怎么定 —— 保存时判一次，对话时只读结论。

两套配置（管理员 system_settings.provider_config 与用户 user_api_providers）原来各写一份
优先级：一边是「手选 > 探测 > 预设」，另一边干脆没有判定、默认 anywhere。于是用户自己
填了本机 llama-server 之后，每轮都把 system 塞在对话中段，Qwen 的 Jinja 直接 500
（模板要求 system 在开头，报 System message must be at the beginning）。

这里把「怎么定」收成一处，优先级只写一遍，两条路径都调它：

    手选（用户明确选了）
      > 探测（保存时读对端 chat 模板，5 秒上限）
      > 预设（平台维护的结论）
      > 按地址保守猜（字面私网 → leading）

探测失败不阻塞保存：拿不到结论就沿用后面的档位，只是少了「自动识别」的收益。
对话路径永不探测，只读已存的结论（适配规则在 utils/pure/chat_template.py）。
"""
from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone

from app.utils.pure.chat_template import (
    LAYOUTS, LEADING, guess_layout_from_host, is_system_layout_error,
    normalize_layout,
)

logger = logging.getLogger(__name__)

# 结论的来源。落库后用来判断「这一档是不是可信的」：
#   user     —— 用户在界面上手选的，永远以它为准
#   detected —— 保存时读对端模板判出来的
#   preset   —— 内置预设给的（平台维护）
#   host     —— 什么证据都没有，按地址保守猜的（私网 → leading）
SOURCES = ("user", "detected", "preset", "host")


@dataclass(frozen=True)
class LayoutDecision:
    """一次的判定结论。ok=False 的概念不需要：任何情况都有结论，只是来源不同。"""

    layout: str
    source: str
    template_hash: str = ""
    checked_at: str = ""      # ISO 串；落库时由调用方按列类型转换
    message: str = ""         # 给人看的一句话（界面上就显示这个）


async def decide_system_layout(
    *,
    base_url: str | None,
    model: str | None = None,
    manual: str | None = None,
    preset_layout: str | None = None,
    probe: bool = True,
    allow_private: bool = False,
    allow_private_hosts: Collection[str] = (),
) -> LayoutDecision:
    """定下这一条通道的 system 摆放。

    manual 是「用户明确选择」：传了且是合法档位就立刻返回，不探测。
    probe=False 用于批量接入这类「一次接很多家」的场景——预设已经给了结论，
    为每家各等 5 秒探测没有收益。
    """
    if isinstance(manual, str) and manual.strip().lower() in LAYOUTS:
        return LayoutDecision(normalize_layout(manual), "user",
                              message="已按手动指定保存")

    if probe and (base_url or "").strip():
        # 延迟 import：探测要 httpx，而多数调用（批量接入）根本不会走到这里
        from app.services.agent.template_probe import probe_chat_template

        result = await probe_chat_template(
            base_url, model=model or None,
            allow_private=allow_private, allow_private_hosts=allow_private_hosts,
        )
        if result.ok:
            return LayoutDecision(
                result.layout, "detected",
                template_hash=result.template_hash,
                checked_at=datetime.now(timezone.utc).isoformat(),
                message=result.message,
            )

    if preset_layout:
        return LayoutDecision(normalize_layout(preset_layout), "preset",
                              message="沿用内置预设")

    return LayoutDecision(guess_layout_from_host(base_url), "host",
                          message="未识别到模板，按地址保守取值")


# 同一通道 10 分钟内只学一次：多轮连续失败不该反复写库，也不该把日志刷满
_learned_at: dict[str, float] = {}
LEARN_INTERVAL_SECONDS = 600


async def learn_from_error(db, base_url: str, error_text: str = "") -> bool:
    """把「对端要求 system 放开头」学进这条通道的结论（下一轮生效）。

    只写用户厂商行：系统预设是管理员维护的模板，自动改写会让「我只填了个地址，
    配置怎么自己变了」无从解释；系统预设那条只在日志里提示管理员。
    返回是否真的写了一次（调用方据此决定给用户的话）。
    """
    import time

    from sqlalchemy import select

    from app.models.api_profile import UserApiProvider
    from app.utils.pure.llm_endpoint import server_root

    if not (base_url or "").strip() or not is_system_layout_error(error_text):
        return False

    root = server_root(base_url)
    now = time.monotonic()
    last = _learned_at.get(root)
    if last is not None and now - last < LEARN_INTERVAL_SECONDS:
        return False
    _learned_at[root] = now

    rows = (await db.execute(
        select(UserApiProvider).where(UserApiProvider.kind == "llm")
    )).scalars().all()
    for row in rows:
        if server_root(row.base_url) != root:
            continue
        if row.system_layout == LEADING and row.system_layout_source == "detected":
            return False
        row.system_layout = LEADING
        row.system_layout_source = "detected"
        row.system_layout_checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        logger.info(
            "通道 %s 的 system 摆放已纠正为 leading（对端模板报错，下一轮生效）",
            row.base_url,
        )
        return True

    logger.warning(
        "对端要求 system 放开头，但没找到匹配的用户厂商行（系统预设不自动改）：%s",
        base_url,
    )
    return False
