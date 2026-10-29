"""
记忆系统路由
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.memory import StoreMemoryRequest, RecallMemoryRequest, RoughMemoryResponse, DetailMemoryResponse
from app.db_providers import get_provider
from app.utils.auth import get_current_user

router = APIRouter(prefix="/memories", tags=["记忆"])

#: 无 SQL 向量运算的后端上，Python 侧算余弦的候选上限（记忆量级远小于此）
_PY_VECTOR_CANDIDATES = 500


async def _python_vector_search(
    db: AsyncSession, query_embedding: list[float], scope: str,
    user_id: int, group_id: int | None, top_k: int,
) -> list[dict]:
    """向量检索降级：候选取回后在 Python 侧算余弦。

    与上面 pgvector 分支的过滤条件保持一致（scope + AI 私有 / 群共享）。
    """
    from sqlalchemy import and_, or_, select
    from app.models.memory import RoughMemory
    from app.services.memory.vector_fallback import score_by_cosine

    group_cond = RoughMemory.owner_type == "group"
    if group_id is not None:
        group_cond = and_(group_cond, RoughMemory.group_id == group_id)
    stmt = (
        select(
            RoughMemory.id, RoughMemory.title,
            RoughMemory.embedding, RoughMemory.created_at,
        )
        .where(
            RoughMemory.scope == scope,
            RoughMemory.embedding.is_not(None),
            or_(
                and_(RoughMemory.owner_type == "ai", RoughMemory.owner_id == user_id),
                group_cond,
            ),
        )
        .order_by(RoughMemory.created_at.desc())
        .limit(_PY_VECTOR_CANDIDATES)
    )
    rows = (await db.execute(stmt)).all()
    scored = score_by_cosine(rows, [row.embedding for row in rows], query_embedding, top_k)
    return [
        {
            "id": row.id,
            "title": row.title,
            "similarity": round(float(sim), 4),
            "created_at": str(row.created_at) if row.created_at else None,
        }
        for sim, row in scored
    ]


@router.post("/rough", status_code=status.HTTP_201_CREATED)
async def store_rough_memory(
    req: StoreMemoryRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """存储粗略记忆（含向量化标题）"""
    from sqlalchemy import select
    from app.models.memory import RoughMemory, DetailMemory
    from app.utils.embedding import get_embedding
    from app.utils.crypto import decrypt_api_key
    from app.models.user import User

    # 获取用户的 API Key
    user_result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = user_result.scalar_one_or_none()
    api_key = decrypt_api_key(user.api_key_encrypted) if user and user.api_key_encrypted else None

    # 向量化标题
    try:
        embedding = await get_embedding(req.title, api_key=api_key)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Embedding 失败: {str(e)}",
        )

    # 存储粗略记忆
    rough = RoughMemory(
        owner_type="ai" if req.scope == "private" else "group",
        owner_id=current_user["user_id"],
        title=req.title,
        embedding=embedding,
        scope=req.scope,
        group_id=req.group_id,
    )
    db.add(rough)
    await db.flush()
    await db.refresh(rough)

    # 存储详细记忆
    detail = DetailMemory(
        rough_id=rough.id,
        content=req.content,
    )
    db.add(detail)
    await db.flush()

    return {"rough_id": rough.id, "detail_id": detail.id, "title": req.title}


@router.get("/search", response_model=list[RoughMemoryResponse])
async def search_memories(
    query: str = Query(..., description="搜索查询"),
    scope: str = Query("private", description="private | group"),
    group_id: int | None = Query(None),
    top_k: int = Query(5, ge=1, le=20),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """检索记忆（向量相似度搜索）"""
    from sqlalchemy import select, text
    from app.models.memory import RoughMemory
    from app.utils.embedding import get_embedding
    from app.utils.crypto import decrypt_api_key
    from app.models.user import User

    # 获取用户的 API Key
    user_result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = user_result.scalar_one_or_none()
    api_key = decrypt_api_key(user.api_key_encrypted) if user and user.api_key_encrypted else None

    # 向量化查询
    try:
        query_embedding = await get_embedding(query, api_key=api_key)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Embedding 失败: {str(e)}",
        )

    memories = []
    if get_provider().supports_sql_vector_search:
        # pgvector 余弦相似度检索：使用原始 SQL 以支持 vector 操作符
        embedding_str = f"[{','.join(map(str, query_embedding))}]"

        sql = text("""
            SELECT id, title, 1 - (embedding <=> :embedding) AS similarity, created_at
            FROM rough_memories
            WHERE scope = :scope
              AND (owner_type = 'ai' AND owner_id = :user_id
                   OR owner_type = 'group' AND (:group_id IS NULL OR group_id = :group_id))
              AND embedding IS NOT NULL
            ORDER BY embedding <=> :embedding
            LIMIT :top_k
        """)

        result = await db.execute(sql, {
            "embedding": embedding_str,
            "scope": scope,
            "user_id": current_user["user_id"],
            "group_id": group_id,
            "top_k": top_k,
        })

        for row in result:
            memories.append({
                "id": row.id,
                "title": row.title,
                "similarity": round(float(row.similarity), 4) if row.similarity else None,
                "created_at": str(row.created_at) if row.created_at else None,
            })
    else:
        # SQLite 安装包等后端没有 SQL 内向量运算（db_providers/sqlite.py 的 P0），
        # 拼 `<=>` 会直接报错，改走「取候选 → Python 侧算余弦」。
        memories = await _python_vector_search(
            db, query_embedding,
            scope=scope, user_id=current_user["user_id"],
            group_id=group_id, top_k=top_k,
        )

    return memories


@router.get("/detail/{rough_id}", response_model=DetailMemoryResponse)
async def get_memory_detail(
    rough_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取详细记忆内容"""
    from sqlalchemy import select
    from app.models.memory import DetailMemory, RoughMemory

    # 检查权限
    rough_result = await db.execute(select(RoughMemory).where(RoughMemory.id == rough_id))
    rough = rough_result.scalar_one_or_none()
    if rough is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在")
    if rough.owner_type == "ai" and rough.owner_id != current_user["user_id"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问")

    detail_result = await db.execute(
        select(DetailMemory).where(DetailMemory.rough_id == rough_id)
    )
    detail = detail_result.scalar_one_or_none()
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="详细记忆不存在")

    return {
        "id": detail.id,
        "rough_id": detail.rough_id,
        "content": detail.content,
        "created_at": str(detail.created_at) if detail.created_at else None,
    }