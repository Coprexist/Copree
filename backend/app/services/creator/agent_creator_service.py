"""创建 AI 的辅助填写助手 —— 一轮对话推进一次创建草稿。

三件事都走现成设施，不另起炉灶：
- 表单：草稿里的 form 就是前端 AgentForm 的原样快照（camelCase，不翻译），
  助手用 set_agent_form 工具往里写 patch，前端收到 form 事件直接 setName/setTemperature。
- 请求体：整段对话交 save_conversation_log 落 ai_conversation_logs
  （conversation_type="creator"，session_id=草稿 id）——与群视界机器人同一个记账口，
  usage_daily 里自然有一笔（agent_id=0、user_id=创建者），个人 API 用量页可见。
- 直播：单次 POST 直接吐 SSE。不做世界那套「发起 + 订阅 / 重连」：本流程一轮几秒，
  断了重发就是，多一张订阅表不值。

工具集只有两个：主站既有的 web_search（唯一来源）+ 本文件定义的填表工具。
填表做成工具调用而不是让模型吐 JSON 文本：落库前先按字段契约收敛，脏值进不了表单。
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_creation import AgentCreationDraft
from app.models.conversation_log import ConversationLog
from app.utils.pure.llm_endpoint import chat_completions_url
from app.utils.pure.llm_usage import normalize_usage

logger = logging.getLogger(__name__)

# ai_conversation_logs.conversation_type 只有 10 字符，这里不能写 "agent_creator"
CREATOR_CONVERSATION_TYPE = "creator"
MAX_TOOL_ROUNDS = 6
HISTORY_KEEP = 20
MAX_TOKENS = 4000

# 档位与子档的取值在 frontend/src/components/agent-create/presets.ts（唯一来源）；
# 档位另与 schemas/agent.py 的 config_profile 同值。这边只声明同名 key，不复制其中的数值。
PRESET_KEYS = ("chat", "immersive", "digital_life")
SUB_IDS = (
    "chat_low_power", "chat_balanced", "chat_private",
    "immersive_group_admin", "immersive_roleplay", "immersive_analyst",
    "digital_thinker", "digital_social", "digital_guardian",
)

# ── 可填字段契约 ──
# 一份表同时生三样东西：给模型的工具 JSON Schema、给模型的字段说明、落库前的收敛规则。
# 字段名必须是前端 AgentForm 的名字（camelCase）——patch 原样进前端 state，中间不做翻译。
# 数值区间与枚举值对着 schemas/agent.py 的 AgentCreateRequest 抄。
FIELD_CATALOG: tuple[dict, ...] = (
    {"name": "name", "kind": "string", "desc": "AI 名字，1~50 字"},
    {"name": "systemPrompt", "kind": "string", "desc": "系统提示词（50~500 字）：性格、语气、知识背景、行为方式"},
    {"name": "bio", "kind": "string", "desc": "个人简介（≤500 字），展示在资料卡"},
    {"name": "statusText", "kind": "string", "desc": "个性状态（中文≤10 字，英文≤30 字符）"},
    {"name": "aiType", "kind": "enum", "desc": "AI 类型", "values": ("general", "semi_general", "resonance")},
    {"name": "temperature", "kind": "number", "desc": "随机性，越高越发散", "range": (0.0, 2.0)},
    {"name": "topP", "kind": "number", "desc": "核采样", "range": (0.0, 1.0)},
    {"name": "presencePenalty", "kind": "number", "desc": "话题新鲜度，越高越爱换话题", "range": (-2.0, 2.0)},
    {"name": "frequencyPenalty", "kind": "number", "desc": "重复抑制，越高越少复读", "range": (-2.0, 2.0)},
    {"name": "thinkingEnabled", "kind": "bool", "desc": "深度思考（推理 token 单独计费）"},
    {"name": "hideAiIdentity", "kind": "bool", "desc": "对用户隐藏 AI 身份"},
    {"name": "maxToolRounds", "kind": "int", "desc": "单次回复最大工具轮次", "range": (1, 20)},
    {"name": "alarmMaxToolRounds", "kind": "int", "desc": "闹钟/心跳最大工具轮次", "range": (1, 30)},
    {"name": "maxAlarms", "kind": "int", "desc": "最多活跃闹钟数", "range": (1, 50)},
    {"name": "forceAlarmOnEnd", "kind": "bool", "desc": "对话结束时强制设定闹钟"},
    {"name": "delayReplyEnabled", "kind": "bool", "desc": "延迟回复（像真人一样隔一会儿再回）"},
    {"name": "isAiEditable", "kind": "bool", "desc": "允许 AI 自修改配置"},
    {"name": "reminderGrace", "kind": "enum", "desc": "系统提醒额外轮次", "values": ("every_time", "once", "off")},
    {"name": "memoryLoadMode", "kind": "enum", "desc": "记忆加载模式",
     "values": ("index_only", "index_plus_recent", "index_plus_semantic")},
    {"name": "memoryRecentCount", "kind": "int", "desc": "加载最近 N 个记忆文件", "range": (0, 50)},
    {"name": "memorySharedScope", "kind": "enum", "desc": "共享记忆范围",
     "values": ("private_only", "private_plus_shared_by_user", "private_plus_shared_all")},
    {"name": "allowFriendRequests", "kind": "bool", "desc": "允许接收好友申请"},
    {"name": "autoRespondFriendRequest", "kind": "bool", "desc": "收到好友申请自动响应"},
    {"name": "discoverable", "kind": "bool", "desc": "允许他人发现与查找"},
    {"name": "allowOthersChat", "kind": "bool", "desc": "允许非主人触发对话"},
    {"name": "othersChatMode", "kind": "enum", "desc": "允许时的模式", "values": ("unlimited", "quota")},
    {"name": "othersChatQuota", "kind": "int", "desc": "非主人触发配额上限", "range": (1, 9999)},
    {"name": "disallowMode", "kind": "enum", "desc": "禁止时的模式", "values": ("strict", "own_key")},
    {"name": "autoDndThreshold", "kind": "int", "desc": "自动免打扰阈值（意愿评分低于它进 DND）", "range": (0, 100)},
    {"name": "autoDndDuration", "kind": "int", "desc": "自动免打扰时长（分钟）", "range": (1, 1440)},
)
FIELD_SPEC = {f["name"]: f for f in FIELD_CATALOG}

_KIND_TO_JSON = {"string": "string", "number": "number", "int": "integer", "bool": "boolean", "enum": "string"}

TOOL_LABELS = {"web_search": "网页搜索", "set_agent_form": "填写表单"}


# ── 提示词与工具定义 ──

def field_manual() -> str:
    """给模型的字段说明（与工具 Schema 同源，改契约只动 FIELD_CATALOG）"""
    lines = []
    for f in FIELD_CATALOG:
        extra = ""
        if f.get("values"):
            extra = "，可选：" + "/".join(f["values"])
        elif f.get("range"):
            extra = f"，范围 {f['range'][0]}~{f['range'][1]}"
        lines.append(f"- {f['name']}（{f['kind']}）：{f['desc']}{extra}")
    return "\n".join(lines)


def build_system_prompt(form: dict, preset: str | None = None, sub: str | None = None) -> str:
    """每轮重算：当前表单快照进提示词，助手才不会覆盖用户自己改过的字段"""
    snapshot = json.dumps(form or {}, ensure_ascii=False)
    picked = f"当前档位：{preset or '未选'}" + (f" / 子档：{sub}" if sub else "")
    return (
        "你是「AI 创建助手」：帮用户把想创建的居民 AI 变成一张填好的创建表单。\n\n"
        "工作方式：\n"
        "1. 用户只给了大概想法时，先用一两句话问清最要紧的几点（角色定位、说话风格、用来做什么），别一次问一长串。\n"
        "2. 需要外部资料（真实人物、专业术语、当前事件、开源项目）时用 web_search 联网查，不要凭记忆编。"
        "检索式要面向语料：专名照抄、中英各来一条。\n"
        "3. 拿定主意后用 set_agent_form 写表单。可以先给 preset（档位）和 sub（子档）定基调，再用 fields 微调。\n"
        "4. 每写一批就回一句人话说明填了什么、为什么这么填；不要把 JSON 或字段名堆给用户。\n"
        "5. 没把握的字段留空（用户可以在弹窗里自己填），别瞎填占位。\n"
        "6. 用户改过的字段以最新快照为准，不要用你的旧判断覆盖回去。\n\n"
        f"{picked}\n"
        f"当前表单快照：{snapshot}\n\n"
        "可填字段：\n" + field_manual()
    )


def fill_tool() -> dict:
    """填表工具定义：preset/sub 定档 + fields 微调"""
    props: dict = {}
    for f in FIELD_CATALOG:
        schema: dict = {"type": _KIND_TO_JSON[f["kind"]], "description": f["desc"]}
        if f.get("values"):
            schema["enum"] = list(f["values"])
        props[f["name"]] = schema
    return {
        "type": "function",
        "function": {
            "name": "set_agent_form",
            "description": (
                "把填写好的内容写进用户的创建表单（立即生效，用户看得见）。"
                "preset/sub 先定档位与子档，fields 里只放你已经定了的字段——没提到的保持不动。"
                "同一轮可以多次调用：先定档再微调。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "preset": {"type": "string", "enum": list(PRESET_KEYS), "description": "档位（聊手/沉浸/数字生命）"},
                    "sub": {"type": "string", "enum": list(SUB_IDS), "description": "子档 id（对应所选档位）"},
                    "fields": {"type": "object", "properties": props, "description": "要写入的字段"},
                },
            },
        },
    }


def creator_tools() -> list[dict]:
    """助手可用的工具：主站 web_search（唯一来源）+ 填表。

    世界侧那份 web_search 也是同一份实现的薄壳；这里直接用主站插件，
    免得再抄一遍参数与文案。段（segment）是平台内部分组，不发给模型。
    """
    from app.tools.file_operations.web_search import WebSearch

    search = WebSearch.to_definition()
    search.pop("segment", None)
    return [search, fill_tool()]


# ── 表单一侧 ──

def clean_fields(fields: dict | None) -> dict:
    """按契约收敛模型给的值：类型不对就丢，数值钳到区间，枚举不认就丢。

    宁可少填一个字段，也不能把 "0.8" 或越界值写进快照——前端拿的就是这份快照，
    脏值会一路带到创建请求体里。unknown key 直接忽略。
    """
    clean: dict = {}
    for key, value in (fields or {}).items():
        spec = FIELD_SPEC.get(key)
        if spec is None:
            continue
        kind = spec["kind"]
        if kind == "bool":
            if isinstance(value, bool):
                clean[key] = value
        elif kind == "string":
            if isinstance(value, str) and value.strip():
                clean[key] = value.strip()
        elif kind in ("int", "number"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            lo, hi = spec.get("range", (None, None))
            num = int(value) if kind == "int" else float(value)
            if lo is not None:
                num = max(lo, min(hi, num))
            clean[key] = num
        elif kind == "enum":
            if value in spec["values"]:
                clean[key] = value
    return clean


def merge_form(form: dict, fields: dict) -> dict:
    return {**(form or {}), **clean_fields(fields)}


# ── 草稿 CRUD ──

def draft_to_dict(d: AgentCreationDraft) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "form": d.form or {},
        "preset": d.preset,
        "sub": d.sub,
        "status": d.status,
        "agent_id": d.agent_id,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


async def list_drafts(db: AsyncSession, user_id: int, limit: int = 30) -> list[AgentCreationDraft]:
    """未完成的排前面，同状态按最近动过排"""
    rows = (await db.execute(
        select(AgentCreationDraft)
        .where(AgentCreationDraft.user_id == user_id, AgentCreationDraft.status != "abandoned")
        .order_by(AgentCreationDraft.status.desc(), AgentCreationDraft.updated_at.desc())
        .limit(limit)
    )).scalars().all()
    return list(rows)


async def create_draft(db: AsyncSession, user_id: int) -> AgentCreationDraft:
    draft = AgentCreationDraft(user_id=user_id, title="", form={}, status="open")
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def get_draft(db: AsyncSession, user_id: int, draft_id: int) -> AgentCreationDraft | None:
    return (await db.execute(
        select(AgentCreationDraft).where(
            AgentCreationDraft.id == draft_id, AgentCreationDraft.user_id == user_id
        )
    )).scalars().first()


async def update_draft(db: AsyncSession, draft: AgentCreationDraft, **fields) -> AgentCreationDraft:
    for key, value in fields.items():
        if value is not None and hasattr(draft, key):
            setattr(draft, key, value)
    await db.commit()
    await db.refresh(draft)
    return draft


async def delete_draft(db: AsyncSession, draft: AgentCreationDraft) -> None:
    await db.delete(draft)
    await db.commit()


async def load_transcript(db: AsyncSession, draft_id: int) -> list[dict]:
    """这段创建的最新请求体——每轮存的是整段快照，取最后一条即可"""
    row = (await db.execute(
        select(ConversationLog)
        .where(
            ConversationLog.conversation_type == CREATOR_CONVERSATION_TYPE,
            ConversationLog.session_id == str(draft_id),
        )
        .order_by(ConversationLog.created_at.desc(), ConversationLog.id.desc())
        .limit(1)
    )).scalars().first()
    if row is None:
        return []
    return visible_messages(row.messages or [])


def visible_messages(messages: list[dict]) -> list[dict]:
    """只留能进上下文的轮次：system 每轮按当前快照重算，工具轮是过程不重放"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "user" and m.get("content"):
            out.append({"role": "user", "content": m["content"]})
        elif role == "assistant" and m.get("content") and not m.get("tool_calls"):
            out.append({"role": "assistant", "content": m["content"]})
    return out[-HISTORY_KEEP:]


# ── 一轮对话 ──

def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _merge_usage(total: dict, usage: dict | None) -> dict:
    if not usage:
        return total
    merged = dict(total)
    for key, value in normalize_usage(usage).items():
        if isinstance(value, (int, float)):
            merged[key] = merged.get(key, 0) + value
    # api_calls：世界侧也是「每次模型调用记 1 次」——内容域的调用数只有这一个口径
    merged["api_calls"] = merged.get("api_calls", 0) + 1
    return merged


async def _stream_once(model: str, api_base: str, api_key: str | None,
                       messages: list, tools: list, out: dict) -> AsyncIterator[str]:
    """单次流式调用：逐 chunk 吐 SSE 事件，完整结果写回 out（正文/工具调用/用量）"""
    import httpx

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": MAX_TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
        "tools": tools,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    result: dict = {"content": "", "reasoning": "", "tool_calls": [], "usage": None}
    out.clear()
    out.update(result)
    acc: dict = {}
    index_to_id: dict = {}
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", chat_completions_url(api_base), json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    err = (await resp.aread()).decode(errors="replace")[:300]
                    yield _sse({"type": "error", "message": f"模型接口 {resp.status_code}：{err}"})
                    return
                buffer = ""
                done = False
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        pos = buffer.find("\n")
                        line, buffer = buffer[:pos], buffer[pos + 1:]
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw == "[DONE]":
                            done = True
                            break
                        try:
                            j = json.loads(raw)
                        except json.JSONDecodeError:
                            buffer = line + "\n" + buffer
                            break
                        # usage 块可能带空 choices，收用量不看 choices
                        if j.get("usage"):
                            result["usage"] = normalize_usage(j["usage"])
                        choices = j.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        if delta.get("reasoning_content"):
                            result["reasoning"] += delta["reasoning_content"]
                            yield _sse({"type": "reasoning", "delta": delta["reasoning_content"]})
                        if delta.get("content"):
                            result["content"] += delta["content"]
                            yield _sse({"type": "text", "delta": delta["content"]})
                        for item in delta.get("tool_calls") or []:
                            # 分片规则沿用世界侧同一套：id 主 key，index 桥（arguments 分片常不带 id）
                            cid = item.get("id") or ""
                            idx = item.get("index", 0)
                            key = cid or index_to_id.get(idx) or f"idx_{idx}"
                            slot = acc.setdefault(key, {"id": "", "name": "", "arguments": ""})
                            if cid:
                                slot["id"] = cid
                                index_to_id[idx] = cid
                            fn = item.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
                    if done:
                        break
    except Exception as e:
        logger.warning(f"创建助手流式调用异常: {e}")
        yield _sse({"type": "error", "message": str(e)[:200]})
        return

    result["tool_calls"] = list(acc.values())
    out.clear()
    out.update(result)


async def _run_tool(db: AsyncSession, draft: AgentCreationDraft, name: str, args: dict) -> tuple[str, dict | None]:
    """执行一次工具，返回 (给模型的文本结果, 额外要推给前端的事件)"""
    if name == "set_agent_form":
        fields = clean_fields(args.get("fields"))
        preset = args.get("preset") if args.get("preset") in PRESET_KEYS else None
        sub = args.get("sub") if args.get("sub") in SUB_IDS else None
        form = merge_form(draft.form or {}, fields)
        await update_draft(db, draft, form=form, preset=preset or draft.preset, sub=sub or draft.sub)
        applied = sorted(set(fields) | ({k for k in ("preset", "sub") if (preset or sub)}))
        event = {"type": "form", "patch": fields, "form": form, "preset": preset, "sub": sub}
        return ("已写入：" + ("、".join(applied) if applied else "无有效字段")), event

    if name == "web_search":
        from app.tools.file_operations.web_search import WebSearch
        try:
            result = await WebSearch().execute(db, 0, None, args, {})
        except (ValueError, TypeError) as e:
            return f"搜索失败：{e}", {"type": "tool_result", "ok": False, "summary": str(e)[:80]}
        results = (result or {}).get("results") or []
        if not (result or {}).get("success") and not results:
            err = (result or {}).get("error") or "没有结果"
            return f"搜索失败：{err}", {"type": "tool_result", "ok": False, "summary": str(err)[:80]}
        brief = [{"title": r.get("title"), "url": r.get("url"), "snippet": (r.get("snippet") or "")[:300]}
                 for r in results[:8]]
        summary = f"搜索结果 {len(results)} 条"
        return json.dumps(brief, ensure_ascii=False), {"type": "tool_result", "ok": True, "summary": summary}

    return f"未知工具 {name}", None


async def run_turn(user_id: int, draft_id: int, message: str) -> AsyncIterator[str]:
    """一轮对话的入口：自己开一个 session，再交给 _run_turn。

    为什么必须自己开：请求级的那个 session 在响应头送出时就被关掉了（中间件链是
    BaseHTTPMiddleware，依赖清理早于响应体迭代）。拿它继续读写真会抛
    「Instance '<AgentCreationDraft>' is not persistent within this Session」，
    而异常发生在响应体迭代里——用户看到的就是一直「思考中」，没有报错。
    """
    import asyncio

    from app.database import async_session

    queue: asyncio.Queue = asyncio.Queue()

    async def worker() -> None:
        try:
            async with async_session() as db:
                draft = await get_draft(db, user_id, draft_id)
                if draft is None:
                    await queue.put(_sse({"type": "error", "message": "创建草稿不存在"}))
                    return
                async for event in _run_turn(db, user_id, draft, message):
                    await queue.put(event)
        except Exception as e:
            # 兜底成帧：前端宁可看到一句失败，也不要空等一个再也不会来的回音
            logger.exception(f"创建助手一轮失败 (draft={draft_id})")
            await queue.put(_sse({"type": "error", "message": str(e)[:200]}))
        finally:
            await queue.put(None)

    # 独立任务跑这一轮：客户端断开（关页面、切路由）不该把它掐死——
    # 表单 patch、对话日志、用量都在这条链上，掐死这三样就都没了
    task = asyncio.create_task(worker())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        if not task.done():
            logger.info(f"创建助手 SSE 断开，这一轮继续在后台跑完 (draft={draft_id})")


async def _run_turn(db: AsyncSession, user_id: int, draft: AgentCreationDraft, message: str) -> AsyncIterator[str]:
    """一轮的实体：上下文 + 工具循环 → 存档（对话日志即请求体与用量）"""
    from app.services.agent.llm_credentials import resolve_user_credentials
    from app.services.content.conversation_log_service import save_conversation_log
    from app.services.world.world_chat_service import resolve_chat_model
    from app.repositories.content_repo import SQLAlchemyContentRepository

    history = await load_transcript(db, draft.id)
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(draft.form or {}, draft.preset, draft.sub)},
        *history,
        {"role": "user", "content": message},
    ]
    if not draft.title:
        await update_draft(db, draft, title=message.strip()[:30])

    api_key, api_base = await resolve_user_credentials(db, user_id)
    model = await resolve_chat_model(db, api_base, owner_id=user_id)
    tools = creator_tools()
    usage_total: dict = {}
    text_out = ""
    hit_cap = False

    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        out: dict = {}
        async for event in _stream_once(model, api_base, api_key, messages, tools, out):
            yield event
        usage_total = _merge_usage(usage_total, out.get("usage"))
        text_out += out.get("content") or ""
        calls = out.get("tool_calls") or []
        if not calls:
            break
        if round_no == MAX_TOOL_ROUNDS:
            hit_cap = True
            break

        messages.append({
            "role": "assistant",
            "content": out.get("content") or None,
            "tool_calls": [
                {"id": c["id"] or f"call_{i}", "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for i, c in enumerate(calls)
            ],
        })
        for i, call in enumerate(calls):
            try:
                args = json.loads(call["arguments"] or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            yield _sse({"type": "tool", "name": call["name"], "label": TOOL_LABELS.get(call["name"], call["name"]),
                        "args": args})
            content, extra = await _run_tool(db, draft, call["name"], args)
            if extra:
                yield _sse(extra)
            messages.append({"role": "tool", "tool_call_id": call["id"] or f"call_{i}", "content": content})

    if hit_cap:
        yield _sse({"type": "notice", "message": "工具轮次到上限，先停在当前表单"})

    try:
        await save_conversation_log(
            SQLAlchemyContentRepository(db), None, messages,
            conversation_type=CREATOR_CONVERSATION_TYPE,
            session_id=str(draft.id),
            token_usage=usage_total or None,
            has_output=bool(text_out),
            model=model,
            user_id=user_id,
        )
        # save_conversation_log 只 flush 不 commit（世界侧由 world_repo 收尾）：
        # 这里必须自己交——否则 session 一关整笔回滚，请求体与用量都留不下来
        await db.commit()
    except Exception as e:
        # 存档失败不能吞掉这一轮：用户已经看到回复与表单，只是账/日志缺一笔
        logger.warning(f"创建助手会话记录失败 (draft={draft.id}): {e}")

    yield _sse({"type": "done", "model": model, "usage": usage_total})
