"""消息出口注册表：同名不去重 + 并发分发（踩过：同名覆盖）

2026-09-25 线上事故：新绑的第二个 QQ 通道（agent-8）注册出口时把第一个（agent-24）顶掉了
→ 群 64 的 AI 回复被分发到群 65 的 sink、静默丢弃。现在注册按句柄、分发并发跑全部。
"""
import pytest

pytestmark = pytest.mark.anyio


async def test_same_name_sinks_all_fire():
    from app.chat.outbound import dispatch_group_message, register_sink, unregister_sink

    calls: list[str] = []

    async def sink_a(db, gid, msg, src): calls.append("a")
    async def sink_b(db, gid, msg, src): calls.append("b")

    h1 = register_sink("qq-channel", group=sink_a)
    h2 = register_sink("qq-channel", group=sink_b)
    try:
        await dispatch_group_message(None, 64, object(), "user")
        assert sorted(calls) == ["a", "b"], "同名两个出口都要发，谁也别顶掉谁"
    finally:
        unregister_sink(h1)
        unregister_sink(h2)


async def test_one_bad_sink_does_not_block_the_others():
    from app.chat.outbound import dispatch_group_message, register_sink, unregister_sink

    calls: list[str] = []

    async def boom(db, gid, msg, src): raise RuntimeError("通道坏了")
    async def ok(db, gid, msg, src): calls.append("ok")

    h1 = register_sink("test-boom", group=boom)
    h2 = register_sink("test-ok", group=ok)
    try:
        await dispatch_group_message(None, 64, object(), "user")   # 不抛异常
        assert calls == ["ok"], "一个坏出口不能拖累别的，更不能拖垮发消息"
    finally:
        unregister_sink(h1)
        unregister_sink(h2)


def test_plugin_key_carries_the_instance():
    from app.services.plugin.api import ServicePlugin

    class _P(ServicePlugin):
        id = "qq-channel"
        instance = "agent-24"

    assert _P().key == "qq-channel:agent-24", "实例必须体现在 key 里"
