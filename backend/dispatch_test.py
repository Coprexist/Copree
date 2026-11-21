import asyncio, json
from app.database import async_session
from app.tools.base import ToolRegistry

async def main():
    async with async_session() as db:
        print("tool:", type(ToolRegistry._plugins["bili_hot"]).__mro__[1].__name__)
        res = await ToolRegistry.dispatch(db, 49, None, "bili_hot", {"limit": 2}, {})
        print("ok:", res.get("ok"), "count:", res.get("count"))
        await db.commit()

asyncio.run(main())

