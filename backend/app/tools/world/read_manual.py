"""read_manual — 查平台文档

跟主站 AI 的 read_manual 同一份实现（services/docs/library）：那边读用户手册是为了答平台功能，
这边读是为了「世界 AI 也说得清平台怎么用」，外加一份《创建 AI 参考》——
设计世界时给 AI 居民写人设、定说话方式，用得着。
"""

from app.tools.world.base import WorldToolPlugin, WorldToolContext


class ReadManualTool(WorldToolPlugin):
    name = 'read_manual'
    label = '查平台文档'
    segment = 'net'

    description = (
        '查平台文档，别凭记忆答。\n'
        'doc=manual（默认）《用户手册》：平台功能与操作——彩色文字 / Markdown、文件转发、闹钟、记忆、'
        '联邦、群权限；用户问「这平台怎么用」时先查它。\n'
        'doc=ai-guide《创建 AI 参考》：AI 人设怎么写——四层结构（身份 / 关系 / 说话方式 / 边界）、'
        '长度预算、去 AI 感的字面清单、可复制模板、发布前自检；给 AI 居民写人设、调某个 AI 的说话方式时查它。\n'
        'doc=world-skill《世界 AI 技能参考》：给 AI 居民颁布世界侧技能怎么写——'
        '「玩家能玩的，居民也要能玩」的五条原则、manifest/permissions/ctx 协议、沙箱边界、骨架示例、颁布前自检。\n'
        '传关键词拿命中片段，不传给目录。'
    )

    parameters = {
        'doc': {
            'type': 'string',
            'enum': ['manual', 'ai-guide', 'world-skill'],
            'description': 'manual=用户手册（默认），ai-guide=创建 AI 参考，world-skill=世界 AI 技能参考（写居民技能前查）',
        },
        'keyword': {'type': 'string', 'description': '关键词（可选）；不传返回目录'},
    }

    required = []

    async def execute(self, ctx: WorldToolContext) -> dict:
        from app.services.docs import library

        return library.read(
            str(ctx.args.get("doc") or "manual").strip(),
            str(ctx.args.get("keyword") or "").strip(),
        )

    def summary(self, result: dict) -> str:
        if not result.get("ok"):
            return f"文档读取失败：{result.get('error', '未知错误')}"
        title = result.get("title") or "文档"
        keyword = result.get("keyword")
        return f"《{title}》{'「' + keyword + '」命中' if keyword else '目录已读取'}"
