"""
搜索路由（v0.1.3: 独立于好友系统，搜索结果可直接发起 DM）
"""
import logging
from fastapi import APIRouter, Depends, Query

from app.repositories.search_repo import SearchRepository
from app.routers.deps import get_search_repo
from app.services.social.search_service import search_entities, search_groups
from app.utils.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["搜索"])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    current_user: dict = Depends(get_current_user),
    search_repo: SearchRepository = Depends(get_search_repo),
):
    """搜索用户、AI 与群聊。

    群聊单独一组返回（只含群主开了「可被搜索」的群）：前端按用户/AI/群聊分区渲染，
    塞进同一个列表会逼前端靠 type 猜字段。
    """
    results = await search_entities(search_repo, q, current_user["user_id"])
    groups = await search_groups(search_repo, q, current_user["user_id"])
    return {"results": results, "groups": groups, "query": q}
