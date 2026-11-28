"""用户备注 API —— 群备注与私信对端备注

列表接口（/groups、/dm/sessions）已经把 remark 一起返回，这里只负责读写。
一个对象至多一条；PUT 空字符串等同删除。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.utils.auth import get_current_user
from app.services.social import remark_service

router = APIRouter(prefix="/user/remarks", tags=["用户备注"])


@router.get("")
async def list_my_remarks(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我设的全部备注（前端会话列表与资料卡共用一份）"""
    return {"remarks": await remark_service.list_remarks(db, current_user["user_id"])}


@router.put("/{target_type}/{target_id}")
async def put_remark(
    target_type: str,
    target_id: int,
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """设备注（空字符串 = 删除）"""
    try:
        saved = await remark_service.set_remark(
            db, current_user["user_id"], target_type, target_id, body.get("remark"),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"remark": saved}


@router.delete("/{target_type}/{target_id}")
async def delete_remark(
    target_type: str,
    target_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删备注（没设过也当成功，删除是幂等的）"""
    try:
        await remark_service.set_remark(db, current_user["user_id"], target_type, target_id, "")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"deleted": True}
