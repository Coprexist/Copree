"""语音配置：key 加密落库、改字段就作废旧结论、能力判据只有 has_voice_capability 一处"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

AGENT_ID, OWNER, AGENT_USER = 999021, 1, 999022


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:o, '主人', 'x', 'human'), (:au, '语音AI', 'x', 'ai')"),
        {"o": OWNER, "au": AGENT_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, state, config_profile, "
        "current_temperature, thinking_enabled, memory_load_mode, memory_recent_count, max_tool_rounds) "
        "VALUES (:a, :o, '语音AI', :u, 'active', 'chat', 0.4, false, 'index_only', 0, 20)"),
        {"a": AGENT_ID, "o": OWNER, "u": AGENT_USER})
    await db.commit()


async def test_key_is_encrypted_and_never_stored_plain(migrated_db):
    from app.database import async_session
    from app.services.agent.voice_config import api_key_of, get_voice_config, save_voice_config

    async with async_session() as db:
        await _seed(db)
        await save_voice_config(
            db, AGENT_ID, provider="breeze", base_url="https://api.breeze.blue/v1",
            api_key="brz-super-secret", voice_id="voc_test", model="",
        )
        await db.commit()

    async with async_session() as db:
        cfg = await get_voice_config(db, AGENT_ID)
        assert cfg is not None
        assert cfg.api_key_encrypted and cfg.api_key_encrypted != "brz-super-secret"
        assert api_key_of(cfg) == "brz-super-secret", "读回来要能解开"
        assert cfg.probe_ok is False, "刚存的配置还没测过，不能算能用"


async def test_changing_any_field_invalidates_the_probe_result(migrated_db):
    """换了音色却沿用旧的"测通了"= 拿一个没验过的配置真去发语音"""
    from app.database import async_session
    from app.services.agent.voice_config import get_voice_config, save_voice_config

    async with async_session() as db:
        await _seed(db)
        await save_voice_config(
            db, AGENT_ID, provider="breeze", base_url="https://api.breeze.blue/v1",
            api_key="k", voice_id="voc_a", model="",
        )
        cfg = await get_voice_config(db, AGENT_ID)
        cfg.probe_ok = True
        cfg.probe_kind = "ok"
        await db.commit()

    async with async_session() as db:
        await save_voice_config(
            db, AGENT_ID, provider="breeze", base_url="https://api.breeze.blue/v1",
            api_key=None, voice_id="voc_b", model="",     # 只换音色，key 不动
        )
        await db.commit()

    async with async_session() as db:
        cfg = await get_voice_config(db, AGENT_ID)
        assert cfg.voice_id == "voc_b"
        assert cfg.probe_ok is False, "改过任何字段都要重新测"


async def test_capability_requires_configured_and_probed_ok(migrated_db):
    from app.database import async_session
    from app.services.agent import voice_config as vc
    from app.services.agent.voice_probe import ProbeResult

    async with async_session() as db:
        await _seed(db)
        assert await vc.has_voice_capability(db, AGENT_ID) is False, "没配 → 没有这个能力"

        await vc.save_voice_config(
            db, AGENT_ID, provider="minimax-cn", base_url="https://api.minimaxi.com/v1",
            api_key="k", voice_id="voice-1", model="",
        )
        await db.commit()
        assert await vc.has_voice_capability(db, AGENT_ID) is False, "配了但没测 → 还不能用"

        cfg = await vc.get_voice_config(db, AGENT_ID)
        original = vc.probe_voice
        calls = {"n": 0}

        async def fake_probe(*args, **kwargs):
            calls["n"] += 1
            return ProbeResult(True, "ok", "Key 有效")

        vc.probe_voice = fake_probe
        try:
            await vc.probe_now(db, cfg)
            await db.commit()
            assert calls["n"] == 1
            assert await vc.has_voice_capability(db, AGENT_ID) is True
        finally:
            vc.probe_voice = original


async def test_failed_probe_keeps_the_capability_off(migrated_db):
    from app.database import async_session
    from app.services.agent import voice_config as vc
    from app.services.agent.voice_probe import ProbeResult

    async with async_session() as db:
        await _seed(db)
        await vc.save_voice_config(
            db, AGENT_ID, provider="breeze", base_url="https://api.breeze.blue/v1",
            api_key="k", voice_id="voc_a", model="",
        )
        cfg = await vc.get_voice_config(db, AGENT_ID)
        original = vc.probe_voice

        async def fake_probe(*args, **kwargs):
            return ProbeResult(False, "quota", "余额不足")

        vc.probe_voice = fake_probe
        try:
            await vc.probe_now(db, cfg)
            await db.commit()
            assert cfg.probe_ok is False and cfg.probe_kind == "quota"
            assert await vc.has_voice_capability(db, AGENT_ID) is False
        finally:
            vc.probe_voice = original
