"""创建 AI 的辅助填写助手 —— 草稿列表 + 每一轮的 SSE 直播。

只管「填」：真正的建号仍走既有的 POST /agents，表单快照原样交给前端提交。
草稿与对话的存法见 services/creator/agent_creator_service.py 的文件头。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.creator import agent_creator_service as creator
from app.utils.auth import get_current_user

router = APIRouter(prefix="/agent-creator", tags=["创建助手"])

DRAFT_STATUSES = ("open", "created", "abandoned")


class DraftPatch(BaseModel):
    """前端自动存：表单快照随打字回写，建号成功后置 status=created"""
    title: str | None = Field(default=None, max_length=100)
    form: dict | None = None
    preset: str | None = None
    sub: str | None = None
    status: str | None = None
    agent_id: int | None = None


class TurnRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


async def _own_draft(db: AsyncSession, user_id: int, draft_id: int):
    draft = await creator.get_draft(db, user_id, draft_id)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "创建草稿不存在")
    return draft


@router.get("/drafts")
async def list_drafts(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await creator.list_drafts(db, current_user["user_id"])
    return {"drafts": [creator.draft_to_dict(d) for d in rows]}


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
async def create_draft(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    draft = await creator.create_draft(db, current_user["user_id"])
    return creator.draft_to_dict(draft)


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    draft = await _own_draft(db, current_user["user_id"], draft_id)
    return {**creator.draft_to_dict(draft), "messages": await creator.load_transcript(db, draft_id)}


@router.patch("/drafts/{draft_id}")
async def patch_draft(
    draft_id: int,
    req: DraftPatch,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    draft = await _own_draft(db, current_user["user_id"], draft_id)
    if req.status is not None and req.status not in DRAFT_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知状态：{req.status}")
    await creator.update_draft(
        db, draft, title=req.title, form=req.form, preset=req.preset,
        sub=req.sub, status=req.status, agent_id=req.agent_id,
    )
    return creator.draft_to_dict(draft)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(
    draft_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    draft = await _own_draft(db, current_user["user_id"], draft_id)
    await creator.delete_draft(db, draft)


@router.post("/drafts/{draft_id}/turns")
async def run_draft_turn(
    draft_id: int,
    req: TurnRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """一轮对话：SSE 直播（text / reasoning / tool / form / done / error）"""
    # 先在这里校验归属（404 要在流开始前给出去）；真正那一轮由它自己开 session
    await _own_draft(db, current_user["user_id"], draft_id)
    stream = creator.run_turn(current_user["user_id"], draft_id, req.message.strip())
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
