"""
从聊天记录导入 —— 两个接口，一个「能做什么」的清单。

    GET  /chat-import          认哪些格式、能不能抓链接（装了插件才有）
    POST /chat-import/resolve  来源 → 统一结构（不落库，给预览用）
    POST /chat-import/apply    统一结构 → 该 AI 与创建者的私信历史（via='import'）

解析与落库分开，是因为「先看清要导入什么」和「真的写进去」风险不同：
预览可以反复试，落库只在用户确认后做一次。
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.services.chat_import import fetchers, parsers
from app.services.chat_import.importer import import_transcript
from app.services.chat_import.schema import Transcript
from app.utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat-import", tags=["从聊天记录导入"])


class SourceSpec(BaseModel):
    """一段来源。type=url 需要抓取插件；text/file 直接解析。"""

    type: Literal["url", "text", "file"]
    value: str = Field(..., max_length=4_000_000)
    filename: str | None = Field(None, max_length=200)


class ResolveRequest(BaseModel):
    source: SourceSpec


class OrganizeRequest(BaseModel):
    transcript: Transcript


class ApplyRequest(BaseModel):
    agent_id: int
    transcript: Transcript
    # 智能整理里用户勾选保留的那部分（记忆 / 知识库）。
    # 提示词不在这里——它是创建表单的字段，由用户在创建前改定。
    memories: list[dict] = Field(default_factory=list)
    knowledge: list[dict] = Field(default_factory=list)


@router.get("")
async def describe(current_user: dict = Depends(get_current_user)):
    """前端据此决定「贴链接」这一格显不显示：没装插件就不装样子。"""
    return {"parsers": parsers.available(), "fetchers": fetchers.available()}


@router.post("/resolve")
async def resolve(
    req: ResolveRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """来源 → 统一结构预览。这一步不写任何东西。"""
    src = req.source
    payload = src.value
    filename = src.filename
    source_url = None

    if src.type == "url":
        source_url = src.value.strip()
        fetcher = fetchers.find(source_url)
        if fetcher is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="没有安装能抓这个链接的插件；请改用「粘贴全文」或上传导出的文件",
            )
        try:
            payload = await fetcher.fetch(source_url)
        except Exception as e:  # 抓取失败是常态（平台改版/限流），如实回给用户
            logger.warning("导入抓取失败 %s: %s", source_url, e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"抓取失败：{e}；可以改用「粘贴全文」或上传官方导出的文件",
            )
        filename = None

    if not payload.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="内容为空")

    try:
        transcript = parsers.parse(payload, filename=filename)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.exception("导入解析失败")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"解析失败：{e}")

    if source_url and not transcript.origin.source_url:
        transcript.origin.source_url = source_url

    return {
        "transcript": transcript.model_dump(mode="json"),
        "stats": transcript.stats(),
        "warnings": transcript.warnings,
        # 「初步摘要注入」的草稿：不调模型，用已知事实拼一句，前端可改。
        # 用户改完随 apply 一起回传（写在 transcript.summary 上）。
        "suggested_summary": transcript.suggested_summary(),
    }


@router.post("/organize")
async def organize_transcript(
    req: OrganizeRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """智能整理：对话 → 人设（prompt）+ 记忆 + 知识库 + 初摘要。

    用不了模型时**不报错**，回 200 + needs_config —— 「还没配」不是故障，
    是下一步要用户去做的事；前端据此弹一个对话框，带「去设置默认配置」的入口。
    这样所有「缺配置」的落点走同一套界面，不用每个调用点各自记得弹窗。
    """
    from app.services.chat_import.organize import OrganizeUnavailable, organize

    try:
        result = await organize(db, user_id=int(current_user["user_id"]), transcript=req.transcript)
    except OrganizeUnavailable as e:
        return {
            "ok": False,
            "needs_config": {"reason": e.reason, "chain": e.chain},
        }
    except Exception as e:
        logger.warning("智能整理失败: %s", e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"智能整理失败：{e}")

    return {"ok": True, **result}


@router.post("/apply")
async def apply(
    req: ApplyRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把这份对话写进该 AI 与创建者的私信历史。"""
    agent = (await db.execute(
        select(Agent).where(Agent.id == req.agent_id)
    )).scalars().first()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI 不存在")
    if int(agent.owner_id) != int(current_user["user_id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="只有这个 AI 的创建者能导入它的历史")

    try:
        result = await import_transcript(
            db,
            agent=agent,
            creator_user_id=int(current_user["user_id"]),
            transcript=req.transcript,
        )
        # 智能整理勾选的那部分（记忆 / 知识库）与历史同一个事务：
        # 要么一起落地，要么一条都不落，不留「历史进来了、设定丢了」的半截状态
        if req.memories or req.knowledge:
            from app.services.chat_import.apply_organize import apply as apply_organized
            result["organized"] = await apply_organized(
                db, agent=agent,
                organized={"memories": req.memories, "knowledge": req.knowledge},
            )
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        await db.rollback()
        logger.exception("导入写库失败")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="导入失败，没有写入任何消息")

    logger.info("聊天记录已导入：agent=%s msgs=%s", agent.id, result.get("imported"))
    return result
