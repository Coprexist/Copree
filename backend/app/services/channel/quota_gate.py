"""
通道对话额度的准入闸门 —— 插件与"额度"之间的唯一接口。

分层：字段、默认值、文案都声明在插件的 config_schema 里（设置属于插件）；
平台只保存生效中的策略、记账、判定。这里收掉每个通道插件都会重复的两件事：

  sync_policy(cfg)  启动时把设置推给平台——记账发生在模型调用那一侧，那里加载不到插件实例
  admit(...)        落库之前问一句"这个对话还能不能用"

说明文案怎么发不在这里：出口是插件的事（官方 QQ 只能被动回复、NapCat 能主动发），
gate 只把要发的那一句交给调用方传进来的 notify。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Mapping

# 插件 config_schema 里的四个字段名：声明在插件、解析在这里。
# 名字只写一遍，插件的 schema 与平台的读取才不会各说各话。
CFG_METER_MODE = "quota_meter_mode"
CFG_LIMIT_TOKENS = "quota_limit_tokens"
CFG_BLOCKED_REPLY = "quota_blocked_reply"
CFG_BLOCKED_REPLY_TEXT = "quota_blocked_reply_text"

_FALSEY = {"0", "false", "off", "no", "none"}


def _as_flag(value: object, *, default: bool) -> bool:
    """配置里的布尔：空 = 用默认；字符串按字面理解（"false" 不是真）"""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY
    return bool(value)


def policy_from_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """把插件声明的四项设置翻译成 set_policy 的参数；脏值交给平台那边归一"""
    return {
        "meter_mode": str(cfg.get(CFG_METER_MODE) or ""),
        "limit_tokens": cfg.get(CFG_LIMIT_TOKENS),
        "blocked_reply_enabled": _as_flag(cfg.get(CFG_BLOCKED_REPLY), default=True),
        "blocked_reply_text": str(cfg.get(CFG_BLOCKED_REPLY_TEXT) or ""),
    }


class QuotaGate:
    """一个通道实例一个闸门：绑定到哪个 AI、哪种通道，就记着这层关系。"""

    def __init__(self, agent_id: int, channel_kind: str, log: logging.Logger) -> None:
        self._agent_id = int(agent_id or 0)
        self._channel_kind = str(channel_kind)
        self._log = log

    @property
    def active(self) -> bool:
        """没绑定 AI 就不参与额度：通道照常收发，只是不记账"""
        return self._agent_id > 0

    async def sync_policy(self, cfg: Mapping[str, Any]) -> None:
        if not self.active:
            return
        from app.database import async_session
        from app.services.channel import token_quota

        try:
            async with async_session() as db:
                await token_quota.set_policy(
                    db, self._agent_id, self._channel_kind, **policy_from_config(cfg))
                await db.commit()
        except Exception as e:
            # 同步失败不该挡住通道启动：记账那侧会退回默认额度
            self._log.warning(f"同步对话额度设置失败（agent={self._agent_id}）：{e}")

    async def admit(self, db, *, kind: str, target: int | str, label: str = "",
                    copree_group_id: int | None = None, dm_session_id: str | None = None,
                    notify: Callable[[str], Awaitable[None]] | None = None) -> bool:
        """True = 继续落库；False = 这条不进 Copree（已按设置回了一句说明）。

        拦在落库之前，而不是记账之后：落库会唤醒 AI、跑完一轮模型调用，那时额度已经花出去了。
        """
        if not self.active:
            return True
        from app.services.channel import token_quota
        from app.utils.pure.channel_token import conversation_key

        conversation = conversation_key(kind, target)
        try:
            # 包在 SAVEPOINT 里：判定是"顺带一问"，它失败不能把外层的落库事务打成 aborted，
            # 否则同一条消息的后续写入全部报 InFailedSQLTransaction。
            async with db.begin_nested():
                decision = await token_quota.decide(
                    db, agent_id=self._agent_id, channel_kind=self._channel_kind,
                    conversation=conversation, label=label,
                    copree_group_id=copree_group_id, dm_session_id=dm_session_id)
        except Exception as e:
            self._log.warning(f"额度判定失败，本次放行（{conversation}）：{e}")
            return True
        if decision["allowed"]:
            return True
        if decision["notice"] and notify is not None:
            await notify(decision["notice"])
        self._log.info("对话 %s 额度已用完（agent=%s），这条消息不落库", conversation, self._agent_id)
        return False
