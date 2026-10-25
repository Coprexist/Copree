"""present_plan — 提交计划

计划模式下：先探索、再出计划，用户弹窗通过后本轮按自动模式执行。
"""

from app.services.world.world_ai_mode import USER_NOTE_KEY
from app.tools.world.base import WorldToolPlugin, WorldToolContext
from app.tools.world.shared import arg_error


class PresentPlanTool(WorldToolPlugin):
    name = 'present_plan'
    label = '提交计划'
    segment = 'approval'

    description = (
        '把完整计划提交给用户过目（弹窗确认）。计划模式下这是动手前的必经一步：'
        '计划通过后，本轮的改动/下载/删除按自动模式直接执行；不通过则按用户的意见调整后重新提交。\n'
        'plan 要写清楚：要做哪几件事、改哪些文件、下载什么、删什么、大概顺序。别写空话。\n'
        '把计划拆成 items 一起提交：用户在弹窗里可以只勾其中几条执行（结果里的 '
        'approved_items / skipped_items 就是勾了和没勾的），没勾的一条都别做。\n'
        '用户在弹窗里还可能写下修改意见（结果里的 '
        f'{USER_NOTE_KEY}）：**按用户的话调整**后重新提交，别当作没看见。'
    )

    parameters = {
        'plan': {'type': 'string', 'description': '完整计划（markdown 文本，分条列出要做什么、动哪些文件）'},
        'items': {'type': 'array', 'items': {'type': 'string'},
                  'description': '可选：把计划里的每件事各写成一条（一句话说清做什么、动哪个文件），'
                                 '用户在弹窗里就能只挑几条放行。条目要和 plan 正文对应，别漏也别多'},
        'tool_rounds': {'type': 'integer',
                        'description': '可选：本次计划预计需要几轮工具调用。超过本轮默认上限时，'
                                       '用户批准计划即同时批准这次提额（硬上限 200）；计划被否则提额不生效'},
    }

    required = ['plan']

    async def execute(self, ctx: WorldToolContext) -> dict:
        args = ctx.args
        plan = str(args.get("plan") or "").strip()
        if not plan:
            return arg_error("缺少 plan 参数", args)
        from app.services.world.world_ai_mode import request_approval
        from app.services.world.world_chat_service import ROUND_BUDGET_CEILING
        turn_id = (ctx.turn_state or {}).get("turn_id", "")
        # 计划里可以带上"这次预计要几轮工具调用"：默认上限不够时，批准计划 = 同时批准提额
        # （世界 AI 2026-09-18：「本轮末提示只剩 3 轮：大改容易卡在半途」）
        budget: int | None = None
        raw_rounds = args.get("tool_rounds")
        if raw_rounds not in (None, ""):
            try:
                budget = max(1, min(int(raw_rounds), ROUND_BUDGET_CEILING))
            except (TypeError, ValueError):
                budget = None
        detail = "通过后，本轮剩下的操作按自动模式直接执行"
        if budget:
            detail += f"（本次申请工具轮次上限：{budget} 轮）"
        # 计划必须真有人点头：无人应答一律不通过（on_timeout=False），
        # 否则"计划模式"会退化成"等 5 分钟自动开工"
        raw_items = args.get("items")
        items = [str(x) for x in raw_items] if isinstance(raw_items, list) else []
        approval = await request_approval(
            ctx.world.id, turn_id, kind="plan", title="AI 提交了一份计划",
            detail=detail,
            body=plan, body_format="markdown",
            items=items,                                   # 用户可逐条勾选（「同意」= 全部通过）
            on_timeout=False,
        )
        granted = bool(approval.approved)
        if ctx.turn_state is not None:
            if granted:
                ctx.turn_state["plan_approved"] = True     # 本轮后续操作按自动模式放行
                if budget:
                    ctx.turn_state["round_budget"] = budget
        # 用户可能只通过一部分、或写下修改意见——原样交给 AI，让它调整后重新提交
        partial = approval.partial
        return {
            "success": True, "approved": granted,
            **({"round_budget": budget, "round_note": f"本轮工具轮次上限已提到 {budget} 轮"} if granted and budget else {}),
            # 部分通过：明确告诉他哪几条能做、哪几条没通过（别靠猜，也别把没勾的顺手做了）
            **({"approved_items": list(approval.approved_items),
                "skipped_items": list(approval.skipped_items),
                "instruction": "只做 approved_items 里的；skipped_items 没通过，一条都别做"} if partial else {}),
            USER_NOTE_KEY: approval.note, "summary": approval.reason,
        }

    def summary(self, result: dict) -> str:
        if not result.get("success"):
            return f"提交计划失败：{result.get('error', '未知错误')}"
        note = result.get(USER_NOTE_KEY) or ""
        if not result.get("approved"):
            return f"计划未通过（{note or '用户未说明理由'}）"
        if result.get("skipped_items"):
            return (f"计划部分通过：只做 {len(result.get('approved_items') or [])} 条，"
                    f"{len(result['skipped_items'])} 条没通过")
        return f"计划已通过（用户补充：{note}）" if note else "计划已通过，本轮按自动模式执行"
