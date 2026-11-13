"""本插件的账号记录：每个实例上次挂过的号

为什么落库而不是写文件：协议端被顶下线之后问不到 uin，而"一键拉起"必须知道拉哪个号。
文件是整读整写、只能靠文件名区分实例；表有唯一键（一个实例一行）与行级原子性。
表由 migrations/ 建，这里只有读取与写入。
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from .tables import ACCOUNTS, T

_FIELDS = ("uin", "nick", "avatar")


async def load_account(instance: str) -> dict[str, Any]:
    """这个实例上次挂过的号；实例还没配（也就没地方记）就给空"""
    if not instance:
        return {}
    from app.database import async_session

    async with async_session() as db:
        row = (await db.execute(
            sa.select(ACCOUNTS.c.uin, ACCOUNTS.c.nick, ACCOUNTS.c.avatar)
            .where(ACCOUNTS.c.instance == instance)
        )).first()
    if row is None:
        return {}
    return {k: v for k, v in zip(_FIELDS, row) if v}


async def save_account(instance: str, account: dict[str, Any]) -> None:
    """记下这个实例挂过的号

    "值没变就不写"落在**一条语句**里（`ON CONFLICT … DO UPDATE … WHERE 有列不同`）：
    - 无变化时零写入（空 UPDATE 只会给表添死元组），只花一次往返；
    - 比"先读再比再写"少一次查询，也没有读与写之间那个窗口。
    条件用 `!=`（三列都 NOT NULL 有默认值），PostgreSQL 与 SQLite 都认。
    """
    uin = str(account.get("uin") or "").strip()
    if not instance or not uin:
        return
    row = {
        "uin": uin,
        "nick": str(account.get("nick") or "").strip(),
        "avatar": str(account.get("avatar") or "").strip(),
    }
    stmt = T.insert(ACCOUNTS).values(instance=instance, **row)
    changed = sa.or_(*(ACCOUNTS.c[k] != stmt.excluded[k] for k in row))
    from app.database import async_session

    async with async_session() as db:
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["instance"],
                set_={**row, "updated_at": sa.func.now()},
                where=changed,
            )
        )
        await db.commit()
