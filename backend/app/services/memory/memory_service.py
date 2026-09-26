"""
记忆服务
自动检索相关记忆注入 prompt、自动存储关键信息
"""
import re
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import bindparam, text
from app.repositories.memory_repo import MemoryRepository, SQLAlchemyMemoryRepository

logger = logging.getLogger(__name__)


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyMemoryRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyMemoryRepository(db_or_repo)
    return db_or_repo


def merge_keyword_and_vector(
    keyword_results: list[dict],
    vector_results: list[dict],
    top_k: int,
) -> tuple[list[dict], str]:
    """
    关键词命中优先 + 向量补位合并（对齐 dsh-mneme 的搜索哲学）。

    - 关键词结果【总是】排最前：它们是用户查询的字面词，精确命中优先级最高
    - 向量结果填剩余槽位（按相似度排序），与关键词结果去重
    - 返回 (合并结果, mode)；mode 用于元信息展示：
      "keyword" = 仅关键词（向量不可用/失败/未启用）
      "vector"  = 关键词 + 向量补位（两者共存）

    设计说明：向量是"增强"而非"替代"——字面词命中不因语义分数略低而丢失。
    """
    merged: list[dict] = []
    seen: set[int] = set()

    # 1. 关键词命中优先（用户的字面词）
    for m in keyword_results:
        if len(merged) >= top_k:
            break
        if m["id"] not in seen:
            seen.add(m["id"])
            m["source"] = "text"
            # 关键词结果无向量分；若同一记忆有向量分，保留它（合并时回填）
            m.setdefault("similarity", None)
            merged.append(m)

    # 2. 向量补位（去重）
    for m in vector_results:
        if len(merged) >= top_k:
            break
        if m["id"] not in seen:
            seen.add(m["id"])
            m["source"] = "vector"
            merged.append(m)

    # 3. 同一条记忆既命中关键词又命中向量 → 回填真实向量分（O(n) dict 查找）
    vec_by_id = {m["id"]: m for m in vector_results}
    for m in merged:
        if m["source"] == "text" and m["id"] in vec_by_id:
            m["similarity"] = vec_by_id[m["id"]].get("similarity", m.get("similarity"))

    mode = "vector" if vector_results else "keyword"
    return merged, mode


def extract_keywords(query: str, max_parts: int = 8) -> list[str]:
    """
    中文友好的关键词提取（ngram 滑窗）。

    策略（修复中文无分词时整段 LIKE 失效的问题）：
    1. 按中英文标点/空格拆成片段
    2. 长度 ≤ 2 的短片段原样保留（英文词 / 双字中文词）
    3. 纯英文/数字/混合片段：不 ngram，原样保留（BAAI → BAAI，不做 BA/AA 碎片）
    4. 长中文片段：整段 + 2~3 字 ngram 滑窗（「化学掌握情况」→ 化学/掌握/情况）
    5. 去重、去单字噪音、截断到 max_parts

    无第三方依赖（不引 jieba），SQLite/PG 通用。
    """
    parts: list[str] = []
    tokens = re.split(r'[,，。！？、\s\n.!?;；：:()（）""''""【】\[\]]+', query)
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        if len(t) <= 2:
            # 短片段直接保留（英文词 / 双字中文词）
            if t not in parts:
                parts.append(t)
            continue
        if t.isascii():
            # 纯英文/数字/混合：本身就是词，不 ngram
            if t not in parts:
                parts.append(t)
            continue
        # 长中文片段：整段 + 2-gram 滑窗（3-gram 对 LIKE 召回贡献小，砍掉）
        if t not in parts:
            parts.append(t)
        for i in range(len(t) - 1):
            gram = t[i:i + 2]
            if gram not in parts:
                parts.append(gram)
    # 去单字噪音（单个中文字符命中价值低，且会拖慢 LIKE）
    filtered = [p for p in parts if len(p) >= 2 or p.isascii()]
    return filtered[:max_parts]


async def _text_search_memories(
    db: AsyncSession,
    agent_id: int,
    query: str,
    top_k: int = 5,
    group_id: int | None = None,
    scope: str | None = None,
    user_id: int | None = None,
    ai_type: str = "resonance",
) -> list[dict]:
    """
    文本关键词回退搜索（当 embedding 不可用或记忆向量为 NULL 时使用）。

    使用 LIKE 全文模糊匹配，支持中英文混合关键词。
    关键词提取：extract_keywords() — 标点拆分 + ngram 滑窗（中文友好）。

    scope 参数：
    - None：检索私有 + 群共享（auto-injection 用）
    - "private"：仅检索该 AI 的私有记忆
    - "group"：仅检索群共享记忆

    v0.1.3: user_id 过滤 — 通用/半通用 AI 仅检索该用户的记忆，
    共振 AI 检索 user_id IS NULL（全部记忆）。
    """
    db = _ensure_repo(db)
    # 提取关键词：标点拆分 + ngram 滑窗（中文友好）
    parts = extract_keywords(query)

    # 构建 LIKE 条件（取前 8 个关键词避免 SQL 过长）
    # 用 LOWER() 包裹实现大小写不敏感（PostgreSQL ILIKE 的通用替代，SQLite 亦兼容）
    conditions = []
    params: dict = {}
    for i, kw in enumerate(parts[:8]):
        param_key = f"kw{i}"
        conditions.append(
            f"(LOWER(rm.title) LIKE LOWER(:{param_key}) OR LOWER(dm.content) LIKE LOWER(:{param_key}))"
        )
        params[param_key] = f"%{kw}%"

    where_parts = ["(" + " OR ".join(conditions) + ")"]

    # v0.1.3: per-user 记忆隔离
    if ai_type == "resonance":
        where_parts.append("rm.user_id IS NULL")
    elif user_id is not None:
        where_parts.append("(rm.user_id = :user_id OR rm.user_id IS NULL)")
        params["user_id"] = user_id

    # 权限过滤
    if scope == "private":
        where_parts.append(
            "(rm.owner_type = 'ai' AND rm.owner_id = :agent_id AND rm.scope = 'private')"
        )
        params["agent_id"] = agent_id
    elif scope == "group":
        where_parts.append(
            "(rm.scope = 'group' AND rm.group_id = :group_id)"
        )
        params["group_id"] = group_id
    elif group_id:
        where_parts.append(
            "((rm.owner_type = 'ai' AND rm.owner_id = :agent_id AND rm.scope = 'private') "
            "OR (rm.scope = 'group' AND rm.group_id = :group_id))"
        )
        params["agent_id"] = agent_id
        params["group_id"] = group_id
    else:
        where_parts.append("(rm.owner_type = 'ai' AND rm.owner_id = :agent_id)")
        params["agent_id"] = agent_id

    where_clause = " AND ".join(where_parts)

    # 排序：标题命中优先（对齐 dsh-mneme），再按时间倒序
    # kw0 = 整段查询词，title LIKE 命中排最前（用户/AI 的原文词在标题里 = 高相关）
    sql = text(f"""
        SELECT rm.id, rm.title, rm.scope, 0.0 AS similarity, dm.content,
               rm.value_score, rm.last_touched_at, rm.last_touched_call
        FROM rough_memories rm
        LEFT JOIN detail_memories dm ON dm.rough_id = rm.id
        WHERE {where_clause}
        ORDER BY
            CASE WHEN LOWER(rm.title) LIKE LOWER(:kw0) THEN 0 ELSE 1 END,
            rm.created_at DESC
        LIMIT :top_k
    """)
    params["kw0"] = f"%{parts[0]}%"
    params["top_k"] = top_k

    result = await db.execute(sql, params)
    memories = []
    for row in result:
        memories.append({
            "id": row.id,
            "title": row.title,
            "scope": row.scope,
            "similarity": 0.0,  # 文本匹配，无向量相似度
            "content": row.content or "",
            "source": "text",
            # 有效权重过滤要用到的三列（口径见 utils/pure/memory_weight.py）
            "value_score": row.value_score,
            "last_touched_at": row.last_touched_at,
            "last_touched_call": row.last_touched_call,
        })

    if memories:
        logger.info(f"📝 文本回退搜索为 AI agent_id={agent_id} 找到 {len(memories)} 条记忆")

    return memories


def _is_injectable(mem: dict, call_count: int, now: datetime) -> bool:
    """这条记忆此刻还在有效期吗——自动注入与自动召回只认「inject」档。

    口径只有一处（utils/pure/memory_weight.py），这里不再写第二套判断。
    """
    from app.utils.pure.memory_weight import disposition_at

    return disposition_at(
        mem.get("value_score") or 3,
        mem.get("last_touched_at"),
        mem.get("last_touched_call"),
        now,
        call_count,
    ) == "inject"


async def _touch_memories(db, ids: list[int], call_count: int, now: datetime) -> None:
    """把刚被想起的条目的时间基准推到当下：设定权值不动，只是重新计时。

    刷新失败不该影响本轮检索（下一轮再更新即可），所以整段吞异常。
    """
    if not ids:
        return
    try:
        stmt = text(
            "UPDATE rough_memories SET last_touched_at = :now, last_touched_call = :call "
            "WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        await db.execute(stmt, {"now": now, "call": call_count, "ids": list(ids)})
    except Exception as e:
        logger.warning(f"记忆时间基准刷新失败（非致命）: {e}")


async def recall_relevant_memories(
    db: AsyncSession,
    agent_id: int,
    query: str,
    api_base_url: str = "https://api.deepseek.com",
    api_key: str | None = None,
    top_k: int = 5,
    similarity_threshold: float = 0.35,
    group_id: int | None = None,
    user_id: int | None = None,
    ai_type: str = "resonance",
    call_count: int = 0,
    only_injectable: bool = True,
) -> list[dict]:
    """
    检索与当前对话相关的记忆（关键词优先 + 向量补位，对齐 dsh-mneme）。

    检索范围：
    - 该 AI 的私有记忆（scope='private'）
    - 群共享记忆（scope='group'，群内任何 AI 存储的）

    策略（v0.1.9 对齐 dsh-mneme 合并哲学）：
    1. 关键词命中【总是】计算（ILIKE，可命中 embedding=NULL 的记忆）→ 排最前
    2. 向量搜索可用时，结果去重后补位剩余槽位（按相似度排序）
    3. 向量不可用/失败 → 仅关键词结果（无感降级）

    v0.1.3: user_id + ai_type 参数支持 per-user 记忆隔离。
    共振 AI 检索所有记忆（user_id IS NULL），
    通用/半通用 AI 仅检索该用户的记忆。

    v1.1: call_count + only_injectable 决定要不要做有效权重过滤——
    自动注入只收还在有效期的条目；显式检索（recall_memory 工具）传 False 看全部。

    返回: [{id, title, content, scope, similarity, source, value_score, ...}]
    """
    db = _ensure_repo(db)
    from app.utils.embedding import get_embedding
    from app.db_providers import get_provider

    provider = get_provider()

    # 自动注入先多取再筛：已退场的比例不可预知，按 top_k 取满会在筛后不足
    fetch_k = top_k * 3 if only_injectable else top_k

    # ═══ 第一轮：关键词搜索（总是执行，字面词命中优先） ═══
    keyword_memories = await _text_search_memories(
        db, agent_id, query, top_k=fetch_k, group_id=group_id,
        user_id=user_id, ai_type=ai_type,
    )

    # ═══ 第二轮：向量补位（仅当后端支持 SQL 向量运算） ═══
    vector_memories: list[dict] = []
    if provider.supports_sql_vector_search:
        try:
            query_embedding = await get_embedding(query, api_base_url=api_base_url, api_key=api_key)
            embedding_str = f"[{','.join(map(str, query_embedding))}]"

            # v0.1.3: 构建 user_id 过滤条件
            if ai_type == "resonance":
                user_filter = "AND rm.user_id IS NULL"
            elif user_id is not None:
                user_filter = "AND (rm.user_id = :user_id OR rm.user_id IS NULL)"
            else:
                user_filter = ""

            if group_id:
                sql = text(f"""
                    SELECT rm.id, rm.title, rm.scope,
                            {provider.vector_similarity_expr("rm.embedding", "embedding")} AS similarity,
                           dm.content, rm.value_score, rm.last_touched_at, rm.last_touched_call
                    FROM rough_memories rm
                    LEFT JOIN detail_memories dm ON dm.rough_id = rm.id
                    WHERE rm.embedding IS NOT NULL
                      AND {provider.vector_similarity_expr("rm.embedding", "embedding")} > :threshold
                      {user_filter}
                      AND (
                          (rm.owner_type = 'ai' AND rm.owner_id = :agent_id AND rm.scope = 'private')
                          OR
                          (rm.scope = 'group' AND rm.group_id = :group_id)
                      )
                    ORDER BY {provider.vector_similarity_expr("rm.embedding", "embedding")} DESC
                    LIMIT :top_k
                """)
                params = {
                    "embedding": embedding_str,
                    "agent_id": agent_id,
                    "group_id": group_id,
                    "threshold": similarity_threshold,
                    "top_k": fetch_k,
                }
                if user_id is not None and ai_type != "resonance":
                    params["user_id"] = user_id
                result = await db.execute(sql, params)
            else:
                sql = text(f"""
                    SELECT rm.id, rm.title, rm.scope,
                            {provider.vector_similarity_expr("rm.embedding", "embedding")} AS similarity,
                           dm.content, rm.value_score, rm.last_touched_at, rm.last_touched_call
                    FROM rough_memories rm
                    LEFT JOIN detail_memories dm ON dm.rough_id = rm.id
                    WHERE rm.owner_type = 'ai'
                      AND rm.owner_id = :agent_id
                      {user_filter}
                      AND rm.embedding IS NOT NULL
                      AND {provider.vector_similarity_expr("rm.embedding", "embedding")} > :threshold
                    ORDER BY {provider.vector_similarity_expr("rm.embedding", "embedding")} DESC
                    LIMIT :top_k
                """)
                params = {
                    "embedding": embedding_str,
                    "agent_id": agent_id,
                    "threshold": similarity_threshold,
                    "top_k": fetch_k,
                }
                if user_id is not None and ai_type != "resonance":
                    params["user_id"] = user_id
                result = await db.execute(sql, params)

            for row in result:
                vector_memories.append({
                    "id": row.id,
                    "title": row.title,
                    "scope": row.scope,
                    "similarity": round(float(row.similarity), 4),
                    "content": row.content or "",
                    "source": "vector",
                    "value_score": row.value_score,
                    "last_touched_at": row.last_touched_at,
                    "last_touched_call": row.last_touched_call,
                })

            if vector_memories:
                logger.info(f"🔍 向量补位为 AI agent_id={agent_id} 找到 {len(vector_memories)} 条相关记忆")

        except Exception as e:
            logger.warning(f"记忆检索向量化失败（仅用关键词结果）: {e}")

    # ═══ 合并：关键词优先 + 向量补位 ═══
    memories, _mode = merge_keyword_and_vector(keyword_memories, vector_memories, fetch_k)

    # 有效权重过滤：自动注入只收「inject」档，筛完再截回 top_k
    from app.utils.pure.timeutil import utc_now

    now = utc_now()
    if only_injectable:
        memories = [m for m in memories if _is_injectable(m, call_count, now)][:top_k]

    # 被想起过就重新计时（设定权值不动）
    await _touch_memories(db, [m["id"] for m in memories], call_count, now)
    return memories


async def auto_store_memory(
    db: AsyncSession,
    agent_id: int,
    group_id: int,
    title: str,
    content: str,
    scope: str = "private",
    api_base_url: str = "https://api.deepseek.com",
    api_key: str | None = None,
) -> dict:
    """
    自动存储一条记忆（封装 embedding + rough + detail 写入）。

    返回: {"success": bool, "rough_id": int|None}
    """
    db = _ensure_repo(db)
    from app.models.memory import RoughMemory, DetailMemory
    from app.utils.embedding import get_embedding

    # 向量化标题
    try:
        embedding = await get_embedding(title, api_base_url=api_base_url, api_key=api_key)
    except Exception as e:
        logger.warning(f"自动记忆向量化失败: {e}")
        embedding = None

    rough = RoughMemory(
        owner_type="ai",
        owner_id=agent_id,
        title=title,
        embedding=embedding,
        scope=scope,
        group_id=group_id if scope == "group" else None,
    )
    db.add(rough)
    await db.flush()

    detail = DetailMemory(
        rough_id=rough.id,
        content=content,
    )
    db.add(detail)
    await db.flush()

    logger.info(f"💾 AI agent_id={agent_id} 自动存储记忆: {title}")
    return {"success": True, "rough_id": rough.id, "title": title}


async def auto_extract_key_facts(
    db: AsyncSession,
    agent_id: int,
    group_id: int,
    content: str,
    sender_name: str = "",
    api_base_url: str = "https://api.deepseek.com",
    api_key: str | None = None,
) -> bool:
    """
    从消息内容中自动提取关键信息并加入记忆缓冲区（异步批量落盘）。

    触发条件（满足任一）：
    - 明确记忆指令：「记住」「记下」「别忘了」「提醒我」
    - 偏好表达：「我喜欢」「我不喜欢」「我讨厌」「我偏好」
    - 决定声明：「决定了」「就这样」「定下来」「确认」
    - 个人信息：「我是」「我的」「我叫」
    - 任务分配：「你来负责」「交给你」「你的任务是」

    返回: True 如果入队了记忆，False 如果跳过。
    """
    db = _ensure_repo(db)
    import re
    from app.services.memory.memory_buffer import enqueue_memory
    from app.models.agent import Agent
    from sqlalchemy import select as _sel2

    triggers = [
        (r'(记住|记下|别忘了|提醒我|记住这个)', "显式记忆请求"),
        (r'我(喜欢|不喜欢|讨厌|偏好|爱|恨)', "偏好表达"),
        (r'(决定了|就这样|定下来|确认了|说定了)', "决定"),
        (r'我(是|叫|的|在|做|从事)', "个人信息"),
        (r'(你来负责|交给你|你的任务是|你负责)', "任务分配"),
        (r'(目标|计划是|下一步|里程碑)', "目标/计划"),
    ]

    triggered_category = None
    for pattern, category in triggers:
        if re.search(pattern, content):
            triggered_category = category
            break

    if not triggered_category:
        return False

    # 生成标题（取内容前 60 字符）
    clean_content = content.strip()
    title = clean_content[:60]
    if len(clean_content) > 60:
        title += "..."

    # 获取 AI 类型
    ai_type = "resonance"
    try:
        agent_row = await db.execute(_sel2(Agent).where(Agent.id == agent_id))
        agent_obj = agent_row.scalar_one_or_none()
        if agent_obj:
            ai_type = agent_obj.ai_type or "resonance"
    except Exception:
        pass

    # 自动提取的偏好/简短信息 → low_value
    low_value = triggered_category in ("偏好表达",)

    try:
        await enqueue_memory(
            agent_id=agent_id,
            title=f"[{triggered_category}] {title}",
            content=clean_content[:500],
            scope="private",
            group_id=group_id,
            api_base_url=api_base_url,
            api_key=api_key,
            ai_type=ai_type,
            source="auto_extract",
            low_value=low_value,
        )
        logger.info(f"📝 自动提取记忆入队: [{triggered_category}] {title[:50]} ({'低价值' if low_value else '普通'})")
        return True
    except Exception as e:
        logger.warning(f"自动提取记忆入队失败: {e}")
        return False


def format_memories_for_prompt(memories: list[dict]) -> str:
    """
    将检索到的记忆格式化为可注入 prompt 的文本。

    返回: 格式化后的记忆文本，无记忆时返回空字符串。
    """
    if not memories:
        return ""

    lines = ["## 相关记忆（来自你的长期记忆库）\n"]
    for i, mem in enumerate(memories, 1):
        sim = mem.get("similarity")
        if sim is not None and sim > 0:
            sim_text = f"（相似度: {sim}）"
        elif mem.get("source") == "text":
            sim_text = "（关键词匹配）"
        else:
            sim_text = ""
        lines.append(f"{i}. **{mem['title']}** {sim_text}".rstrip())
        if mem.get("content"):
            # 截断过长内容
            content = mem["content"]
            if len(content) > 300:
                content = content[:300] + "..."
            lines.append(f"   {content}")
        lines.append("")

    # ⚠️ 字符串内如需引用中文名词，用直角引号「」或转义 \"，严禁直接用 ""——
    #    Python 会把第二个 " 当作字符串终止符，导致 SyntaxError 全局炸（worker 崩溃、AI 不回复）
    lines.append("请参考以上记忆来个性化你的回复，但不要刻意提及「记忆库」。\n")
    return "\n".join(lines)
