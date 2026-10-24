"""view_api_doc — 看接口文档

查看「群视界 API 文档」指定分区的详细接口内容（文档按区分区，此处只列区名与区介绍，需要细节时按需打开对应分区）：
"""

from app.tools.world.base import WorldToolPlugin, WorldToolContext
from app.tools.world.shared import arg_error


class ViewApiDocTool(WorldToolPlugin):
    name = 'view_api_doc'
    label = '看接口文档'
    segment = 'world'

    description = (
        '查看「群视界 API 文档」指定分区的详细接口内容（文档按区分区，此处只列区名与区介绍，需要细节时按需打开对应分区）：\n01 世界编号变量：window 注入的 `WORLD_ID / GROUP_ID '
        '/ USER_ID / WORLD_AI_ID` 等变量与打包原则。**写任何世界页面代码前必读**，杜绝硬编码编号。\n02 世界UI桥 WorldUI：控制宿主外壳：侧边栏/悬浮图标的显隐。'
        '实现了自己的导航时用 `hideFloatingIcon` 避免两套 UI 重复；全屏直接用浏览器 Fullscreen API（iframe 已授权）。\n03 文件操作：`file_list / file_write / file_read / file_edit '
        '/ file_delete` 全部参数与返回、类型白名单、越界防护、读取截断规则。**建/改世界网页代码前必读**。\n04 世界块体系（积木）：`list_world_blocks / view_world_block '
        '/ apply_world_block` 用法、现有积木（平台侧边栏、群聊对话窗）、**DIY 定制**与**更新机制**、侧边栏约定。\n05 群聊 API（世界 AI 的群工具）：读消息/发消息/成员列表/角色管理/踢人工具：'
        '参数、返回、身份与权限约定（默认作用于本世界绑定群）。\n06 页面与资源：沉浸界面预览入口、静态资源路由 `/world/{id}/files/`、相对路径规则、资源类型。\n07 懒通知与世界时间：'
        '用户手动改代码的通知机制（对话时取通知）、世界时间流速与运行模式（懒加载/后台任务）。\n08 错误与安全：错误体格式、状态码表、认证要求、写操作身份与防护约定。\n09 受控数据 API（世界代码）'
        '：沙箱/世界代码访问世界数据/对话状态/群聊的代理接口：WORLD_API_TOKEN 鉴权、端点、动态配额。\n10 同步与限流机制：DSH 世界镜像双向同步（world_pull/world_push）'
        '与写操作限流（429）的完整机制。**改文件后 push 报「无变化」、pull 报「pulled N 却无变化」、页面操作触发 429 时看本区。**'
    )

    parameters = {'section': {'type': 'string', 'description': '分区号（01~10，见上方区列表）'}}

    required = ['section']

    async def execute(self, ctx: WorldToolContext) -> dict:
        try:
            args = ctx.args
            section = str(args.get("section", "")).strip()
            if not section:
                from app.services.world.world_api_docs import _discover_sections
                ids = " / ".join(s["id"] for s in _discover_sections())
                return arg_error(f"缺少 section 参数（可选：{ids}）", args)
            from app.services.world.world_api_docs import view_section
            return {"success": True, **view_section(section)}
        except (ValueError, FileNotFoundError) as e:
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        ok = bool(result.get("success"))
        if ok:
            return f"接口文档「{result.get('title') or result.get('section', '')}」已读取"
        return f"接口文档读取失败：{result.get('error', '未知错误')}"
