"""用户自制表情包 API —— 我的表情包：新建 / 加表情 / 删表情 / 删包。

路径挂在 /emoji-packs（用户级资源，不属于某个 AI）：表情包是创建者自己的资产，
AI 只是"能不能用"的一层开关（见 /agents/{id}/emoji-packs）。两者分开，
同一个包才能既属于我、又被我几只 AI 各自开关。

权限只看归属：所有写操作都在 service 里核对 manifest 的 owner_user_id，
不是作者一律 400（"不存在或不属于你"），不需要再分管理员/合作者。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.plugin import catalog, emoji_packs
from app.utils.auth import get_current_user

router = APIRouter(prefix="/emoji-packs", tags=["emoji-packs"])


class PackCreateRequest(BaseModel):
    name: str = ""
    usage: str = ""


@router.get("/mine")
async def list_my_packs(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我做的表情包（与上限一起给前端，界面不用自己记数字）"""
    await catalog.sync_plugins_to_db(db)      # 手装/手删过目录也能马上看到
    packs = await emoji_packs.list_mine(db, current_user["user_id"])
    return {
        "packs": packs,
        "limits": {
            "packs_max": emoji_packs.MAX_PACKS_PER_USER,
            "faces_max": catalog.EMOJI_FACE_MAX,
            "image_max_mb": emoji_packs.FACE_IMAGE_MAX_MB,
        },
    }


@router.post("")
async def create_pack(
    req: PackCreateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """新建一个表情包（空包，随后往里加表情）"""
    try:
        return await emoji_packs.create_pack(
            db,
            user_id=current_user["user_id"],
            name=req.name,
            usage=req.usage,
            author=str(current_user.get("username") or ""),
        )
    except emoji_packs.EmojiPackError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{pack_id}/faces")
async def add_face(
    pack_id: str,
    file: UploadFile = File(...),
    name: str = Form(""),
    caption: str = Form(""),
    keywords: str = Form(""),
    mood: str = Form(""),
    emoji: str = Form(""),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传一个表情。名字与说明（caption）是给 AI 用的，说明必填。"""
    content = await file.read()
    try:
        return await emoji_packs.add_face(
            db,
            user_id=current_user["user_id"],
            pack_id=pack_id,
            name=name,
            caption=caption,
            keywords=keywords,
            mood=mood,
            emoji=emoji,
            content=content,
            content_type=file.content_type or "",
        )
    except emoji_packs.EmojiPackError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{pack_id}/faces/{face_id}")
async def remove_face(
    pack_id: str,
    face_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删掉一个表情"""
    try:
        await emoji_packs.remove_face(
            db, user_id=current_user["user_id"], pack_id=pack_id, face_id=face_id,
        )
    except emoji_packs.EmojiPackError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "已删除", "pack_id": pack_id, "face_id": face_id}


@router.delete("/{pack_id}")
async def delete_pack(
    pack_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删掉整个表情包（AI 那边的开关记录一并清掉）"""
    try:
        await emoji_packs.delete_pack(db, user_id=current_user["user_id"], pack_id=pack_id)
    except emoji_packs.EmojiPackError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "已删除", "pack_id": pack_id}
