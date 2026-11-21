"""我的通道账号 — "我的"页上看到我名下各 AI 在通道侧挂的是哪个号

为什么不复用 AI 页面的通道卡片：卡片是"配置"视角（一个 AI 一份，带 schema、
配对名单、落点候选），这里要的是"账号"视角（我的所有 AI，谁挂着谁）。
逐个 AI 去拉卡片既慢，又会把没配过通道的 AI 也问一遍协议端。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.plugin import channel
from app.utils.auth import get_current_user

router = APIRouter(prefix="/me", tags=["我的 · 通道账号"])


@router.get("/channel-accounts")
async def list_accounts(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我名下各 AI 挂着的通道账号（没配过通道、从没登过的 AI 不出现）"""
    return {"accounts": await channel.accounts(db, int(user["user_id"]))}
