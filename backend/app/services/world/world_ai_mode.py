"""世界 AI 运行模式与操作审批 — 自动 / 审阅 / 计划（产品 2026-09-15 定）

三种模式对三类敏感动作（下载文件 / 删除文件 / 改动机制）的处置：
- auto   自动：不打断，AI 自行执行下载、改动机制、删除文件
- review 审阅：平台弹窗征得同意后才执行（同类动作本轮同意一次即可）；未同意一律不执行
- plan   计划：敏感动作先全部挡住，AI 用 present_plan 出计划 → 用户弹窗通过 → 本轮按自动模式执行

单一机制：审批一律走 request_approval() 这一条通道（弹窗 + 等服务端事件），
AI 侧的 ask_user 工具与平台门禁共用它，不存在第二套确认逻辑。

用户在弹窗里除了点同意/不同意，还可以写下理由或补充要求（回执的 note）——**这句话是给 AI 的**：
它随审批结论一起进 AI 上下文（工具结果里的 user_note，见 Approval.instruction），AI 必须照办。
丢掉它就等于"用户明明说了，AI 没听见"。

存储：worlds.config["ai_mode"]。**只由用户/API 改**——AI 没有改模式的工具，
否则等于让它自己拆掉审阅（安全边界不能靠自觉）。

配套硬约束（见 shared.web_download）：没走平台门禁的路径（决策技能 / 定时 / 斜杠命令）
下载完文件后，平台再弹窗问是否保留；无人应答按「不保留」删除。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MODES = ("auto", "review", "plan")
MODE_LABELS = {"auto": "自动模式", "review": "审阅模式", "plan": "计划模式"}
DEFAULT_MODE = "review"                 # 默认最安全的一档（老世界一并生效）

# 三类敏感动作 → 工具名单（按动作类批准：一次同意覆盖本轮同类操作，避免弹窗轰炸）
ACTION_TOOLS: dict[str, frozenset[str]] = {
    "download": frozenset({"web_download"}),
    "delete": frozenset({"file_delete"}),
    "modify": frozenset({
        "file_write", "file_edit", "file_move", "file_copy",
        "apply_world_block", "run_world_code",
        "update_group_types", "update_trigger_mode", "update_world_info",
        "set_group_member_role", "kick_group_member",
    }),
}
# 内部/日志用（含"机制"这类术语，只给开发看）
ACTION_LABELS = {"download": "下载文件", "delete": "删除文件", "modify": "改动世界机制"}
# 弹窗用：**用户听得懂的人话**（用户 2026-09-15 反馈：别让用户读内部术语）
ACTION_ASKS = {
    "download": "AI 想下载一个文件",
    "delete": "AI 想删除文件",
    "modify": "AI 想改动这个世界",
}

# 只读 / 无副作用工具：明列，其余未登记的一律按「改动机制」（保守默认，安全优先）
SAFE_TOOLS = frozenset({
    "view_api_doc", "view_world_block", "list_world_blocks",
    "file_list", "file_read", "file_grep",
    "get_bound_groups", "get_group_messages", "get_group_types",
    "list_group_members", "send_group_message", "suggest_questions",
    "manage_records", "store_memory", "recall_memory",
    "web_fetch", "web_search",
    "clear_context", "compact_context",
    "ask_user", "present_plan", "rename_session",   # 命名自己的对话：无副作用（只改会话元信息）
    "request_read_budget",                          # 申请提额：审批入口本身不是动作
})

_APPROVAL_TIMEOUT = 600                 # 审阅/计划：**空闲**多久没人动才算超时（秒）；超时 = 不通过（安全默认）
_APPROVAL_MAX = 1800                    # 同一条审批的总时长上限（秒）：打字能续期，但不能无限续
_ASK_TIMEOUT = 600                      # 自动档：AI 主动提问等用户的上限（10 分钟，超时自行继续，对齐 DSH）
_WAIT_FOR_VIEWER = 20                   # 没人在看时先等一小会儿（页面最多 10s 一次空闲轮询会接上）
_TOUCH_SLICE = 5                        # 等待循环的切片（秒）：切片够短，打字心跳才能及时续期


def get_mode(world) -> str:
    """世界当前运行模式（未设置/非法值 → DEFAULT_MODE）"""
    mode = str((world.config or {}).get("ai_mode") or DEFAULT_MODE)
    return mode if mode in MODES else DEFAULT_MODE


def action_of(tool_name: str) -> str | None:
    """工具 → 动作类；None = 无需审批。未登记的工具按「改动机制」处理（保守）。"""
    if tool_name in SAFE_TOOLS:
        return None
    for action, names in ACTION_TOOLS.items():
        if tool_name in names:
            return action
    return "modify"


def needs_gate(world, tool_name: str) -> bool:
    """该工具在当前模式下是否需要门禁介入（auto 永远不需要）"""
    return get_mode(world) != "auto" and action_of(tool_name) is not None


# ═══════════════════════════════════════════════════════════════
# 审批通道（唯一机制：弹窗 + 等答案）
# ═══════════════════════════════════════════════════════════════
# 用户补充说明（理由 / 补充要求）上限：够写清楚一段要求，又不至于把回执与上下文撑爆。
# 对外也是 API 契约（路由的 ApprovalRequest.max_length 引用它），所以是公开名。
NOTE_MAX = 2000

# 用户原话进工具结果时用的键（唯一约定）：门禁放行、旁路事后确认都写它，AI 从这里读
USER_NOTE_KEY = "user_note"


@dataclass(frozen=True, slots=True)
class Approval:
    """一次审批的结论——唯一来源。

    用户的原话（note）与「有没有人真的应答」（attended）分开存：调用方一律读字段，
    **不要再从句子抠字符串**（曾经用「"未回复" not in note」判断有没有人应答，改一次措辞就崩）。
    reason 是给日志和 AI 看的一句话，结论与用户原话都在里面。
    """

    approved: bool
    note: str = ""            # 用户自己写的原话（理由 / 补充要求），没写就是空串
    attended: bool = True     # 真有人点了按钮才为 True（没前端 / 等超时 = False）
    reason: str = ""          # 一句话结论（进日志，也在无人应答时进 AI 上下文）

    @property
    def instruction(self) -> str:
        """给 AI 的补充指示：用户写了才非空（空串 = 没什么要额外交代的）。"""
        if not self.note:
            return ""
        return f"{self.reason}。请把用户的话一并考虑进接下来的操作。"


def with_user_note(result: dict, text: str) -> dict:
    """把给 AI 的话并进工具结果（唯一入口）。text 为空就原样返回。"""
    return {**result, USER_NOTE_KEY: text} if text else result


# approval_id → {future, kind, title, detail, world_id, created_at, note}
_pending: dict[str, dict] = {}


def _broadcasters(world_id: int, turn_id: str) -> list:
    """审批弹窗的投递目标：指定轮次；没指定（旁路下载等）就发给该世界所有活跃轮次。

    仅保留有订阅者（前端确实连着）的通道——没有订阅者 = 没人在看，不值得空等。
    """
    from app.services.world.world_turn import active_broadcasts, get_turn_broadcast
    tbs = [get_turn_broadcast(world_id, turn_id)] if turn_id else active_broadcasts(world_id)
    return [tb for tb in tbs if tb is not None and tb.subscribers]


def _event(payload: dict) -> str:
    return "data: [APPROVAL]" + json.dumps(payload, ensure_ascii=False) + "\n\n"


def unattended_policy(world) -> tuple[bool, int]:
    """无人应答时的处置（模式决定，单一来源）：返回 (超时是否放行, 等多久)。

    - **auto 自动**：无人应答就继续——AI 主动提问等 10 分钟（对齐 DSH 的「问不到就自己判断」），
      没人在看时更不该干等；
    - **review 审阅 / plan 计划**：无人应答一律**不放行**（这是这两种模式的全部意义，
      绝不因为"等超时了"就默认批准敏感操作）。
    """
    if get_mode(world) == "auto":
        return True, _ASK_TIMEOUT
    return False, _APPROVAL_TIMEOUT


# 弹窗正文上限：够看清一个文件/一份计划，又不至于把 SSE 撑爆
_BODY_MAX = 8000


class ApprovalTimeout(Exception):
    """等用户等超了（空闲超时 / 总时长封顶）——由 request_approval 翻成「不通过」。"""


def _human_duration(seconds: float) -> str:
    """秒数说人话：不足 1 分钟就说秒（曾经写成 1 // 60 = 「0 分钟」）"""
    return f"{int(seconds)} 秒" if seconds < 60 else f"{int(seconds) // 60} 分钟"


def _idle_left(entry: dict, idle_timeout: int) -> float:
    """距「上次活动」还剩多久（秒，可为负）"""
    return idle_timeout - (time.monotonic() - entry["last_activity"])


def remaining_seconds(entry: dict, idle_timeout: int = _APPROVAL_TIMEOUT,
                      hard_cap: int = _APPROVAL_MAX) -> int:
    """这条审批还剩多少秒——**唯一口径**：弹窗倒计时、状态轮询、等待循环都读它。

    取「空闲剩余」与「总时长剩余」的较小值：打字能把空闲窗口续上，但续不过总上限。
    """
    left = min(_idle_left(entry, idle_timeout), hard_cap - (time.monotonic() - entry["started_at"]))
    return max(0, int(left))


def touch_approval(approval_id: str) -> int:
    """用户正在弹窗里打字（心跳）：刷新活动时间，返回剩余秒数（0 = 该项已不在）。

    打字不是「点按钮」，所以不在这里改结论——它只重置「多久没人动」这个计时，
    免得用户还在写理由，那边已经按超时判了不通过。
    """
    entry = _pending.get(approval_id)
    if entry is None or entry["future"].done():
        return 0
    entry["last_activity"] = time.monotonic()
    return remaining_seconds(entry)


async def _wait_decision(entry: dict, idle_timeout: int, hard_cap: int) -> bool:
    """等用户点按钮：按「距上次活动」计时（打字就续期），总时长封顶。

    future 必须 shield：asyncio.wait_for 超时会**取消**传入的 future，
    而这条 future 是 resolve_approval 唯一的回执通道——被取消就再也接不到用户的点击。
    """
    while True:
        left = min(_idle_left(entry, idle_timeout),
                   hard_cap - (time.monotonic() - entry["started_at"]))
        if left <= 0:
            if _idle_left(entry, idle_timeout) <= 0:
                raise ApprovalTimeout(f"等待用户确认超时（{_human_duration(idle_timeout)}没人操作）")
            raise ApprovalTimeout(f"等待用户确认超时（已达 {_human_duration(hard_cap)}上限）")
        try:
            decided = await asyncio.wait_for(asyncio.shield(entry["future"]),
                                             timeout=min(left, _TOUCH_SLICE))
        except asyncio.TimeoutError:
            continue                             # 切片到点：回去看有没有新活动（打字会续期）
        return bool(decided)


async def request_approval(
    world_id: int, turn_id: str, *, kind: str, title: str,
    detail: str = "", body: str = "", body_format: str = "text", body_lang: str = "",
    timeout: int = _APPROVAL_TIMEOUT, on_timeout: bool = False,
) -> Approval:
    """弹窗征询用户同意（唯一审批通道）。返回 Approval（结论 + 用户原话 + 有没有人应答）。

    on_timeout = 没有人应答（没人看 / 等超时）时算不算放行——由调用方按模式声明，
    不要在这里猜：审阅/计划必须 False，自动档的 AI 主动提问才是 True。

    timeout = **多久没人动**才算超时（秒），不是总时长：用户在弹窗里打字
    （touch_approval 心跳）会把这段空闲窗口续上，总时长另由 _APPROVAL_MAX 封顶。
    """
    def _unattended(reason: str) -> Approval:
        if on_timeout:
            return Approval(approved=True, attended=False,
                            reason=f"{reason}；自动档按你的判断继续（在回复里说明你的决定）")
        return Approval(approved=False, attended=False, reason=reason)
    # 页面未必已经在看这个轮次（外部发起的轮次靠空闲轮询接上）——先等一小会儿再判定无人。
    tbs = _broadcasters(world_id, turn_id)
    deadline = time.monotonic() + _WAIT_FOR_VIEWER
    while not tbs and time.monotonic() < deadline:
        await asyncio.sleep(1)
        tbs = _broadcasters(world_id, turn_id)
    if not tbs:
        return _unattended("当前没有可交互的前端（弹窗无人应答）")

    approval_id = uuid.uuid4().hex[:12]
    entry = {
        "id": approval_id, "world_id": world_id, "kind": kind,
        # detail = 一行摘要（动哪个文件）；body = 用户真正要看的内容，
        # 由前端按 body_format 走聊天同款渲染器（markdown / code / text）
        "title": title, "detail": (detail or "")[:4000],
        "body": (body or "")[:_BODY_MAX], "body_format": body_format, "body_lang": body_lang,
        "created_at": time.time(),                 # 墙上时间：给前端显示什么时候开始的
        "started_at": time.monotonic(),            # 单调钟：算总时长上限（不受系统时间调整影响）
        "last_activity": time.monotonic(),         # 用户最近一次动作（点按钮 / 打字心跳）
        "future": asyncio.get_running_loop().create_future(),
    }
    _pending[approval_id] = entry
    pending_event = _event({
        "approval_id": approval_id, "status": "pending", "kind": kind,
        "title": title, "detail": entry["detail"],
        "body": entry["body"], "body_format": entry["body_format"], "body_lang": entry["body_lang"],
        "expires_in": remaining_seconds(entry),
    })
    # 未完成时的兜底结论（finally 里要广播回执，不能因为异常路径没赋值而炸）
    result = Approval(approved=False, attended=False, reason="审批未完成（按「不通过」处理）")
    try:
        for tb in tbs:
            await tb.broadcast(pending_event)
        logger.info(f"🔐 世界 #{world_id} 等待用户审批（{kind}）: {title[:60]}")
        approved = await _wait_decision(entry, timeout, _APPROVAL_MAX)
        note = entry.get("note") or ""
        # 结论必在句子里：用户写了原话就原样附上（不改写用户的措辞），AI 才看得懂是"同意"还是"不同意"
        result = Approval(
            approved=approved, note=note,
            reason=("用户同意了" if approved else "用户选择不同意")
                   + (f"；用户补充说：{note}" if note else ""),
        )
    except ApprovalTimeout as e:
        result = _unattended(str(e))
    except asyncio.CancelledError:
        result = Approval(approved=False, attended=False, reason="轮次被中断，审批未完成（按「不通过」处理）")
        raise
    finally:
        _pending.pop(approval_id, None)
        resolved_event = _event({
            "approval_id": approval_id, "status": "resolved",
            "kind": kind, "approved": result.approved,
        })
        for tb in tbs:                               # 弹窗关不掉不影响业务结果
            try:
                await tb.broadcast(resolved_event)
            except Exception as e:
                logger.warning(f"🔐 世界 #{world_id} 审批结果回执广播失败: {e}")
    logger.info(f"🔐 世界 #{world_id} 审批结果（{kind}）: {result.approved}｜{result.reason[:60]}")
    return result


def resolve_approval(approval_id: str, approved: bool, note: str = "") -> bool:
    """用户弹窗点击回执（HTTP 端点调用）。note = 用户写的理由/补充要求（可选）。

    返回是否命中待审批项。note 在这里收口裁剪：换行保留（用户可能分条写），长度封顶。
    """
    entry = _pending.get(approval_id)
    if entry is None or entry["future"].done():
        return False
    entry["note"] = (note or "").strip()[:NOTE_MAX]
    entry["future"].set_result(bool(approved))
    return True


def pending_approvals(world_id: int) -> list[dict]:
    """该世界待审批项（前端刷新后重画弹窗；不含 future）"""
    return [
        {"approval_id": e["id"], "kind": e["kind"], "title": e["title"],
         "detail": e["detail"], "body": e["body"],
         "body_format": e["body_format"], "body_lang": e["body_lang"],
         "created_at": e["created_at"],
         # 弹窗倒计时的唯一来源（刷新后照它接着走，不从头重数）
         "expires_in": remaining_seconds(e)}
        for e in _pending.values() if e["world_id"] == world_id
    ]


# ═══════════════════════════════════════════════════════════════
# 工具门禁
# ═══════════════════════════════════════════════════════════════

async def gate_tool_call(world, world_id: int, tool_name: str, args: dict, turn_state: dict):
    """工具执行前的唯一门禁。返回 (allowed, approved_for_tool, feedback)。

    feedback = 要说给 AI 听的话（空串 = 没什么要说的）：被挡下时是拦截原因；
    用户点了同意但写了补充要求时，是那句要求——调用方用 with_user_note() 并进工具结果。
    （用户的话不能吞：吞了 AI 就会按自己原来的打算做完，还怪用户没提醒它。）

    - auto：直接放行（approved=True，工具知道平台已经兜过底了，不用再自己问）
    - plan：计划未通过 → 挡住并提示先出计划；通过后本轮全部放行
    - review：同类动作本轮同意过一次就放行，否则弹窗问
    """
    action = action_of(tool_name)
    if action is None:
        return True, False, ""
    mode = get_mode(world)
    if mode == "auto":
        return True, True, ""

    if mode == "plan" and not turn_state.get("plan_approved"):
        # run_world_code 在计划通过前由平台强制只读（不写文件/世界数据/群消息），
        # 读代码、跑统计、探索现状正是出计划所需——放行；
        # 计划通过后本分支不再进入，写入类工具正常放行，沙箱 readonly 也随之解除（见 run_world_code.py）
        if tool_name == "run_world_code":
            return True, False, ""
        return False, False, (
            "计划模式：本轮计划还没通过。请先用 present_plan 提交完整计划（要改哪些文件、下载什么、删什么），"
            "用户在弹窗里通过后我才会执行——先出计划，不要直接动手。"
        )

    approved_classes = turn_state.setdefault("approved_classes", set())
    if turn_state.get("plan_approved") or action in approved_classes:
        return True, True, ""

    # 门禁永不「超时放行」：审阅/计划模式下没人应答就是不同意（on_timeout=False）
    desc = describe_action(tool_name, args)
    approval = await request_approval(
        world_id, turn_state.get("turn_id", ""),
        kind=action,
        # 弹窗写给用户看：一句人话的标题 + 一行摘要 + 可渲染的正文
        # （模式不写进标题，弹窗上方已有徽章）
        title=ACTION_ASKS[action],
        detail=desc["summary"],
        body=desc.get("body") or "",
        body_format=desc.get("format") or "text",
        body_lang=desc.get("lang") or "",
        on_timeout=False,
    )
    if not approval.approved:
        return False, False, (
            f"用户没有同意本次{ACTION_LABELS[action]}（{approval.reason}），操作未执行。"
            "请先向用户说明原因，得到明确同意后再来一次；不要换个方式绕过。"
        )
    approved_classes.add(action)
    # 同意照常放行；用户顺手写的理由/补充要求一并带给 AI（不因此再弹一次窗——
    # 用户点的是"同意"，让他为同一件事点两次是本末倒置）
    return True, True, approval.instruction


_LANG_BY_EXT = {
    "html": "html", "htm": "html", "css": "css", "js": "javascript", "mjs": "javascript",
    "jsx": "javascript", "ts": "typescript", "tsx": "typescript", "py": "python",
    "json": "json", "md": "markdown", "yml": "yaml", "yaml": "yaml", "sh": "bash",
    "sql": "sql", "xml": "xml", "txt": "plaintext",
}


def _lang_of(path: str) -> str:
    return _LANG_BY_EXT.get(str(path).rsplit(".", 1)[-1].lower(), "plaintext") if "." in str(path) else "plaintext"


def _fence(lang: str, text: str) -> str:
    """四反引号围栏：正文里带三反引号也不会截断（markdown 渲染器支持）"""
    return "````" + lang + "\n" + text + "\n````"


def describe_action(tool_name: str, args: dict) -> dict:
    """弹窗内容：summary 一行摘要 + body 用户真正要看的东西 + format 渲染方式。

    前端按 format 走**聊天同一套渲染器**（markdown → MarkdownContent，code → CodeRenderer），
    不再把内容当一坨纯文本塞进 <pre>——用户要能看懂 AI 到底要改什么（2026-09-15）。
    """
    if tool_name == "web_download":
        url = str(args.get("url", ""))
        target = args.get("path") or "downloads/（自动命名）"
        return {"summary": f"要下载：{url}", "body": f"下载地址\n{url}\n\n存到\n{target}", "format": "text"}
    if tool_name == "file_delete":
        return {"summary": f"要删掉：{args.get('path', '')}", "body": "", "format": "text"}
    if tool_name in ("file_move", "file_copy"):
        verb = "要搬去" if tool_name == "file_move" else "要复制成"
        return {"summary": f"{verb}：{args.get('from', '')} → {args.get('to', '')}", "body": "", "format": "text"}
    if tool_name == "file_write":
        path = str(args.get("path", ""))
        return {"summary": f"要写这个文件：{path}", "body": str(args.get("content") or ""),
                "format": "code", "lang": _lang_of(path)}
    if tool_name == "file_edit":
        path = str(args.get("path", ""))
        lang = _lang_of(path)
        old, new = str(args.get("old_string") or ""), str(args.get("new_string") or "")
        body = ""
        if old:
            body += "**替换掉这一段：**\n\n" + _fence(lang, old) + "\n\n"
        body += "**换成：**\n\n" + _fence(lang, new)
        return {"summary": f"要改这个文件：{path}", "body": body, "format": "markdown"}
    return {"summary": f"要执行：{tool_name}", "body": json.dumps(args, ensure_ascii=False, indent=2),
            "format": "code", "lang": "json"}


# ═══════════════════════════════════════════════════════════════
# 提示词（模式语义与门禁同一处定义，避免两处说法不一致）
# ═══════════════════════════════════════════════════════════════

def build_mode_prompt(mode: str) -> str:
    """强注入的模式说明段（非 auto 才需要讲规矩）"""
    if mode == "auto":
        return (
            "\n【运行模式：自动】本世界处于自动模式：下载文件、改动世界机制、删除文件都由你自行决定并执行，"
            "平台不会弹窗打断你。仍须遵守内容与文件类型的硬规则（不得下载色情/暴力/违法内容、"
            "不得下载可执行文件与脚本）。"
        )
    if mode == "review":
        return (
            "\n【运行模式：审阅】本世界处于审阅模式：**下载文件、改动世界机制、删除文件**三类操作平台会弹窗"
            "请用户确认，用户同意后才会执行（同类操作本轮同意一次即可，之后不再打断）。\n"
            "- 用户明确要求你做的改动，也只需照常调用工具（平台会弹一次确认），不用额外解释；\n"
            "- 你自己判断需要做的改动（用户没说、或与用户先前说法有出入），**先向用户说明为什么**，再调用工具；\n"
            "- 弹窗里有个输入框，用户点同意/不同意时可能顺便写下理由或补充要求（工具结果里的 "
            f"{USER_NOTE_KEY}）——**这是给你的指示，不是旁白**：与你的做法不一致时以用户为准，\n"
            "  别当作没看见，更别回一句「我已经做了」；\n"
            "- 被拒绝时不要换路径、换工具、改参数重试——停下来问清楚用户的意图；\n"
            "- 需要用户在其他事情上拍板（选方案、确认理解）时用 ask_user 工具，事件类型关键词必填。"
        )
    return (
        "\n【运行模式：计划】本世界处于计划模式：**先探索、先规划**。\n"
        "- 收到任务先读相关文件/资料摸清现状，然后用 present_plan 提交计划（要改哪些文件、下载什么、删什么、"
        "分几步），用户在弹窗里通过后，本轮剩下的操作按自动模式执行——此时直接干，不要再逐步请示；\n"
        "- run_world_code 在计划模式下仍可用：**计划通过前**平台强制只读（能读文件、跑统计、探索现状，"
        "不能写文件/世界数据/发群消息）；**计划通过后**本轮按自动模式执行，沙箱恢复可写——"
        "跑打包脚本、批量替换脚本都行，不用手工对齐产物；\n"
        "- 计划未通过前，任何写入/下载/删除工具都会被平台挡下，这是正常的，不要反复试；\n"
        "- 任务明显超过本轮默认工具轮次上限（例如要连改十几个文件）时，在 present_plan 里带 "
        "tool_rounds=N 一并申请提额：用户批准计划即批准提额，别硬着头皮开工、卡在最后一轮半途而废；\n"
        "- 用户在弹窗里可能只通过部分内容，或在输入框里写下修改意见（工具结果里的 "
        f"{USER_NOTE_KEY}）：**按用户的话调整**后重新 present_plan，不要把意见当作没看见。"
    )
