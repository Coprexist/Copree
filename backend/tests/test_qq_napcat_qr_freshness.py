"""NapCat 二维码的状态判定与"点击才申请"语义。

这些用例盯的是一件事：卡片上的倒计时要代表"这张码真的还能扫几秒"，
而不是"这张图多久没被碰过"。倒计时的起点是我们要码的那一刻，
所以判定函数只认时间戳，不认文件。
"""
import time
from pathlib import Path

import pytest
import sqlalchemy as sa

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "qq-napcat"


def _load_plugin():
    """按生产同样的方式导入插件包（相对 import 才找得到 tables.py）"""
    from app.services.plugin import api, packages

    api.set_current_plugin("qq-napcat")
    try:
        return packages.load("qq-napcat", PLUGIN_DIR)
    finally:
        api.set_current_plugin(None)


plugin = _load_plugin()


def test_fresh_code_counts_down_from_issue_time():
    now = time.time()
    state, left = plugin._qr_state(now - 10, now)
    assert state == "live"
    assert plugin.QR_TTL - 11 <= left <= plugin.QR_TTL - 10


def test_code_past_ttl_is_expired_not_live():
    now = time.time()
    state, left = plugin._qr_state(now - plugin.QR_TTL - 5, now)
    assert state == "expired"
    assert left == 0


def test_never_issued_is_expired():
    assert plugin._qr_state(None, time.time()) == ("expired", 0)


async def _schema():
    """插件表按生产同样的路径建起来：迁移是 DDL 的唯一来源"""
    from app.services.plugin import storage

    result = await storage.ensure("qq-napcat", PLUGIN_DIR)
    assert result["error"] is None


async def _clear(*instances):
    from app.database import async_session

    accounts = plugin.accounts.ACCOUNTS
    async with async_session() as db:
        await db.execute(accounts.delete().where(accounts.c.instance.in_(instances)))
        await db.commit()


async def _rows(instance):
    from app.database import async_session

    accounts = plugin.accounts.ACCOUNTS
    async with async_session() as db:
        return (await db.execute(
            sa.select(accounts.c.uin, accounts.c.nick, accounts.c.avatar)
            .where(accounts.c.instance == instance)
        )).all()


class _StubClient:
    """只实现判定用得到的两个动作，不碰网络"""

    def __init__(self, configured=True, state=None, qr="https://txz.qq.com/p?k=stub", error=None):
        self.base = "http://stub:6099"
        self.configured = configured
        self._state = state or {"isLogin": False, "loginPhase": "waiting_qrcode"}
        self._qr = qr
        self._error = error

    async def status(self):
        if self._error:
            raise plugin.NapCatWebUIError(self._error)
        return self._state

    async def refresh_qr(self):
        return self._qr

    async def login_info(self):
        return {"uin": "10001", "nick": "测试号"}


@pytest.mark.asyncio
async def test_no_code_before_user_asks():
    plugin._QR_ISSUED.clear()
    info = await plugin._login_progress(_StubClient())
    assert info["logged_in"] is False
    assert "qr_png" not in info and "qr_url" not in info


@pytest.mark.asyncio
async def test_asking_yields_a_fresh_code():
    plugin._QR_ISSUED.clear()
    issued = await plugin._request_qr(_StubClient())
    assert issued["qr_url"].startswith("https://txz.qq.com/p?")
    assert issued["qr_expires_in"] == plugin.QR_TTL
    info = await plugin._login_progress(_StubClient())
    assert info["qr_state"] == "live"
    assert info["qr_png"]


@pytest.mark.asyncio
async def test_second_ask_restarts_the_clock():
    plugin._QR_ISSUED.clear()
    await plugin._request_qr(_StubClient(qr="https://txz.qq.com/p?k=one"))
    first = plugin._QR_ISSUED["http://stub:6099"][1]
    await plugin._request_qr(_StubClient(qr="https://txz.qq.com/p?k=two"))
    second = plugin._QR_ISSUED["http://stub:6099"]
    assert second[0].endswith("two")
    assert second[1] >= first


@pytest.mark.asyncio
async def test_logged_in_hides_the_code_and_names_the_account():
    plugin._QR_ISSUED.clear()
    info = await plugin._login_progress(_StubClient(state={"isLogin": True}))
    assert info["logged_in"] is True
    assert info["bot_name"] == "测试号"
    assert "qr_png" not in info


@pytest.mark.asyncio
async def test_unconfigured_endpoint_reports_nothing():
    assert await plugin._login_progress(_StubClient(configured=False)) is None


@pytest.mark.asyncio
async def test_control_plane_error_is_surfaced_not_raised():
    info = await plugin._login_progress(_StubClient(error="连不上协议端控制面"))
    assert info["logged_in"] is False
    assert "连不上" in info["login_error"]


# ── 被顶下线之后还要认得这个号（"一键拉起"要知道拉哪个） ─────────────

@pytest.mark.asyncio
async def test_offline_after_being_kicked_still_names_the_account(migrated_db):
    await _schema()
    await _clear("agent-9")
    plugin._QR_ISSUED.clear()
    await plugin._login_progress(_StubClient(state={"isLogin": True}), instance="agent-9")
    info = await plugin._login_progress(_StubClient(state={"isLogin": False}), instance="agent-9")
    await _clear("agent-9")
    assert info["logged_in"] is False
    assert info["qq_uin"] == "10001"
    assert info["qq_nick"] == "测试号"


@pytest.mark.asyncio
async def test_one_row_per_instance_and_latest_value_wins(migrated_db):
    await _schema()
    await _clear("agent-9", "agent-10")
    save, load = plugin.accounts.save_account, plugin.accounts.load_account
    await save("agent-9", {"uin": "10001", "nick": "A", "avatar": ""})
    await save("agent-9", {"uin": "10001", "nick": "B"})
    assert await _rows("agent-9") == [("10001", "B", "")]
    assert await load("agent-9") == {"uin": "10001", "nick": "B"}
    assert await load("agent-10") == {}
    await _clear("agent-9", "agent-10")


@pytest.mark.asyncio
async def test_nothing_is_written_before_a_number_is_known(migrated_db):
    await _schema()
    await _clear("agent-9")
    await plugin.accounts.save_account("agent-9", {"nick": "只有昵称"})
    assert await _rows("agent-9") == []


@pytest.mark.asyncio
async def test_unchanged_values_do_not_rewrite_the_row(migrated_db):
    """条件 UPSERT 的意义：同值再存一次不该产生一次空 UPDATE（updated_at 不动）"""
    from datetime import datetime

    from app.database import async_session

    await _schema()
    await _clear("agent-11")
    accounts = plugin.accounts.ACCOUNTS
    await plugin.accounts.save_account("agent-11", {"uin": "10002", "nick": "A"})
    async with async_session() as db:
        await db.execute(
            accounts.update().where(accounts.c.instance == "agent-11")
            .values(updated_at=datetime(2000, 1, 1)))
        await db.commit()

    await plugin.accounts.save_account("agent-11", {"uin": "10002", "nick": "A"})
    async with async_session() as db:
        same = (await db.execute(
            sa.select(accounts.c.updated_at).where(accounts.c.instance == "agent-11"))).scalar()
    assert same == datetime(2000, 1, 1)

    await plugin.accounts.save_account("agent-11", {"uin": "10002", "nick": "B"})
    async with async_session() as db:
        changed = (await db.execute(
            sa.select(accounts.c.updated_at).where(accounts.c.instance == "agent-11"))).scalar()
    await _clear("agent-11")
    assert changed > datetime(2000, 1, 1)
