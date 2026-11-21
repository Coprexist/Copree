import asyncio
from pathlib import Path

from app.services.plugin.skill_bridge import _load_plugin_code
from app.tools.base import ToolRegistry


async def main():
    ok = _load_plugin_code("bilibili", Path("/app/plugins/bilibili"))
    print("plugin loaded:", ok, "| bili_hot registered:", "bili_hot" in ToolRegistry._plugins)
    from app.database import async_session
    async with async_session() as db:
        res = await ToolRegistry.dispatch(db, 49, None, "bili_hot", {"limit": 2}, {})
        print("tool ok:", res.get("ok"), "| count:", res.get("count"))
        await db.commit()
    from sqlalchemy import text
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT instance, action, ok, duration_ms, summary FROM plugin_bilibili__activity ORDER BY id DESC LIMIT 3"
        ))).all()
        for r in rows:
            print("trail:", r.instance, r.action, r.ok, r.duration_ms, (r.summary or "")[:60])


asyncio.run(main())
