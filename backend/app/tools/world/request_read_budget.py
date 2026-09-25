"""request_read_budget — 申请增加只读（读取/搜索）预算

只读工具（file_read / file_grep / file_list / view_api_doc…）有**独立的一本账**，
和改动预算分开算（用户 2026-09-23：搜文件不该吃掉改动额度）。
要探索的东西多、读的轮次不够时用它向用户申请增加：批准当轮立刻生效，不批准维持原预算。
"""
from app.services.world.world_ai_mode import USER_NOTE_KEY
from app.tools.world.base import WorldToolPlugin, WorldToolContext
from app.tools.world.shared import arg_error


class RequestReadBudgetTool(WorldToolPlugin):
    name = 'request_read_budget'
    label = '申请读取预算'
    segment = 'approval'

    description = (
        '申请增加本轮的**读取预算**（只读工具单独一本账，与改动预算分开）。'
        '读的轮次快用完、而确实还需要继续读/搜时用它，写清还要几轮、为什么。'
        '用户在弹窗里批准 → 当轮立刻生效；不批准 → 维持原预算，你就按现有信息继续或收尾。'
    )

    parameters = {
        'rounds': {'type': 'integer', 'description': '还需要几轮只读（建议 20~100）'},
        'reason': {'type': 'string',
                   'description': '为什么还需要：还要查什么、已经查到哪一步、为什么不能先收尾'},
    }
    required = ['rounds', 'reason']

    async def execute(self, ctx: WorldToolContext) -> dict:
        args = ctx.args
        try:
            rounds = int(args.get('rounds'))
        except (TypeError, ValueError):
            return arg_error('rounds 必须是整数', args)
        reason = str(args.get('reason') or '').strip()
        if rounds <= 0:
            return arg_error('rounds 必须大于 0', args)
        if not reason:
            return arg_error('reason 不能为空：用户得知道你为什么还要读', args)

        from app.services.world.world_ai_mode import request_approval
        from app.services.world.world_chat_service import (
            READ_ROUND_CEILING, granted_read_budget,
        )
        turn_state = ctx.turn_state if ctx.turn_state is not None else {}
        # 当前生效的只读预算由对话循环写在 turn_state 上（工具不自己重算配置，口径只有一处）
        budgets = turn_state.get('round_budgets') or ()
        current_read = int(budgets[1]) if len(budgets) > 1 else READ_ROUND_CEILING
        new_read = granted_read_budget(current_read, rounds)

        approval = await request_approval(
            ctx.world.id, turn_state.get('turn_id', ''),
            kind='budget',
            title=f'AI 申请增加读取预算（+{min(max(1, rounds), READ_ROUND_CEILING)} 轮）',
            detail=reason,
            # 没人应答一律不批：预算也是资源，不能"等超时了就当默认给了"
            on_timeout=False,
        )
        granted = bool(approval.approved) and new_read > current_read
        if granted:
            turn_state['read_round_budget'] = new_read
        return {
            'success': True,
            'approved': granted,
            'read_budget': new_read if granted else current_read,
            'read_rounds_used': int(turn_state.get('read_rounds', 0)),
            USER_NOTE_KEY: approval.note,
            'summary': approval.reason,
        }

    def summary(self, result: dict) -> str:
        if not result.get('success'):
            return f"申请读取预算失败：{result.get('error', '未知错误')}"
        if result.get('approved'):
            return f"已批准：读取预算提到 {result.get('read_budget')} 轮"
        note = result.get(USER_NOTE_KEY) or ''
        return f"未批准（{note or '用户未说明理由'}），维持 {result.get('read_budget')} 轮"
