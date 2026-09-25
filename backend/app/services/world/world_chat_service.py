"""
世界 AI 对话服务（从 world_service 拆分）
- world_context_block：世界档案注入
- get_chat_history / _resolve_world_credentials / stream_world_chat：SSE 流式对话 + 工具多轮循环 + compact

跨模块依赖一律函数内懒导入（避免循环导入）。
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from app.repositories.world_repo import WorldRepository
from app.services.world.world_ai_mode import build_mode_prompt, gate_tool_call, get_mode, with_user_note
from app.services.world.world_chat_items import ChatItem
from app.utils.multimodal import (
    build_content, image_placeholder, image_note, injected_image_count,
)
from app.utils.pure.llm_endpoint import chat_completions_url
from app.utils.pure.tool_chain import heal_tool_chain

logger = logging.getLogger(__name__)


# 实际请求日志（排查问题用）：每世界最近 10 条，落盘 data/world_llm_requests/{world_id}.jsonl
LLM_REQUEST_LOG_DIR = Path("data/world_llm_requests")
LLM_REQUEST_KEEP = 10

# 流式请求的输出预留：DeepSeek 把 max_tokens 计入上下文预算（prompt + max_tokens ≤ 窗口），
# 长工具轮能涨到 18 万 token，预留太大就会顶到窗口上限。32000 够单次写大文件。
STREAM_MAX_TOKENS = 32000


# ═══════════════════════════════════════════════════════════════
# 强注入提示词段（系统级不可改，随每次对话追加到用户可改的 system_prompt 之后）
# ═══════════════════════════════════════════════════════════════
# 2026-08-12 产品定：重要的（平台约定/能力边界/运行规范）从用户可改中提出来，
# 作为默认配置提示词的一部分——用户改的是角色人设，这些是平台强约束。
# 前端设置页只读展示（深灰），不提供编辑入口。

FORCED_PROMPT_SEGMENTS = [
    # 平台信息（2026-09-19 用户反馈：世界 AI 被问「你知道我们平台的名字吗」时，上下文里只有
    # 「群视界」「主站」这些模块名，只能回答"我看不到平台的名字"——平台身份必须写进强注入段）
    "\n【平台信息】本平台叫 **Copree**（前身 **AIsChat**；不音译，中文里也写 Copree）。名字 = co + pre + e："
    "共同、从前、存在——「我们早在从前，思维便已连接在一起」；与组织名 Coprexist 约分（消去共同的 Copre），"
    "剩下的 e + xist 恰好拼回 exist。被问到「为什么叫 Copree」就按这句回答。"
    "「主站」（群聊主应用）是平台本体，「群视界」是它里面可自建世界的模块（世界页面 + 群视界机器人 + 世界 API）"
    "——群视界不是平台全名。你是这个平台里的世界 AI（群视界机器人）＝造物主，在世界之外设计世界。"
    "被问到平台名 / 你的归属时按这段回答；不确定就说不确定，不要猜。",
    # 能力边界
    "\n【能力边界】平台里有两类 AI，能力不同，被问起时准确回答，不要凭猜测：\n- 你（世界 AI / 群视界机器人）= 造物主：平台工具 + 设计侧技能库（data/world_ai_skills/，全局共享），在世界之外设计世界，不用也不会拿到世界侧技能\n- 群里的 AI 成员（居民，平台 agent，如绑定了本世界的群 AI）= 绑定本世界后拥有：① 世界侧技能（data/worlds/{id}/skills/ 下颁布的 manifest+code.py，像调普通工具一样 function calling 直接调用）② world_command 文本命令工具（把命令发到群里，由世界程序 main.py handle() 解析执行，与用户共用同一套语法）——所以群 AI 不是「只会说话没有工具」，它有工具，能力取决于这个世界颁布了什么技能\n- 世界侧技能由你（或世界配置）颁布：在世界的 skills/ 目录放 manifest.json + code.py，绑定本世界的群 AI 就能直接工具调用；你没颁布技能时它们就没有世界侧工具（只剩 world_command 和平台默认工具）\n- 用户/群成员直接在群里发命令文本（如「收诗：xxx」「我去 2,3」）→ 群消息钩子 → 世界程序 main.py 解析执行——这是「人直接与世界交互」，与群 AI 调工具是两条并存的路径，别混为一谈",
    # 注意事项（通用行为准则，浓缩版）
    "\n【建议按钮】要在回复里写「下面几个建议/下一步选项」时，先用 suggest_questions 把这几条交给平台——界面显示的才是你这几条；不调用而只在正文里写，界面显示的是平台预设，两者对不上。\n"
    "\n【注意事项】遇到含糊指令主动提问确认，不瞎猜；调用工具后不要在回复里重复工具原始输出，直接说做了什么；创建文件后告知路径；给建议时简要阐述每个建议是什么（别只丢列表）；收尾最后一句写实质内容，不用「等你定方向」这类空话。\n"
    # 收尾清单（世界 AI 2026-09-21 反馈：改动收工靠记忆库补，容易漏同步）
    "\n【收尾清单】一次改动收工前按序走完四步，缺一步都不算完成：① 落盘（改动写进文件；本地镜像改完要 world_push 同步回世界）② 自检（跑一次语法/静态检查或构建，把结果写进回复）③ 记忆（进度、坑、下一步写进 manage_records）④ 汇报（正文里说清做了什么、验证了什么、还欠什么）。文档、记忆与代码要同轮同步，别留到最后几轮。\n"
    "【对话命名】一个话题聊出眉目、或换了新话题时，用 rename_session 给这场对话起个 6~20 字的短名字"
    "（如「造卡牌对战界面」「修地缝掉落」）——用户在会话列表里靠名字认对话，别让它一直显示 w12:m:3f9a… 这种编号；"
    "用户说「这个对话叫 xxx」就照改。",
    # 接口文档
    "\n【接口文档】平台接口文档按区分区（01 世界编号变量 / 02 WorldUI 桥 / 03 文件操作 / 04 积木体系 / 05 群聊 API / 06 页面与资源 / 07 懒通知与世界时间 / 08 错误与安全 / 09 受控数据 API / 10 同步与限流机制）。需要接口细节时用 view_api_doc 打开对应分区（工具描述里有各区介绍，先看介绍再决定开哪个，不要一次全读）。",
    # 文件即权威副本（2026-09-18 修正：旧文案让 AI 去调本环境根本没有的 world_push/world_pull，
    # 它真去找了——指引一个不存在的工具，比冗余更贵）
    "\n【文件即权威副本】世界文件就是你直接读写的这一份：file_write / file_edit / file_delete 改完即生效，没有「推送 / 同步」这一步，不要去找 world_push / world_pull（那套镜像同步只存在于 DSH 工作区侧，与本环境无关）。改完在回复里说明改了哪个文件。",
    # 限流机制（2026-08-21 新增：429 不是通道故障）
    "\n【限流机制】页面事件通道/群消息等写操作有 10 秒滑动窗口限流（配额 = api_group_msg_limit + api_group_msg_limit_per_user × 活跃人数）。HTTP 429 = 操作太快、稍后再试，不是通道故障、不是权限问题、不是代码没生效；前端对 429 不降级发群消息（只有 404/网络错误才降级）。排查时先确认是否 429 再怀疑其他。详见接口文档 10 分区。",
    # 记忆约定（精简：只保留结构化记忆一句，细节走文档）
    "\n【记忆约定】你的长期记忆（按世界隔离）一律用 manage_records 结构化存储（目录/子目录/字段三级），不用 store_memory/recall_memory（向量不可靠）；新会话开始时先 categories + 相关 get 检索再继续。",
    # UI 约定
    "\n【UI 约定】若你的页面实现了自己的侧边栏/菜单/导航，请调用 WorldUI.hideFloatingIcon() 隐藏平台悬浮图标（见接口文档），避免重复；未实现时不要调用。",
    # 群类型约定
    "\n【群类型约定】群类型 = slug（稳定 id）+ name（显示名）：绑定群/AI 存的是 slug，改名字只改 name、绝不能改 slug，否则已绑定的群/AI 会脱绑。世界未配置类型文件时系统自动提供「默认类型」（slug=default）兜底，群/AI 都可绑定。\n⚠️ 设计世界时必须写清楚：① 世界类型（群聊以什么类型进入）② AI 加入的类型（AI 以什么身份入驻）——关键剧本的角色可以直接创建以角色名或职位命名的类型（如 族长/骑士团长/商人），不只泛类型（冒险团/商会）。类型用 update_group_types 配置（slug 用英文小写稳定 id，上限可用 -1 = 无限，默认类型群/AI 都无限）。",
    # AI 侧 skill（按类型分层注入）
    "\n【AI 侧 skill】设计世界时不仅要写世界页面，还要考虑为 AI 居民写 skill：\n- skill 按类型分层注入——给不同类型的 AI 发不同的 skill（如 铁匠类型 → forge_weapon，商人类型 → trade）；skill 声明 types 字段（省略或 [*] = 所有类型通用，指定列表 = 仅这些类型可用）\n- 通过群聊进入世界的实体默认落到该群绑定的 AI 类型；AI 直接绑定世界 → 落到自己绑定的类型\n- skill 放 data/worlds/{id}/skills/ 下（manifest.json + code.py），居民像调普通工具一样 function calling 直接调用；你（造物主）是 skill 的颁布者，别忘了为每个类型设计配套能力\n- ⚠️ skill 设计准则（站在使用方角度）：可操作性（参数清晰、报错可读）、简便性（一次调用完成一件事）、效果最大化（一个完整意图做进一次调用）——AI 用最少次调用完成用户意图 = 好 skill；发布前模拟一遍 AI 调用路径，要调好几步才能办成事就合并",
    # 不同场景进入世界的 index 响应
    "\n【入口场景响应】依据进入场景，为同一个世界做不同的 index 响应：从群聊进入 / 从私信进入 / AI 直接进入，前端首页可以不同（如从私信进入 = 直接打开世界中与 AI 的对话的前端响应，而不是完整世界主页）。入口场景由平台注入变量（WORLD_ID / GROUP_ID / 入口类型），世界代码据此分发。",
    # 侧边栏约定
    "\n【侧边栏约定】世界侧边栏/菜单必须保留平台基础菜单（首页/聊天/世界列表/设置四个目的地，可折叠成可展开的「平台」项但绝不能缺失，否则用户无法回到主应用）；平台项跳主应用（window.parent），世界自定义项跳世界内页面。组名/项名/样式可自行调整，推荐直接应用 platform-sidebar 积木。⚠️ **侧边导航栏默认收起**（桌面端和移动端都一样）：默认只露一个展开入口（悬浮按钮/顶部按钮/边缘手柄），用户点击才展开导航；不能默认展开占满屏幕。移动端（<768px）同样默认收拢，至少实现可收拢功能。",
    # 路径约定
    "\n【路径约定】页面内资源（css/js/图片）一律用相对路径引用（支持跨文件夹 ../），不要用 / 开头的绝对路径（会 404）；数据请求用 /world/${WORLD_ID}/ 变量路径。",
    # 文件查询（2026-08-13 新增：对齐 OpenClaw read 工具设计——先定位再分段读，不整文件全读）
    "\n【文件查询】看文件不要整文件全读浪费上下文：先 file_grep 按关键词/正则定位（返回命中行+行号，path 可传目录或数组一次搜多文件，"
    "带 context=1~2 直接拿到上下文，多半就不用再 file_read），再用 file_read 的 offset/limit 按行分页读对应段落"
    "（返回 total_lines/start_line/end_line/truncated）。改文件用 file_edit 精确替换，编辑前只读目标区域即可；"
    "file_write / file_edit 落盘前会做语法自检、成功后回改动摘要（行数+首处改动），不用回读全文确认。",
    # 编号约定
    "\n【编号约定】不要硬编码任何编号（世界号/群号/用户 id）——世界编号 window.WORLD_ID、入口群聊编号 window.GROUP_ID 由平台注入变量，代码里一律用变量；群聊类工具默认作用于本世界绑定的群，直接说目的即可，无需也不应指定群号；成员 id 一律以 list_group_members 的返回为准。",
    # 设计美学（占一部分即可，控制 token）
    "\n【设计美学】世界是给人看的可视化界面，注重观感：配色协调有主色调、层级清晰（标题/正文/按钮主次分明）、留白适当不拥挤；移动端适配（窄屏可用）；动效克制、有加载/空状态；第一次进页面知道怎么玩（引导/提示）。丑的界面用户不玩。",
    # 界面硬性要求（产品 2026-08-13 定：手机适配 + 全前端化 + 操作不借道群聊）
    "\n【界面硬性要求】（硬约束，每次创建/修改页面都必须遵守）\n"
    "- 必须适应手机宽度很小的手机：界面响应式，在 320px 窄屏下不溢出、可操作、可读（字号不小于 12px，可点击目标不小于 44px）\n"
    "- 必须直接前端化整个界面，**操作和视图渲染都要前端化**：\n"
    "  ① 视图渲染前端化：界面就是 HTML/CSS/JS 前端页面本身，页面 DOM 由前端 JS 渲染（createElement/模板字符串/框架），不依赖后端渲染模板（不靠后端输出 HTML 片段再塞进页面）\n"
    "  ② 操作前端化：页面操作（点击/输入/状态切换/局部更新）在前端直接完成——能用前端 JS 处理的就不走后端；需要后端数据/持久化时才调接口（相对路径 fetch 或受控数据 API）\n"
    "- **操作绝不借道群聊**：页面上的每个按钮/入口都要有直接的点击反馈（前端直接执行或调事件通道），**禁止写「从群聊进入/群内直接发指令：探索 战斗 商店…」这类引导文案**，禁止让用户离开页面去群里发指令才能操作；页面操作一律走事件通道（06 分区 4.1，POST /world/{id}/api/event），世界程序回复用 SSE 状态（publish）。只有真正关键、需要别人在群里看到的事件（如宣布重大结果、求助他人）才用群消息 API",
    # 消息同步纪律（产品 2026-08-13 定）
    "\n【消息同步纪律】（产品定）不要把内容无节制同步到群聊：\n- 群消息是稀缺资源，只在用户真正需要/期待在群里看到时才发布（回复提问、宣布重要事件、被 @ 时）\n- 前端/沉浸界面能展示或响应的内容（状态变化、进度、中间过程、提示信息）一律不同步到群——页面自会呈现，同步即噪音\n- 同一事件不要既发群消息又在界面重复展示；宁可少发，不可轰炸\n- 批量/例行通知（定时、状态刷新、系统噪音）默认不发群，除非用户明确要求",
    # 文件与下载纪律（产品 2026-09-15 定：内容红线 + 程序文件红线 + 固定落点）
    "\n【文件与下载纪律】\n"
    "- 严禁下载色情、暴力、违法内容：平台按链接/文件名/正文关键词拦截并记录，命中即失败——不要换个链接再试；\n"
    "- 严禁下载可执行文件、安装包与脚本（.exe/.msi/.dll/.bat/.cmd/.sh/.ps1/.vbs/.jar/.apk 等）："
    "创建时直接拒绝，目录里遗留的会被强制删除。世界代码只有 .js 与 .py 两种（.py 在沙箱里执行），其余内容一律写成网页资源或纯文本；\n"
    "- 下载一律落在固定目录 downloads/ 下，不要往别处塞下载来的文件；\n"
    "- 移动/复制/删除世界文件用 file_move / file_copy / file_delete（都限定在世界目录内）；\n"
    "- 沙箱已做隔离（文件系统 Landlock + 系统调用 seccomp，禁 execve/网络/挂载）：不要试图执行外部程序或绕过限制。",
    # 世界运行规范
    "\n【世界运行规范】\n- 沙箱环境：世界代码（main.py / 沙箱脚本）的工作目录 = 世界文件夹本体，可直接读写世界文件夹里的文件（含 JSON 数据文件）；环境变量注入 WORLD_ID / WORLD_API_TOKEN / WORLD_API_BASE / WORLD_DIR（token 只用于受控 API，绝不外泄/打印/写进页面）\n- 数据规范（代码/数据分离）：\n  1) 结构化/操作数据（状态、计数、记录）→ 用受控 API 的世界数据库：GET/PUT/DELETE /data/{key}（key 用命名空间如 player.lihua / poems / quest.1，value 任意 JSON）——世界代码与页面三方共用同一份数据\n  2) 静态文字类（设定、文档、素材文本）→ 放世界文件夹 content/ 子目录（可自由建层级；content/ 是世界产物区，发布世界不打包，下载数据可选包含）\n  3) 代码（网页/脚本）放世界文件夹根目录或自有目录\n- 页面读数据：经世界代码发布状态（POST /state → 页面 SSE）或世界代码生成页面时内嵌；页面不要直连数据库\n【内容提炼与动态加载】（产品 2026-08-12 定）剧情/按钮列表/大量设定等**内容不准写死在渲染中**：能提炼为文档/列表/数据文件的提炼掉，页面动态加载（fetch/import）；不固定数目；图片/音频等资源同理；同构多实例（NPC/卡牌/角色）**每个实例一个文件**（如 npcs/lihua.json）。改内容/新增实例都不碰渲染代码。\n【结构约定】注意维护和优化世界的项目结构：文件按职责组织（页面/样式/脚本/数据分开），定期清理无用文件，代码保持整洁可维护——世界会长期演进，结构混乱会让后续修改越来越难。⚠️ **适时拆分文件**：文件一旦变大（或你觉得对维护不利）就拆分——拆成职责单一的小文件/模块，别让单个文件越来越臃肿；拆分的判断标准：这个文件继续变大会不会让后续修改变难？会就拆。",
    # 常驻模式与实时通道
    "\n【常驻模式与实时通道（强约束）】\n- resident=true 才是常驻世界：有独立后台进程持续运行，可实时接收事件、推演状态、广播变化。\n- resident=false 为非常驻世界：仅在用户操作时临时执行，不支持实时交互。\n- 页面实时交互必须使用 WebSocket：WS /world/{id}/realtime\n  仅常驻世界可用；非常驻世界连接会返回 NOT_RESIDENT。\n- 需要实时交互的世界，必须先将 resident 设为 true，并在唤醒世界或重启后端后生效。\n- 禁止在世界前端用短间隔 setInterval 轮询 /state 或 /api/event 代替实时通道。\n- 常驻世界会占用服务器资源，默认保持非常驻；只有明确需要实时交互时才开启。",
]


def build_forced_prompt() -> str:
    """强注入提示词全文（只读展示用，与对话组装完全一致）"""
    return "".join(FORCED_PROMPT_SEGMENTS)


def _mark(value: str) -> str:
    """重要记忆标记（软锚定）：value 以 ⭐/❗ 开头 → 名字加对应前缀。

    产品定：重要度覆盖整个级的记忆可直接写在该级（value 前缀标记），
    地图生成时把标记提到名字上，产生 Attention 特征峰值。
    """
    v = (value or "").strip()
    if v.startswith("❗"):
        return "❗"
    if v.startswith("⭐"):
        return "⭐"
    return ""


async def build_memory_map(world_repo: WorldRepository, world_id: int) -> str | None:
    """记忆地图（缩进树）：clear/new/compact 后注入，让 AI 知道有什么记忆存档。

    格式（省 token + 软锚定）：
    【本世界记忆】\nproject/\n  图鉴页面/\n    进度\n  ⭐当前计划\nuser/\n  ⭐偏好\n详细内容用 manage_records get 按需取。

    规则：只注入有内容的路径（空目录不出现）；重要记忆 ⭐ 前缀（软锚定，注意力倾斜）；
    硬约束 ❗ 前缀；详细 value 不注入（第 4 级按需 get）。
    """
    from app.models.world import WorldStructuredRecord
    from sqlalchemy import select as _sel

    rows = (await world_repo.execute(
        _sel(WorldStructuredRecord).where(
            WorldStructuredRecord.world_id == world_id
        ).order_by(WorldStructuredRecord.category, WorldStructuredRecord.sub_key, WorldStructuredRecord.field)
    )).scalars().all()
    if not rows:
        return None

    # 分组：category → {sub_key: {field: value}}，sub_key 为空串时挂 category 下
    cats: dict[str, dict[str, dict[str, str]]] = {}
    for r in rows:
        cats.setdefault(r.category, {}).setdefault(r.sub_key, {})[r.field] = r.value
    lines = ["【本世界记忆】"]
    for cat in sorted(cats):
        subs = cats[cat]
        if not subs or (len(subs) == 1 and "" in subs):
            # 只有无 sub_key 的记录：直接列出 field（带重要标记的加前缀）
            fields = subs.get("", {})
            if fields:
                lines.append(f"{cat}/")
                for f in sorted(fields):
                    lines.append(f"  {_mark(fields[f])}{f}")
            continue
        lines.append(f"{cat}/")
        for sk in sorted(subs):
            if sk == "":
                continue
            fields = {f: v for f, v in subs[sk].items() if f}
            if fields:
                lines.append(f"  {sk}/")
                for f in sorted(fields):
                    lines.append(f"    {_mark(fields[f])}{f}")
            else:
                lines.append(f"  {sk}")
    lines.append("⭐=重要记忆 ❗=硬约束（详细内容用 manage_records get 按需取）")
    return "\n".join(lines)




def turn_completed(full_content: str, had_error: bool) -> bool:
    """这轮算不算**正常结束**（决定清不清「未完成工作流」记忆）。

    只看"正文是否以「（」开头"是不够的：出错时正文被换成友好提示（「💰 …余额不足（402）…」、
    「⏳ 请求太频繁…」），它**不以「（」开头** —— 那样会把刚写好的未完成工作流记忆清掉，
    而那条提示里恰恰写着「已记录未完成的工作流，充值后说「继续」即可接着做」（用户 2026-09-23）。
    """
    text = str(full_content or "")
    return bool(text) and not had_error and not text.startswith("（")


def _friendly_llm_error(err) -> str:
    """把 LLM 错误转成友好提示（余额不足/鉴权/限流/服务端）"""
    text = str(err)
    low = text.lower()
    if "402" in text or "insufficient balance" in low or "余额" in text:
        return "💰 世界 AI 余额不足（402）：请为 API 账号充值或更换有效 Key。已记录未完成的工作流，充值后说「继续」即可接着做。"
    if "401" in text or "authentication" in low or "invalid api key" in low or "403" in text:
        return "🔑 世界 AI 的 API Key 无效（401/403）：请在 API 配置中检查更新。"
    if "429" in text or "rate limit" in low:
        return "⏳ 请求太频繁（429 限流）：稍等片刻再试。"
    if "500" in text or "503" in text or "server error" in low or "busy" in low:
        return "🔧 DeepSeek 服务繁忙（5xx）：稍后再试。"
    return text[:200]


def _now_local() -> datetime:
    """本模块的时间戳（避免跨模块依赖 _now）"""
    from datetime import datetime as _dt, timezone as _tz
    return _dt.now(_tz.utc)


def _ensure_aware(dt: datetime) -> datetime:
    """确保 datetime 是 timezone-aware（旧数据存的是 naive UTC，补上 tzinfo）"""
    from datetime import timezone as _tz
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_tz.utc)
    return dt


def _is_json_line(line: str) -> bool:
    """行是否为完整可解析的 JSON（裁剪时跳过损坏行）"""
    try:
        json.loads(line)
        return True
    except Exception:
        return False


def _log_llm_request(world_id: int, turn_id: str, round_no: int, model: str, thinking: bool, messages: list) -> None:
    """保存一次实际 LLM 请求（消息全量），保留最近 10 条/世界

    append 模式（O_APPEND 行缓冲）：不再整文件重写，多轮/并发调用不会互相
    覆盖截断；超过 2 倍保留数时裁剪重写一次（顺带清掉损坏行）。
    """
    try:
        d = LLM_REQUEST_LOG_DIR
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{world_id}.jsonl"
        entry = {
            "ts": _now_local().isoformat(),
            "turn_id": turn_id,
            "round": round_no,
            "model": model,
            "thinking": thinking,
            "messages": messages,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with open(path, "a", encoding="utf-8", buffering=1) as f:
            f.write(line + "\n")
        # 定期裁剪：超过 2x 保留数 → 只留最近 N 条（跳过损坏行）
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            good = [ln for ln in lines if ln.strip() and _is_json_line(ln)]
            if len(good) > LLM_REQUEST_KEEP * 2:
                path.write_text("\n".join(good[-LLM_REQUEST_KEEP:]) + "\n", encoding="utf-8")
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 请求日志保存失败: {e}")


async def _record_usage(world_repo, world_id: int, turn_id: str, round_no, model: str, usage: dict | None, messages: list | None = None) -> None:
    """LLM 用量落库：
    - world_llm_usage：每世界缓存命中统计（2.7）
    - conversation_log：归入用户「群视界 agent」（个人 API 用量页可见）
    """
    if not usage:
        return
    try:
        from app.models.world import WorldLLMUsage
        world_repo.add(WorldLLMUsage(
            world_id=world_id,
            turn_id=turn_id,
            round_no=str(round_no),
            model=model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
            cached_tokens=_extract_cached_tokens(usage),
        ))
        await world_repo.flush()
        # 个人 API 用量：记账人 = 世界 AI 表单的世界主人（user_id 直记，查询时虚拟聚合「群视界 agent」）
        if messages:
            from app.services.content.conversation_log_service import save_conversation_log
            from app.models.world import World
            world = await world_repo.get(World, world_id)
            if world is not None:
                await save_conversation_log(
                    world_repo, None, messages, conversation_type="world",
                    token_usage=usage, model=model, thinking_enabled=False,
                    user_id=world.owner_id,
                )
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 用量记录失败: {e}")


def _extract_cached_tokens(usage: dict | None) -> int:
    """提取缓存命中 token：DeepSeek 各接口返回位置不一——
    顶层 cached_tokens（首轮）｜prompt_tokens_details.cached_tokens（工具轮）｜
    prompt_cache_hit_tokens（兼容）；2026-08-13 修复（之前只读顶层 → 工具轮全记 0）。"""
    if not usage:
        return 0
    v = usage.get("cached_tokens")
    if v is None:
        v = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if v is None:
        v = usage.get("prompt_cache_hit_tokens")
    return int(v or 0)


def world_context_block(world) -> str:
    """世界档案：注入给世界 AI 的自身信息（名字/简介/状态）——⚠️ 不含世界时间！

    世界时间是动态的（每次对话 apply_time_compensation 都变），放这里会破坏
    DeepSeek 前缀缓存（system 第一句就变 → 全 miss）。世界时间放 messages 尾部。
    """
    return (
        "【你的世界档案】\n"
        f"- 世界名：{world.name}\n"
        f"- 简介：{world.description or '（无）'}\n"
        f"- 状态：{world.status}\n"
        f"- 时间流速：{world.time_flow_rate}x\n"
        f"- 你的身份：world-{world.id}\n"
        "你可以用 update_world_info 工具修改世界名/简介。"
    )


# 世界 AI 对话上下文（与主对话压缩机制一致：128K 窗口 60% 触发提示，AI 调 compact 压缩）
# 条数一律按**真实消息**（user/ai）算——文档 7.15：模型真正带的只有这两种角色，
# 按数据库行数算会被工具卡片/思考挤成 0 条（world #45 实测 30 行里只剩 1 条真实消息）
WORLD_CHAT_KEEP_LAST = 10          # 压缩后保留的最近真实消息数
# 少于 N 条不提示压缩。必须比保留窗口多 1：真实消息数 ≤ 保留窗口时压缩是**空操作**，
# 否则会出现"提示 AI 去压缩、压了却无可压缩"（旧值 6 < 10，正好落在这个空区间里）
WORLD_CONTEXT_MIN_MESSAGES = WORLD_CHAT_KEEP_LAST + 1
DEFAULT_MAX_TOOL_ROUNDS = 50       # 改动预算默认上限（可在设计页配置 max_tool_rounds 覆盖）
ROUND_BUDGET_CEILING = 200         # 改动预算硬上限（含计划模式申请的提额）
READ_ROUND_FACTOR = 2              # 只读预算 = 改动预算 × 2（默认 50 → 100）
READ_ROUND_CEILING = 400           # 只读预算硬上限（申请提额也封在这里）


def resolve_round_budget(ai_cfg: dict) -> int:
    """**改动**预算的唯一口径：世界配置 max_tool_rounds（默认 50，硬顶 200）"""
    raw = (ai_cfg or {}).get("max_tool_rounds")
    if raw in (None, ""):
        return DEFAULT_MAX_TOOL_ROUNDS
    try:
        return max(1, min(int(raw), ROUND_BUDGET_CEILING))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOOL_ROUNDS


def resolve_read_budget(ai_cfg: dict) -> int:
    """**只读**预算的唯一口径：改动预算 × 2，硬顶 400（用户 2026-09-23：搜文件不该吃改动额度）。"""
    return min(resolve_round_budget(ai_cfg) * READ_ROUND_FACTOR, READ_ROUND_CEILING)


def current_budgets(turn_state: dict, ai_cfg: dict) -> tuple[int, int]:
    """本轮实际可用的 (改动, 只读) 预算。

    **每轮都要重新读**：用户在弹窗里批准提额是写在 turn_state 上的，循环外只算一次的话
    批准当轮就不生效（旧写法正是如此，present_plan 的 tool_rounds 提额从来没生效过）。
    """
    state = turn_state or {}
    write = min(int(state.get("round_budget") or 0) or resolve_round_budget(ai_cfg), ROUND_BUDGET_CEILING)
    read = min(int(state.get("read_round_budget") or 0) or resolve_read_budget(ai_cfg), READ_ROUND_CEILING)
    return write, read


def granted_read_budget(current_read: int, granted: int) -> int:
    """批准增加只读预算后的新值：当前 + 申请量，硬顶封住（唯一算法）。"""
    return min(int(current_read) + max(1, int(granted)), READ_ROUND_CEILING)


def is_write_round(tool_names) -> bool:
    """这一批工具算不算**改动轮**：只要有一个改动类工具就算（同批的只读免费跟着走）。

    判定复用 world_ai_mode.action_of（SAFE_TOOLS = 无副作用）—— 只读名单只有一份。
    """
    from app.services.world.world_ai_mode import action_of
    return any(action_of(str(name or "")) is not None for name in tool_names)


def round_budget_error(tool_name: str, turn_state: dict, write_budget: int, read_budget: int) -> str | None:
    """这个工具现在还能不能执行：预算见底的类型返回拒绝理由（进 tool 结果，不打断调用链）。

    不抛异常、不算失败——悬空的 tool_calls 会让下一次请求直接 400（2026-09-14 事故）。
    """
    from app.services.world.world_ai_mode import action_of
    state = turn_state or {}
    if action_of(str(tool_name or "")) is not None:
        if int(state.get("write_rounds", 0)) >= write_budget:
            return (f"改动预算已用尽（{write_budget} 轮）：本轮不再执行任何改动类工具（写文件/删除/下载等）。"
                    "请立刻收尾：把已完成的落盘，并在正文里写清还欠什么、下一步怎么做。")
        return None
    if int(state.get("read_rounds", 0)) >= read_budget:
        return (f"只读预算已用尽（{read_budget} 轮）：本轮不再执行只读工具（读/搜/列目录等）。"
                "确实还需要读，就用 request_read_budget 向用户申请增加（写清还要几轮、为什么），"
                "用户批准当轮立刻生效；不需要就按现有信息收尾。")
    return None


def prefix_capability_sources(world_id: int) -> list[str]:
    """进前缀的能力源：compact / 清空上下文 / 会话起点解锁共用同一份口径（要改只改这里）"""
    return ["ai-skills", f"world-prompt-{world_id}", "forced-prompt", f"world-name-{world_id}"]


def build_budget_prompt(max_rounds: int, read_rounds: int | None = None) -> str:
    """本轮预算段（进前缀；按世界配置稳定 → 缓存友好）。

    世界 AI 2026-09-18 反馈：「本轮末提示只剩 3 轮：大改容易卡在半途」——它到快用完才知道上限。
    开局就给数，它才能规划"先落盘再验证"，而不是撞墙。

    2026-09-23 用户要求：**读的预算单独一本账**（搜文件不该吃掉改动额度），
    不够可以申请提额 —— 所以开头就把两本账和申请入口一起讲清楚。
    """
    read_rounds = resolve_read_budget({"max_tool_rounds": max_rounds}) if read_rounds is None else read_rounds
    return (
        f"\n【本轮预算】两本账分开记，开局就按这个数规划：\n"
        f"- 改动预算 {max_rounds} 轮：写文件、改文件、删除、下载这类**有副作用**的操作，一轮一个（同一轮里改几个文件也只算一轮）。\n"
        f"- 读取预算 {read_rounds} 轮：整轮都是只读的（file_read / file_grep / file_list / view_api_doc 等）记在读取账上，"
        "**不占改动预算**；一轮里夹了改动类工具就按改动轮记账。\n"
        "每轮都会告诉你两本账各还剩多少。改动预算只剩 5 轮会提醒你收尾、剩 2 轮禁止开启新工作；"
        f"读取预算不够时用 request_read_budget 向用户申请增加（写清还要几轮、为什么，用户批准当轮立刻生效，"
        f"硬顶 {READ_ROUND_CEILING} 轮）；计划模式下也可在 present_plan 里带 tool_rounds 申请**改动**预算的提额"
        f"（上限 {ROUND_BUDGET_CEILING} 轮，用户批准计划即同时批准提额）。"
    )


async def session_is_fresh(world_repo, world) -> bool:
    """本会话还没有任何真实消息（新会话 / 刚清空）——前缀反正要重建，可以安全解锁能力变更"""
    try:
        rows = await get_chat_history(
            world_repo, world.id, 1, session_id=session_id_for_db(world), roles=REAL_ROLES,
        )
        return not rows
    except Exception as e:
        logger.warning(f"🌐 世界 #{world.id} 会话起点判定失败（按非起点处理）: {e}")
        return False


CHAT_HISTORY_LIMIT = 30  # 没有摘要时携带的最近真实消息数
# 进模型上下文的角色：tool（工具卡片）与 note（思考过程）只给用户看——聊天与压缩共用这份口径
REAL_ROLES = ("user", "ai")


def session_key(world) -> str:
    """当前会话 key（world.config.current_session；无 = 默认会话 'default'）"""
    return (world.config or {}).get("current_session") or "default"


def session_id_for_db(world) -> str | None:
    """落库用的 session_id：默认会话存 NULL（兼容旧数据），/new 会话存 uuid"""
    return (world.config or {}).get("current_session") or None

def session_settings(world, global_defaults: dict | None = None) -> dict:
    """会话生命周期配置：世界配置（设计页可改）> 全局默认（管理员 system_settings）> 代码默认
    auto_new_enabled / auto_new_time("04:00") / compact_idle_hours(18, 0=关) / retention_days(90, 0=关)"""
    g = global_defaults or {}
    cfg = (world.config or {}).get("session_settings") or {}
    return {
        "auto_new_enabled": bool(cfg.get("auto_new_enabled", g.get("auto_new_enabled", True))),
        "auto_new_time": str(cfg.get("auto_new_time") or g.get("auto_new_time") or "04:00"),
        "compact_idle_hours": int(cfg.get("compact_idle_hours") or g.get("compact_idle_hours") or 18),
        "retention_days": int(cfg.get("retention_days") or g.get("retention_days") or 90),
    }

def new_session_id(world) -> str:
    """生成会话 id：w{wid}:{type}:{uuid12}；与已存在会话碰撞则重试（uuid 防碰撞）"""
    import uuid as _uuid
    sessions = ((world.config or {}).get("sessions") or {})
    typ = "m"  # 分类：m=设计页主对话（预留 g{群id}=群入口）
    while True:
        sid = f"w{world.id}:{typ}:{_uuid.uuid4().hex[:12]}"
        if sid not in sessions:
            return sid


async def create_new_session(world_repo, world) -> str:
    """开一个新会话并切过去，返回新会话 id。

    只动 world.config（current_session + sessions 登记），一条历史都不碰——
    "/new 文本命令"与前端"新对话"按钮共用这一个入口，避免两处各写一遍。
    """
    from datetime import datetime as _dt, timezone as _tz

    cfg = dict(world.config or {})
    sid = new_session_id(world)
    now = _dt.now(_tz.utc).replace(tzinfo=None).isoformat()
    sessions = dict(cfg.get("sessions") or {})
    sessions[sid] = {"created_at": now, "last_active_at": now}
    cfg["current_session"] = sid
    cfg["sessions"] = sessions
    world.config = cfg
    await world_repo.commit()
    return sid


def touch_session(world) -> None:
    """更新当前会话元信息（last_active_at）"""
    from datetime import datetime, timezone
    cfg = dict(world.config or {})
    sessions = dict(cfg.get("sessions") or {})
    key = (cfg.get("current_session") or "default")
    meta = dict(sessions.get(key) or {})
    meta["last_active_at"] = datetime.now(timezone.utc).isoformat()
    sessions[key] = meta
    cfg["sessions"] = sessions
    world.config = cfg


SESSION_TITLE_MAX = 20            # 对话名上限（短名字才好在列表里看）


def normalize_session_title(raw: str) -> str | None:
    """对话名清洗：去空白/换行、压空格、限长；空 → None（纯函数，便于测试）"""
    title = " ".join(str(raw or "").split())
    if not title:
        return None
    return title[:SESSION_TITLE_MAX]


def set_session_title(world, title: str, session_id: str | None = None) -> str | None:
    """给会话命名/改名（纯函数：只改 world.config，调用方负责 commit）。

    session_id 省略 = **当前会话**（AI 的 rename_session 走这条）；前端会话列表要改
    "列表里的任意一场"时显式传 id（含 'default'）——清洗规则、存储位置都只有这一处。
    返回规范化后的名字；名字为空则清除命名（回落到默认显示）。
    """
    name = normalize_session_title(title)
    cfg = dict(world.config or {})
    key = (cfg.get("current_session") or "default") if session_id is None else (session_id or "default")
    sessions = dict(cfg.get("sessions") or {})
    meta = dict(sessions.get(key) or {})
    if name:
        meta["title"] = name
    else:
        meta.pop("title", None)
    sessions[key] = meta
    cfg["sessions"] = sessions
    world.config = cfg
    return name


async def list_sessions(repo, world) -> list[dict]:
    """会话列表（单一来源，含「最近聊天时间」）。

    - 时间取**该会话最后一条消息的时间**（权威），config 里的 last_active_at 只作兜底——
      老会话/默认会话原本没有时间，前端列表会有一半空白
    - 默认会话（session_id 为空 = 旧数据入口）也在列，否则用户找不到它
    - **按最近聊天时间倒序**：最近用过的排最前
    """
    from datetime import datetime as _dt
    from sqlalchemy import func as _f, select as _sel
    from app.models.world import WorldChatMessage

    cfg = world.config or {}
    meta = dict(cfg.get("sessions") or {})
    rows = (await repo.execute(
        _sel(WorldChatMessage.session_id, _f.max(WorldChatMessage.created_at))
        .where(WorldChatMessage.world_id == world.id)
        .group_by(WorldChatMessage.session_id)
    )).all()
    last_msg: dict = {sid: ts for sid, ts in rows}

    def _iso(value):
        return value.isoformat() if isinstance(value, _dt) else value

    out: list[dict] = []
    for sid in list(meta) + [s for s in last_msg if s]:
        if sid not in meta and not last_msg.get(sid):
            continue
        item = meta.get(sid) or {}
        out.append({
            "id": sid,
            "title": item.get("title"),
            "created_at": item.get("created_at"),
            "last_active_at": _iso(last_msg.get(sid)) or item.get("last_active_at"),
            "pinned": bool(item.get("pinned_by")),
        })
    if None in last_msg:                      # 默认会话：session_id 为空的历史
        item = meta.get("default") or {}
        out.append({
            "id": "default", "title": item.get("title"), "created_at": item.get("created_at"),
            # 默认会话没有消息时也别空着：回落到 config 记的活跃时间（新会话就是刚建的）
            "last_active_at": _iso(last_msg[None]) or item.get("last_active_at"),
            "pinned": bool(item.get("pinned_by")),
        })
    # 去重（config 里万一同名存过 default）+ 按最近聊天倒序（没时间的排最后）
    seen, uniq = set(), []
    for s in out:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        uniq.append(s)
    uniq.sort(key=lambda s: s["last_active_at"] or "", reverse=True)
    return uniq


async def ensure_session_lifecycle(world_repo, world) -> dict:
    """懒加载会话生命周期检查（GET/POST /chat 入口调用，无定时器）：

    - auto_new：跨过每日 auto_new_time（默认 04:00）→ 自动开新会话（已 new 过不重复）

    - retention：未收藏会话超过保留天数 → 删除（id+消息+列表）

    - idle_compact 不在此处（放 stream_world_chat 开头，需 LLM 压缩）

    返回 {auto_newed: bool, cleaned: int}"""
    from datetime import datetime, timezone, timedelta
    from zoneinfo import ZoneInfo
    from app.models.world import WorldChatMessage
    from sqlalchemy import delete as sa_delete

    # auto_new 按用户时区（display_timezone）算；retention 用 UTC（时长比较与基准无关）
    from app.config import settings as _settings
    tz = ZoneInfo(_settings.display_timezone)
    local_now = datetime.now(tz)
    now_utc = datetime.now(timezone.utc)
    cfg = dict(world.config or {})
    st = session_settings(world)
    result = {"auto_newed": False, "cleaned": 0}

    # ── auto_new：跨过配置时间点（用户时区）→ 开新会话 ──
    if st["auto_new_enabled"]:
        try:
            hh, mm = str(st["auto_new_time"]).split(":")[:2]
            today_cutoff = local_now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            today_cutoff = local_now.replace(hour=4, minute=0, second=0, microsecond=0)
        # 最近一次应 new 的时间点：现在未到今天的配置点（凌晨）→ 取昨天的；否则取今天的
        # ⚠️ 若直接取「今天」的配置点，凌晨时它在未来 → last 永远 < cutoff → 每次访问都开新会话（会话爆炸）
        cutoff = today_cutoff - timedelta(days=1) if local_now < today_cutoff else today_cutoff
        last = cfg.get("last_auto_new_at")
        if last is None or last < cutoff.isoformat():
            # 跨过时间点且还没 new 过 → 开新会话（旧会话保存，可切回）
            cfg["current_session"] = new_session_id(world)
            sessions = dict(cfg.get("sessions") or {})
            sessions[cfg["current_session"]] = {
                "created_at": local_now.isoformat(),
                "last_active_at": local_now.isoformat(),
            }
            cfg["sessions"] = sessions
            cfg["last_auto_new_at"] = local_now.isoformat()
            world.config = cfg
            await world_repo.commit()
            result["auto_newed"] = True

    # ── retention：清理过期未收藏会话 ──
    days = st["retention_days"]
    if days > 0:
        sessions = dict(cfg.get("sessions") or {})
        expired = []
        for sid, meta in sessions.items():
            if sid == (cfg.get("current_session") or "default"):
                continue  # 当前会话不清理
            if (meta or {}).get("pinned_by"):
                continue  # 收藏的会话不清理
            la = (meta or {}).get("last_active_at")
            if not la:
                continue
            try:
                la_dt = datetime.fromisoformat(la)
            except Exception as e:
                # 解析不了就跳过该会话的过期判定 → 可能永远不被清理，留痕便于排查
                logger.warning(f"🌐 会话 {sid} 的 last_active_at 无法解析，跳过过期清理: {la!r} ({e})")
                continue
            if now_utc - _ensure_aware(la_dt) > timedelta(days=days):
                expired.append(sid)
        if expired:
            for sid in expired:
                q = sa_delete(WorldChatMessage).where(
                    WorldChatMessage.world_id == world.id,
                    WorldChatMessage.session_id == sid,
                )
                await world_repo.execute(q)
                sessions.pop(sid, None)
            cfg["sessions"] = sessions
            world.config = cfg
            await world_repo.commit()
            result["cleaned"] = len(expired)
    return result


async def get_chat_history(
    world_repo: WorldRepository, world_id: int, limit: int | None = 30,
    before_id: int | None = None, session_id: str | None = None,
    roles: tuple[str, ...] | None = None, since_id: int | None = None,
) -> list[dict]:
    """世界 AI 对话历史（最近 limit 条；limit=None 取全部）

    - before_id：传最旧 id 可翻更早（前端翻页）
    - session_id：过滤会话
    - roles：按角色过滤——模型上下文只取 REAL_ROLES；前端要工具卡片时不要传
    - since_id：只要这个 id 之后的（compact 边界锚定用）
    """
    from app.models.world import WorldChatMessage
    from app.tools.world import tool_label

    query = select(WorldChatMessage).where(WorldChatMessage.world_id == world_id)
    if session_id is None:
        query = query.where(WorldChatMessage.session_id.is_(None))
    else:
        query = query.where(WorldChatMessage.session_id == session_id)
    if roles is not None:
        query = query.where(WorldChatMessage.role.in_(roles))
    if before_id is not None:
        query = query.where(WorldChatMessage.id < before_id)
    if since_id is not None:
        query = query.where(WorldChatMessage.id > since_id)
    query = query.order_by(WorldChatMessage.id.desc())
    if limit is not None:
        query = query.limit(limit)
    result = await world_repo.execute(query)
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "reasoning": m.reasoning if m.role in ("ai", "note") else None,
            "is_error": bool(m.is_error) if m.role == "tool" else None,
            # 工具卡片：是哪个工具 + 点开看详情（历史行 tool_name 为空的是老数据，前端自动退化）
            "tool_name": m.tool_name if m.role == "tool" else None,
            "tool_label": (tool_label(m.tool_name) or None) if m.role == "tool" else None,
            "tool_detail": m.tool_detail if m.role == "tool" else None,
            "attachments": m.attachments,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in reversed(result.scalars().all())
    ]


async def _save_note_separated(
    world_repo: WorldRepository, world_id: int, content: str, reasoning: str, session_id: str | None,
) -> None:
    """落库中间轮 note：正文/思考拆成两条独立 note（2026-08-13）。

    流式事件里正文和思考是独立气泡（contentTargetId/reasoningTargetId），
    落库也必须拆开——否则刷新后 loadChat 拿到合并 note，前端渲染成普通气泡
    （思考塞 details 里），思考折叠/独立展示失效。
    """
    from app.models.world import WorldChatMessage
    # 顺序对齐流式：思考先落库、正文后落库（思考气泡在正文上方）——
    # 否则刷新后正文 note id < 思考 note id，渲染顺序反了
    if reasoning:
        world_repo.add(WorldChatMessage(
            world_id=world_id, user_id=None, role="note",
            content="", reasoning=reasoning, session_id=session_id,
        ))
    if content:
        world_repo.add(WorldChatMessage(
            world_id=world_id, user_id=None, role="note",
            content=content[:4000], reasoning=None, session_id=session_id,
        ))
    await world_repo.commit()


async def _save_ai_reply(world_repo: WorldRepository, world, content: str, reasoning: str, session_id: str | None = None) -> None:
    """落库 AI 回复（含思考过程）；内容为空则跳过"""
    from app.services.world.world_service import _now
    from app.models.world import WorldChatMessage
    if not content:
        return
    world_repo.add(WorldChatMessage(
        world_id=world.id, user_id=None, role="ai",
        content=content, reasoning=reasoning or None,
        session_id=session_id,
    ))
    world.last_active_at = _now()
    await world_repo.commit()


async def _resolve_world_credentials(world_repo: WorldRepository, world) -> tuple[str | None, str]:
    """世界 AI 计费/凭证：账单人 = 世界主人（主人 Key → 池 Key → 全局默认 base）"""
    from app.config import settings
    from app.models.user import User

    api_key, api_base = None, settings.deepseek_base_url
    owner = await world_repo.get(User, world.owner_id) if world.owner_id else None
    if owner is not None:
        try:
            from app.utils.crypto import decrypt_api_key
            if owner.api_key_encrypted:
                api_key = decrypt_api_key(owner.api_key_encrypted)
                api_base = owner.api_base_url or settings.deepseek_base_url
            else:
                from app.services.infrastructure.quota_service import find_best_pool_key
                pool_key = await find_best_pool_key(world_repo, owner.id)
                if pool_key:
                    api_key = decrypt_api_key(pool_key.api_key_encrypted)
                    api_base = pool_key.api_base_url or settings.deepseek_base_url
        except Exception as e:
            # 密钥解密失败等：降级到无 Key（走全局默认 base），让 LLM 层报清晰错误
            logger.warning(f"🌐 世界 #{world.id} 凭证解析降级: {e}")
            api_key, api_base = None, settings.deepseek_base_url
    return api_key, api_base


async def resolve_world_chat_model(
    world_repo: WorldRepository, world, api_base: str, wai=None,
) -> str:
    """世界 AI 未显式指定模型时的默认模型解析——**唯一入口**。

    world_chat_service / app.tools.world / world_suggestions 三处共用，
    避免同一段优先级链被手抄多份后各自漂移。

    优先级（高 → 低）：
      1. 世界AI 自己指定的模型（wai.model）
      2. 世界主人的用户级覆盖 users.global_chat_model（用户在 /settings 里配的）
      3. 提供商配置 system_settings.provider_config 的 global_default_chat_model（管理员配的）
      4. 提供商预设 provider_presets.PRESETS 的 chat_model
      5. 平台全局默认 settings.default_chat_model

    提供商一律按 base_url 匹配 api_base；管理员配置优先于内置预设。
    """
    from app.config import settings
    from app.models.user import User
    from app.utils.pure.provider_config import find_provider_by_base_url

    model = getattr(wai, "model", None)
    if model:
        return model

    # 1. 世界主人的用户级覆盖
    owner = await world_repo.get(User, world.owner_id) if world.owner_id else None
    model = getattr(owner, "global_chat_model", None) if owner else None
    if model:
        return model

    # 2. 管理员配置的提供商（get_providers 返回数组；get_default_provider 只返回默认项，不能用于匹配）
    from app.services.infrastructure.system_settings_service import get_providers
    from app.services.agent.provider_presets import PRESETS

    provider = find_provider_by_base_url(await get_providers(world_repo), api_base)
    # 3. 管理员没配这个 base_url → 回退内置预设
    if provider is None:
        provider = find_provider_by_base_url(list(PRESETS.values()), api_base)
    if provider:
        model = provider.get("global_default_chat_model") or provider.get("chat_model")
        if model:
            return model

    # 4. 平台全局默认
    return settings.default_chat_model


async def _stream_llm_once(
    world_id: int, turn_id: str, round_no: int,
    model: str, thinking: bool, api_base: str, api_key: str | None,
    messages: list, tools, cfg: dict,
    out: dict | None = None,
):
    """单次 LLM 流式调用：逐 chunk yield SSE 事件（正文/思考），并收集完整结果。

    2026-08-13 修复：工具轮（round 2+）之前复用 chat_completion（聚合式）——
    正文/思考要等整次调用结束才一次性出现，没有流式效果。
    提取首轮的手写流式解析为公共生成器，工具轮也走它。

    用法：
        out = {}
        async for event in _stream_llm_once(..., out=out):
            yield event          # 外层再转发（或丢弃）
        # 之后 out 里是完整 content/reasoning_content/tool_calls/usage
    """
    import httpx
    _log_llm_request(world_id, turn_id, round_no, model, thinking, messages)
    # 构造 payload（与首轮完全一致——字段+顺序都要相同！DeepSeek 前缀缓存 key
    # 若基于序列化字符串，顺序不同也 miss。2026-08-13 修复：昨天 chat_completion 与
    # 首轮同构所以命中；今天 _stream_llm_once 顺序/字段有差异 → 工具轮全 miss）
    payload = {
        "model": model,
        "messages": messages,
        "temperature": cfg.get("temperature", 0.8),
        "top_p": cfg.get("top_p", 0.9),
        # 输出预留挡上下文预算（DeepSeek 算 prompt + max_tokens ≤ 窗口）：原先 64000 会让
        # 200k 级的长工具轮直接顶到窗口；32000 足够单个文件写入，还给长上下文留了余量
        "max_tokens": STREAM_MAX_TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if thinking:
        payload["thinking"] = {"type": "enabled"}
    if tools:
        payload["tools"] = tools
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"} if api_key else {"Content-Type": "application/json"}

    result: dict = {"content": "", "reasoning_content": "", "tool_calls": None, "usage": None}
    if out is not None:
        out.clear()
        out.update(result)
    tool_call_acc: dict = {}
    index_to_id: dict[int, str] = {}  # index → id 桥（arguments 无 id 分片定位用）
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", chat_completions_url(api_base), json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    err = (await resp.aread()).decode(errors="replace")[:300]
                    yield f"data: [ERROR]{_friendly_llm_error(f'{resp.status_code}: {err}')}\n\n"
                    return
                buffer = ""
                done = False
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        pos = buffer.find("\n")
                        line = buffer[:pos]
                        buffer = buffer[pos + 1:]
                        if not line.strip() or not line.startswith("data: "):
                            continue
                        p = line[6:]
                        if p == "[DONE]":
                            done = True
                            break
                        try:
                            j = json.loads(p)
                            # ⚠️ 2026-08-13 修复：usage 收集独立于 choices——DeepSeek 的 usage 块
                            # 可能带空 choices（[]）也可能带非空 choices，只要消息有 usage 就收
                            if j.get("usage"):
                                result["usage"] = j["usage"]
                            choices = j.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            rt = delta.get("reasoning_content")
                            if rt:
                                result["reasoning_content"] += rt
                                yield f"data: [REASONING]{rt.replace(chr(10), '{NL}')}\n\n"
                            t = delta.get("content")
                            if t:
                                result["content"] += t
                                yield f"data: {t.replace(chr(10), '{NL}')}\n\n"
                            tcs = delta.get("tool_calls")
                            if tcs:
                                for item in tcs:
                                    cid = item.get("id") or ""
                                    idx = item.get("index", 0)
                                    # ⚠️ DeepSeek 流式坑（2026-08-13）：name 分片带 id、arguments 分片常不带 id——
                                    # 优雅解：id 主 key（区分并行，index 重复不怕）+ index 桥（无 id 分片定位）。
                                    key = cid or index_to_id.get(idx) or f"idx_{idx}"
                                    acc = tool_call_acc.setdefault(key, {"id": "", "name": "", "arguments": "", "index": idx})
                                    if cid:
                                        acc["id"] = cid
                                        index_to_id[idx] = cid
                                    fn = item.get("function") or {}
                                    if fn.get("name"):
                                        acc["name"] = fn["name"]
                                    if fn.get("arguments"):
                                        acc["arguments"] += fn["arguments"]
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            buffer = line + "\n" + buffer
                            break
                    if done:
                        break
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 流式对话异常: {e}")
        yield f"data: [ERROR]{str(e)[:200]}\n\n"
        return
    if tool_call_acc:
        result["tool_calls"] = [
            {
                "id": acc["id"] or f"call_{key}",
                "type": "function",
                "function": {"name": acc["name"], "arguments": acc["arguments"] or "{}"},
            }
            for key, acc in sorted(tool_call_acc.items())
        ]
    # DeepSeek DSML/XML 兜底（2026-08-13）：模型有时把工具调用输出成 XML 文本
    # （<invoke name=...><parameter name=...>），标准解析拿不到 → 从完整 content 里解析
    if not result.get("tool_calls") and result.get("content"):
        dsml_calls = _parse_dsml_tool_calls(result["content"])
        if dsml_calls:
            result["tool_calls"] = dsml_calls
            # XML 已解析为工具调用，正文不再当作回复内容
            result["content"] = ""
    if out is not None:
        out.clear()
        out.update(result)
    # 注意：不在这里 yield [DONE]——它是单次 LLM 调用，[DONE] 只由 stream_world_chat
    # 主流程在整轮对话结束时发（2026-08-13 修复：否则工具轮每轮发 [DONE]，
    # 前端收到就退出订阅，第二轮之后的内容不显示）


async def _inject_pending_user_messages(
    world_repo: WorldRepository, world_id: int, messages: list, sid_db: str | None,
):
    """把插入中的普通消息拼进工具轮上下文（每轮 LLM 调用前调用）。

    2026-08-16 产品定（改）：插入消息**不再立即绘制**——用户发送后先进排队队列，
    等 AI 真正收到（此处注入上下文）时，才逐条**落库 + 广播** [INSERTED]/[INSERT]
    （前端清排队弹窗 + 画真实气泡）——用户看到"已发送" = AI 已看到。
    无排队消息时直接返回（零开销）。
    """
    from app.services.world.world_turn import get_world_worker
    insert_items = await get_world_worker(world_id).drain_inserts()
    if not insert_items:
        return
    # 落库 + 广播（先 [INSERTED] 清前端排队弹窗，再逐条落库 + [INSERT] 画气泡）
    from app.models.world import World, WorldChatMessage
    from app.config import settings
    from app.models.world import World, WorldChatMessage
    for _it in insert_items:
        tb = _it.get("tb")
        user_id = _it.get("user_id")
        items = _it.get("items") or []
        if tb:
            try:
                await tb.broadcast(f"data: [INSERTED]{json.dumps({'count': len(items)}, ensure_ascii=False)}\n\n")
            except Exception as e:
                # 回执发不出去 = 前端排队弹窗永远清不掉（用户可见），必须留痕
                logger.warning(f"🌐 世界 #{world_id} [INSERTED] 回执广播失败: {e}")
        ids: list[int] = []
        for it in items:
            if it.is_empty:
                continue
            # 落库（真实 msg_id → 前端 [INSERT] 画的气泡与历史一致，刷新不重复）
            if sid_db is None:
                _w = await world_repo.get(World, world_id)
                sid_db = session_id_for_db(_w) if _w else None
            wm = WorldChatMessage(
                world_id=world_id, user_id=user_id, role="user",
                content=it.text, session_id=sid_db,
                attachments=list(it.attachments) or None,
            )
            world_repo.add(wm)
            await world_repo.flush()
            ids.append(wm.id)
            if tb:
                try:
                    payload = {
                        "msg_id": wm.id, "content": it.text,
                        "attachments": [dict(a) for a in it.attachments],
                    }
                    await tb.broadcast(f"data: [INSERT]{json.dumps(payload, ensure_ascii=False)}\n\n")
                except Exception as e:
                    # 同上：气泡画不出来（用户可见）。消息已落库，下次 loadChat 会补上
                    logger.warning(f"🌐 世界 #{world_id} [INSERT] 回执广播失败（msg_id={wm.id}）: {e}")
            # 注入 AI 上下文（真正"发送"给 AI）：带图则实时转多模态
            messages.append({"role": "user", "content": build_content(it.text, it.attachments, settings.data_dir)})
        _it["msg_ids"] = ids
    await world_repo.commit()


async def _run_one_tool_call(
    world_repo: WorldRepository, world, world_id: int, sid_db: str | None,
    acc: dict, turn_state: dict, messages: list, budget_error: str | None = None,
):
    """执行单个工具调用：执行 → 摘要/详情 → 注入 AI 上下文 → 落库 → 状态事件。

    以异步生成器把 SSE 行交给调用方；落库在这里自己 commit（每个工具一次，和原来一致：
    前一个工具的记录不会因为后一个失败而丢）。
    """
    from app.models.world import WorldChatMessage
    from app.tools.world import execute_world_tool, tool_label, tool_result_detail, tool_result_summary

    tool_id = f"t_{uuid.uuid4().hex[:8]}"
    args_summary = _args_summary(acc.get("arguments") or "")
    # ① 执行前：running 状态（前端创建/更新气泡：正在执行 XX）
    yield f"data: [TOOL_UPDATE]{json.dumps({'tool_id': tool_id, 'status': 'running', 'name': acc['name'], 'args_summary': args_summary}, ensure_ascii=False)}\n\n"
    # ② 工具内部进度事件（耗时工具如 run_world_code 分阶段 yield update）
    progress_events: list[str] = []
    async def _on_progress(note: str) -> None:
        progress_events.append(note)
    result = None
    # ⓪ 运行模式门禁（唯一入口）：审阅/计划模式下敏感操作在这里弹窗等用户点头；
    #    auto 直接放行并告知工具「平台已兜底」，工具不必自己再问一遍。
    from app.tools.world.shared import parse_args
    if budget_error:
        # 预算见底的类型这一轮直接挡下：仍然给出 tool 结果（悬空 tool_call 会让下次请求 400）
        allowed, approved, feedback = False, False, budget_error
        result = {"success": False, "error": budget_error, "blocked_by": "round_budget"}
    else:
        allowed, approved, feedback = await gate_tool_call(
            world, world_id, acc["name"], parse_args(acc.get("arguments") or ""), turn_state,
        )
    if allowed:
        try:
            result = await execute_world_tool(
                world_repo, world, acc["name"], acc["arguments"], turn_state,
                on_progress=_on_progress, approved=approved,
            )
        except Exception as e:
            # 工具/技能自己抛异常：如实回传错误，交给 AI 决定下一步（别重试——副作用可能已发生）
            logger.warning(f"🌐 世界 #{world_id} 工具 {acc['name']} 执行失败: {e}")
            result = {"success": False, "error": str(e)[:500]}
        # 用户点同意时写的理由/补充要求必须进 AI 上下文（feedback 为空时原样返回）
        result = with_user_note(result, feedback)
    elif not result:
        result = {"success": False, "error": feedback or "该工具未执行", "blocked_by": "ai_mode"}
        try:
            result = await execute_world_tool(
                world_repo, world, acc["name"], acc["arguments"], turn_state,
                on_progress=_on_progress, approved=approved,
            )
        except Exception as e:
            # 工具/技能自己抛异常：如实回传错误，交给 AI 决定下一步（别重试——副作用可能已发生）
            logger.warning(f"🌐 世界 #{world_id} 工具 {acc['name']} 执行失败: {e}")
            result = {"success": False, "error": str(e)[:500]}
        # 用户点同意时写的理由/补充要求必须进 AI 上下文（feedback 为空时原样返回）
        result = with_user_note(result, feedback)
    summary = tool_result_summary(acc["name"], result)
    # 卡片详情（UI 专用）：和 summary 一起算好，随事件下发 + 落库；不进 LLM 上下文
    detail = tool_result_detail(acc["name"], acc["arguments"], result)
    turn_state["tools_done"].append(summary)
    messages.append({
        "role": "tool",
        "tool_call_id": acc["id"] or f"call_{idx}",
        "content": json.dumps(result, ensure_ascii=False),
    })
    # ③ 进度事件转发（同 tool_id，status=update）
    for note in progress_events:
        yield f"data: [TOOL_UPDATE]{json.dumps({'tool_id': tool_id, 'status': 'update', 'name': acc['name'], 'summary': note}, ensure_ascii=False)}\n\n"
    # ④ 执行后：done（同 tool_id，前端原地更新）
    yield f"data: [TOOL_UPDATE]{json.dumps({'tool_id': tool_id, 'status': 'done', 'name': acc['name'], 'label': tool_label(acc['name']), 'success': bool(result.get('success')), 'summary': summary, 'detail': detail}, ensure_ascii=False)}\n\n"
    # ⑤ 落库：同 tool_id 更新最后一条（历史只留最终态）；无 tool_id 旧字段则新增
    existing = (await world_repo.execute(
        select(WorldChatMessage).where(
            WorldChatMessage.world_id == world_id,
            WorldChatMessage.tool_id == tool_id,
        ).order_by(WorldChatMessage.id.desc()).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        existing.content = summary
        existing.is_error = not bool(result.get("success"))
        existing.tool_name = acc["name"]
        existing.tool_detail = detail
    else:
        world_repo.add(WorldChatMessage(
            world_id=world_id, user_id=None, role="tool",
            content=summary, session_id=sid_db, tool_id=tool_id,
            is_error=not bool(result.get("success")),
            tool_name=acc["name"], tool_detail=detail,
        ))
    await world_repo.commit()


async def _execute_tool_round(
    world_repo: WorldRepository, world, world_id: int, tool_call_acc: dict,
    messages: list, turn_state: dict, sid_db: str | None,
    *, write_budget: int, read_budget: int,
):
    """执行本轮所有工具调用：逐个交给 _run_one_tool_call，事件按序透传。

    顺便记一笔预算账：这一批里**只要有一个改动类工具**就算改动轮（同批的只读免费跟着走），
    整批都是只读才算只读轮 —— 两本账分开，搜文件不再吃改动额度（用户 2026-09-23）。
    """
    names = [acc.get("name") for acc in tool_call_acc.values()]
    write_round = is_write_round(names)
    for idx, acc in sorted(tool_call_acc.items()):
        err = round_budget_error(acc.get("name") or "", turn_state, write_budget, read_budget)
        async for line in _run_one_tool_call(world_repo, world, world_id, sid_db, acc, turn_state, messages, budget_error=err):
            yield line
    key = "write_rounds" if write_round else "read_rounds"
    turn_state[key] = int(turn_state.get(key, 0)) + 1


def _parse_dsml_tool_calls(text: str) -> list[dict] | None:
    """解析 DeepSeek DSML/XML 工具调用（模型有时把工具调用输出成 XML 文本而非标准 JSON）。

    支持两种格式（2026-08-13）：
      <invoke name="file_read"><parameter name="path">js/game.js</parameter></invoke>
      <tool_call><name>file_read</name><parameters>{"path": "x"}</parameters></tool_call>
    返回标准 tool_calls 列表；未检测到 XML 返回 None（调用方继续走标准解析）。
    """
    import re as _re
    if "<invoke" not in text and "<tool_call" not in text and "DSML" not in text:
        return None
    results = []
    # 格式1：<invoke name="x"><parameter name="k">v</parameter>...</invoke>
    for m in _re.finditer(r'<invoke\s+name=["\']([^"\'\s]+)["\']>(.*?)</invoke>', text, _re.S):
        name, body = m.group(1), m.group(2)
        params = {}
        for p in _re.finditer(r'<parameter\s+name=["\']([^"\'\s]+)["\']>(.*?)</parameter>', body, _re.S):
            params[p.group(1)] = p.group(2).strip()
        if name:
            results.append({"id": f"call_{len(results)}", "type": "function",
                            "function": {"name": name, "arguments": _json_safe_dump(params)}})
    # 格式2：<tool_call><name>x</name><parameters>{...}</parameters></tool_call>
    for m in _re.finditer(r'<tool_call>(.*?)</tool_call>', text, _re.S):
        body = m.group(1)
        nm = _re.search(r'<name>(.*?)</name>', body, _re.S)
        pm = _re.search(r'<parameters>(.*?)</parameters>', body, _re.S)
        if nm:
            name = nm.group(1).strip()
            args = pm.group(1).strip() if pm else "{}"
            if name:
                results.append({"id": f"call_{len(results)}", "type": "function",
                                "function": {"name": name, "arguments": args}})
    return results or None


def _json_safe_dump(obj) -> str:
    """dict → JSON 字符串（失败回退 repr）"""
    import json as _json
    try:
        return _json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"

def _args_summary(arguments: str) -> str:
    """工具参数摘要（展示用）：取 path/code/event 等关键字段，避免全量刷屏"""
    try:
        args = json.loads(arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(args, dict):
        return ""
    for key in ("path", "code", "event", "name", "query"):
        v = args.get(key)
        if v:
            s = str(v).strip()
            return s[:80] + ("…" if len(s) > 80 else "")
    return ""


def _history_text(text: str, attachments) -> str:
    """历史消息里的图片降级成 [图片] 标记：让模型知道当时有图，但不给字节。"""
    placeholder = image_placeholder(attachments)
    if not placeholder:
        return text or ""
    return f"{placeholder} {text}".strip() if text else placeholder


async def _prepare_world_chat(
    world_repo: WorldRepository, world_id: int, user_id: int, items: list[ChatItem],
) -> dict | None:
    """世界 AI 对话的准备阶段：世界加载/凭证/前缀/历史/消息列表/命令识别。

    返回上下文 dict（供 stream_world_chat 编排）；世界不存在返回 None。
    斜杠命令不在这里执行（需 yield SSE），只识别 cmd_text 供主编排处理。
    """
    from app.config import settings
    from app.models.world import World

    world = await world_repo.get(World, world_id)
    if world is None:
        return None

    # 对话 = 活跃信号：唤醒 + 离线时间补偿（让 AI 看到的世界时间准确）
    from app.services.world.world_service import apply_time_compensation
    apply_time_compensation(world)
    await world_repo.commit()

    # ── 会话空闲自动 compact（懒加载：发消息时检查；空闲超时先压缩再继续，趁缓存最大化利用）──
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        st = session_settings(world)
        hours = st["compact_idle_hours"]
        if hours > 0:
            _cfg = world.config or {}
            _key = _cfg.get("current_session") or "default"
            _meta = (_cfg.get("sessions") or {}).get(_key) or {}
            _la = _meta.get("last_active_at")
            if _la:
                _la_dt = _dt.fromisoformat(_la)
                _now = _dt.now(_tz.utc)
                if _now - _ensure_aware(_la_dt) > _td(hours=hours):
                    from app.tools.world import run_world_tool
                    await run_world_tool(world_repo, world, "compact_context", "{}")
                    touch_session(world)
                    await world_repo.commit()
                    logger.info(f"🌐 世界 #{world_id} 会话 {_key} 空闲 {hours}h 已自动压缩")
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 空闲自动压缩失败: {e}")

    from app.services.world.world_service import ensure_world_ai, take_pending_notices, CREATOR_DEFAULT_CONFIG
    wai = await ensure_world_ai(world_repo, world_id)
    cfg = {
        "name": wai.name, "system_prompt": wai.system_prompt, "model": wai.model,
        "temperature": wai.temperature, "top_p": wai.top_p, "thinking": wai.thinking,
        "max_tool_rounds": wai.max_tool_rounds,
    }

    # ── 前缀文本版本化（2026-08-12 产品定：所有进前缀的内容必须保证缓存命中）──
    # 三个文本源：用户可改提示词（每世界）/ 强注入段（全局）/ 昵称（每世界）
    # 变更 → 写新版本 + 尾部 changelog 告知，不碰前缀；compact / clear 解锁后生效
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    from app.services.capability_versioning import (
        apply_pending_changes, ensure_text_source_version, get_effective_text, mark_known_latest,
    )
    _cap_repo = SQLAlchemyCapabilityRepository(world_repo.session)
    # ── 会话起点解锁（2026-09-19）───────────────────────────────────────────────
    # 前缀内容的版本链只在 compact / 清空上下文时对齐，而天天在用的世界永远等不到那次 idle compact
    # （compact_idle_hours 默认 18h）——实测 4 个真正聊过天的世界里 3 个停在旧版强注入段（v3/v6/v7），
    # world 45 因此在 v7 上按旧文案去找本环境不存在的 world_push，白烧轮次。
    # 新会话第一轮没有"前缀要保"的包袱（历史为空，缓存本来就要重建），在这里对齐一次。
    # 代价：版本确实变过时，这一轮前缀全额 miss 一次（约 76k × ¥0.2/M ≈ 1.5 分）。
    if await session_is_fresh(world_repo, world):
        _prefix_cfg = dict(world.config or {})
        _sources = prefix_capability_sources(world_id)
        await apply_pending_changes(_cap_repo, _prefix_cfg, _sources)
        await mark_known_latest(_cap_repo, _prefix_cfg, _sources)   # 已在生效前缀里，无需再发变更通知
        world.config = _prefix_cfg
        await world_repo.commit()
        logger.info(f"🌐 世界 #{world_id} 会话起点：前缀能力快照已对齐最新")
    user_prompt = cfg.get("system_prompt") or CREATOR_DEFAULT_CONFIG["system_prompt"]
    forced_prompt = build_forced_prompt()
    creator_name = cfg.get("name") or "群视界机器人"
    await ensure_text_source_version(_cap_repo, f"world-prompt-{world_id}", user_prompt, "世界AI提示词")
    await ensure_text_source_version(_cap_repo, "forced-prompt", forced_prompt, "强注入段")
    await ensure_text_source_version(_cap_repo, f"world-name-{world_id}", creator_name, "世界AI昵称")
    eff_user_prompt = await get_effective_text(_cap_repo, world.config, f"world-prompt-{world_id}", user_prompt)
    eff_forced_prompt = await get_effective_text(_cap_repo, world.config, "forced-prompt", forced_prompt)
    eff_name = await get_effective_text(_cap_repo, world.config, f"world-name-{world_id}", creator_name)

    # ── 组装消息：静态 system 前缀保持稳定（prompt cache 友好）──
    system_prompt = world_context_block(world) + "\n\n" + eff_user_prompt
    system_prompt += eff_forced_prompt  # 强注入段：平台强约束，用户不可改
    system_prompt += build_mode_prompt(get_mode(world))  # 运行模式（自动/审阅/计划）
    system_prompt += build_budget_prompt(resolve_round_budget(cfg), resolve_read_budget(cfg))  # 双预算前置（前缀稳定，可缓存）
    # 昵称也在前缀里（版本化保证缓存命中）：改名当轮只有尾部「世界AI昵称」变更通知，
    # 全文要等 compact / 清空上下文才刷新——所以这里给一条常驻的冲突裁决规则。
    system_prompt += (f"\n【名字】你的名字是「{eff_name}」，对外标识 world-{world_id}。"
                      "若收到「世界AI昵称」变更通知，以通知里的新名字为准（系统提示全文在下次 compact / 清空上下文后刷新）。")
    # 通俗模式（用户级偏好）：跟着发消息的人走——同一个世界，新手看到的是带解释的话，老手看到的照旧
    try:
        from app.models.user import User as UserModel
        from app.utils.pure.expression_style import build_expression_segment
        _reader = await world_repo.get(UserModel, user_id) if user_id else None
        system_prompt += build_expression_segment(_reader)
    except Exception as e:
        logger.warning(f"通俗模式注入失败（非致命）: {e}")

    notices = await take_pending_notices(world_repo, world_id)
    notice_lines = "\n".join(
        f"- {n['file']}（{n.get('location', '')}）: {n.get('summary', '')}"
        for n in notices
    ) if notices else ""

    # ── 上下文：模型只看真实消息（user/ai）；接近上限时提示 AI 调 compact ──
    # 有摘要 → **从 compact 边界往后全部带上**：只增不减，前缀稳定（跨轮命中缓存）且 AI 真的记得住；
    # 边界缺失（老数据）→ 退回最近 WORLD_CHAT_KEEP_LAST 条；无摘要 → 最近 CHAT_HISTORY_LIMIT 条
    sid_db = session_id_for_db(world)  # 落库用 session_id（默认会话 None）
    skey = session_key(world)
    summaries = (world.config or {}).get("chat_summaries") or {}
    summary = summaries.get(skey) or ""
    boundary = ((world.config or {}).get("chat_summary_bounds") or {}).get(skey) if summary else None
    limit, since_id = (None, boundary) if (summary and boundary) else (
        WORLD_CHAT_KEEP_LAST if summary else CHAT_HISTORY_LIMIT, None)
    history = await get_chat_history(
        world_repo, world_id, limit, session_id=sid_db, roles=REAL_ROLES, since_id=since_id,
    )
    hist_llm = [
        {
            "role": "assistant" if m["role"] == "ai" else m["role"],
            "content": _history_text(m["content"], m.get("attachments")),
        }
        for m in history
    ]
    # 用户消息（单条/批量统一；批量 = 排队消息一起发，逐条气泡）
    from app.services.memory.context_compression_service import should_compress
    needs_compress = should_compress(
        [{"role": "system", "content": system_prompt}, *hist_llm,
         *[{"role": "user", "content": it.text} for it in items]],
        min_messages=WORLD_CONTEXT_MIN_MESSAGES,
    )

    # 前缀稳定：位置2摘要（静态）+ 历史 + 用户消息（批量 = 逐条注入，AI 一次看到全部）
    messages = [{"role": "system", "content": system_prompt}]
    if summary:
        messages.append({"role": "system", "content": summary})
    messages += hist_llm
    # 只有最后一条带真实图片字节（同批靠前的降级成 [图片]）——既护住 prompt cache，
    # 也避免一次塞进多张图把 token 顶爆
    _last_idx = len(items) - 1
    for _idx, it in enumerate(items):
        content = (
            build_content(it.text, it.attachments, settings.data_dir)
            if _idx == _last_idx
            else _history_text(it.text, it.attachments)
        )
        messages.append({"role": "user", "content": content})
    # 附图便签（真 system role，放在用户消息之后）：只给 image_url 不给这句话，
    # 模型会自称"我是文本 AI"却又能描述图里的内容（实测 mimo-v2.5）。
    # 数量用**实际注入数**，与"另有 N 张未提供"配套，避免模型去找不存在的图
    _n_img = injected_image_count(messages[-1]["content"]) if items else 0
    if _n_img:
        messages.append({"role": "system", "content": image_note(_n_img)})

    # 动态信息全部放末尾（每次变化，不影响前缀 cache）——与主对话同规则
    # 未完成工作流记忆：上次对话中断（无最终回复）→ 本次继续，不重做。
    # 必须放尾部：它带 interrupted_at 时间戳，若拼进 system，一变就是整段前缀全 miss
    wm = (world.config or {}).get("workflow_memory")
    if wm and wm.get("tools_done"):
        done = "、".join(wm["tools_done"][-8:])
        messages.append({"role": "system", "content": (
            "【未完成工作流】上次对话在 " + str(wm.get("interrupted_at", ""))[:19] +
            " 中断，已执行：" + done + "。请继续完成剩余工作并给出总结，不要重复已完成的步骤。"
        )})
    if notice_lines:
        messages.append({"role": "system", "content": "【用户手动改动的懒通知，回复中应体现你看到了】\n" + notice_lines})
    # 记忆地图：只在新会话/clear 后注入（文档 6.9：普通延续对话不注入——它是索引，
    # 每轮重发既费 token，又把动态内容塞进尾部）
    if not summary and not hist_llm:
        try:
            memory_map = await build_memory_map(world_repo, world_id)
            if memory_map:
                messages.append({"role": "system", "content": memory_map})
        except Exception as e:
            # 降级可接受（本轮少一份记忆上下文），但必须留痕：
            # 静默会让人误判成"这个世界没有记忆"，排查时无从下手
            logger.warning(f"🌐 世界 #{world_id} 记忆地图注入失败（本轮降级）: {e}")
    if needs_compress:
        messages.append({"role": "system", "content": "⚠️ 上下文已接近上限，请调用 compact_context 工具压缩对话历史后再继续。"})
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(settings.display_timezone)
    messages.append({"role": "system", "content": f"## 当前时间\n{datetime.now(tz).strftime(f'%Y-%m-%d %H:%M {tz.key}')}\n"})
    # 世界时间（2026-08-13 从前缀移到这里）：每次对话 apply_time_compensation 都变，
    # 放前缀会破坏 DeepSeek 缓存；放尾部动态区不影响前缀命中
    messages.append({"role": "system", "content": f"## 世界时间\n{world.world_time.isoformat() if world.world_time else '未开始'}\n"})

    # 世界内访客（身份系统 identity_index 快照）→ AI 知道谁在玩这个世界
    try:
        from app.services.world.world_service import get_world_data
        idx_row = await get_world_data(world_repo, world_id, "identity_index")
        idx = (idx_row or {}).get("value")
        if isinstance(idx, dict) and idx:
            parts = []
            for v in idx.values():
                if isinstance(v, dict):
                    parts.append(f"{v.get('name', '?')}" + (f"（{v.get('role', '未绑定角色')}）" if v.get('role') else ""))
            if parts:
                messages.append({"role": "system", "content": "## 世界内访客\n" + "、".join(parts[:20])})
    except Exception as e:
        # 降级可接受（AI 本轮不知道访客名单），但必须留痕
        logger.warning(f"🌐 世界 #{world_id} 访客名单注入失败（本轮降级）: {e}")

    # 能力变更通知（懒加载：增量 changelog 追加尾部，known 更新与注入同轮）
    try:
        from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
        from app.services.capability_versioning import build_change_notice
        notice = await build_change_notice(
            SQLAlchemyCapabilityRepository(world_repo.session), world.config,
            prefix_capability_sources(world_id),
        )
        if notice:
            messages.append({"role": "system", "content": notice})
            await world_repo.commit()
    except Exception as e:
        # 降级可接受（本轮不发能力变更通知），但必须留痕
        logger.warning(f"🌐 世界 #{world_id} 能力变更通知注入失败（本轮降级）: {e}")

    # 落库用户消息（批量 = 排队消息一起发，逐条气泡；先提交，即使流失败也不丢）
    from app.models.world import WorldChatMessage
    for it in items:
        world_repo.add(WorldChatMessage(
            world_id=world_id, user_id=user_id, role="user",
            content=it.text, session_id=sid_db,
            attachments=list(it.attachments) or None,
        ))
    try:
        await world_repo.commit()
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 用户消息落库失败: {e}")

    # 世界 AI（造物主）工具 = 平台内置 + 设计侧 skills（world_ai_skills/ 全局库；世界侧居民能力不注入）
    from app.tools.world import WORLD_TOOLS
    from app.services.world.world_skill_runtime import build_ai_tools
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    from app.services.capability_versioning import ensure_source_version, get_effective_definitions
    skill_tools = build_ai_tools()
    if skill_tools:
        await ensure_source_version(SQLAlchemyCapabilityRepository(world_repo.session), "ai-skills", skill_tools, "设计侧能力")
    effective_skill_tools = await get_effective_definitions(SQLAlchemyCapabilityRepository(world_repo.session), world.config, "ai-skills", skill_tools)
    tools_for_world = [*WORLD_TOOLS, *effective_skill_tools]

    # 凭证 + 模型
    api_key, api_base = await _resolve_world_credentials(world_repo, world)
    model = await resolve_world_chat_model(world_repo, world, api_base, wai)
    thinking = bool(cfg.get("thinking", False))

    # 命令只在单条纯文本时识别：带附件的消息一律当普通消息走 LLM
    cmd_text = items[0].text if len(items) == 1 and not items[0].attachments else ""
    return {
        "world": world, "wai": wai, "cfg": cfg,
        "api_key": api_key, "api_base": api_base, "model": model, "thinking": thinking,
        "tools_for_world": tools_for_world, "messages": messages,
        "sid_db": sid_db, "cmd_text": cmd_text,
    }


async def _handle_slash_command(
    world_repo: WorldRepository, world, world_id: int, cmd_text: str,
    user_id: int | None, sid_db: str | None, out: dict,
):
    """执行斜杠命令并下发事件；已处理时置 out["handled"]=True（调用方直接收尾返回）。

    CmdResult 为 None = 非注册命令 → 不处理，继续走 LLM（未知斜杠当普通消息处理）。
    """
    try:
        from app.services.world.world_chat_commands import run_slash_command
        cmd_result = await run_slash_command(world_repo, world, cmd_text, user_id=user_id)
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 命令执行失败: {e}")
        yield f"data: [ERROR]命令执行失败: {e}\n\n"
        yield "data: [DONE]\n\n"
        out["handled"] = True
        return
    if cmd_result is None:
        return
    from app.models.world import WorldChatMessage
    world_repo.add(WorldChatMessage(world_id=world_id, user_id=None, role="tool", content=cmd_result.text, session_id=sid_db))
    await world_repo.commit()
    # success 由命令自己报（原先硬写 True：/compact 没压成也画 ✓）
    yield f"data: [TOOL_UPDATE]{json.dumps({'tool_id': f't_{uuid.uuid4().hex[:8]}', 'status': 'done', 'name': cmd_text.lstrip('/'), 'success': cmd_result.ok, 'summary': cmd_result.text}, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"
    out["handled"] = True


async def _emit_suggestions(world_repo: WorldRepository, world, world_id: int, turn_state: dict):
    """下发「你可以」建议：只认 AI 自己调 suggest_questions 生成的那几条。

    2026-09-18 修：AI 没给建议时旧实现会跑一次"轻量 LLM 兜底"，实测几乎总是解析失败退化成
    随机预设（「帮我做一个聊天室」这类新手引导）——于是 AI 正文写着「下面四个建议 ①②③④」、
    界面显示的却是另一套，用户直接问"怎么对不上"；而且它还会把 UI 持久化的 AI 建议一起覆盖掉
    （前端 /chat/suggest 下次读到的是这些预设）。没有就什么都不发，界面保留 AI 上次的建议。
    """
    try:
        suggestions = list(turn_state.get("suggestions") or [])
        if not suggestions:
            return
        try:
            from app.services.world.world_service import set_world_data
            await set_world_data(world_repo, world_id, "ui.suggestions", suggestions[:5])
        except Exception as e:
            # 本次仍会把建议推给前端；留痕便于排查"刷新后建议就没了"
            logger.warning(f"🌐 世界 #{world_id} 建议持久化失败（本次仍下发）: {e}")
        yield f"data: [SUGGEST]{json.dumps(suggestions[:5], ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 建议问题生成失败: {e}")


async def _stream_first_round(
    world_id: int, turn_id: str, model: str, thinking: bool,
    api_base: str, api_key: str | None, cfg: dict, tools_for_world: list,
    messages: list, out: dict,
):
    """首轮流式调用：正文/思考逐 chunk 透传，收集 tool_calls 与 usage。

    结果写入 out：full_content / full_reasoning / usage / tool_calls；
    上游若因非 200 提前收尾，置 out["aborted"]=True（调用方直接 return）。
    """
    import httpx  # client.stream 用

    # ── 请求 DeepSeek（stream=true，透传 SSE）──
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": cfg.get("temperature", 0.8),
        "top_p": cfg.get("top_p", 0.9),
        "max_tokens": STREAM_MAX_TOKENS,   # 与工具轮同值（见 _stream_llm_once 注释）
        "stream": True,
        "tools": tools_for_world,
        "stream_options": {"include_usage": True},
    }
    # v4 思考默认开启（正常行为）；显式开启时走 thinking 参数
    if thinking:
        payload["thinking"] = {"type": "enabled"}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    full_content, full_reasoning = "", ""
    first_usage: dict | None = None  # 2.7：首轮 usage（流结束块捕获）
    tool_call_acc: dict = {}  # id → {id, name, arguments}
    index_to_id: dict[int, str] = {}  # index → id 桥（arguments 分片不带 id 时定位用）
    try:
        _log_llm_request(world_id, turn_id, 0, model, thinking, messages)
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", chat_completions_url(api_base), json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    err = (await resp.aread()).decode(errors="replace")[:300]
                    yield f"data: [ERROR]{_friendly_llm_error(f'{resp.status_code}: {err}')}\n\n"
                    yield "data: [DONE]\n\n"
                    out["aborted"] = True
                    return

                buffer = ""
                done = False
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    buffer += chunk.decode("utf-8", errors="replace")
                    # 逐行处理 buffer（UTF-8 多字节被拆到两个 chunk 时，JSON 解析失败就把行放回）
                    while "\n" in buffer:
                        pos = buffer.find("\n")
                        line = buffer[:pos]
                        buffer = buffer[pos + 1:]
                        if not line.strip() or not line.startswith("data: "):
                            continue
                        p = line[6:]
                        if p == "[DONE]":
                            done = True
                            break
                        try:
                            j = json.loads(p)
                            # ⚠️ 2026-08-13 修复：usage 收集独立于 choices（同上）
                            if j.get("usage"):
                                u = dict(j["usage"])
                                pd = u.pop("prompt_tokens_details", None) or {}
                                cd = u.pop("completion_tokens_details", None) or {}
                                u["cached_tokens"] = pd.get("cached_tokens", 0)
                                u["reasoning_tokens"] = cd.get("reasoning_tokens", 0)
                                first_usage = u
                            choices = j.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            rt = delta.get("reasoning_content")
                            if rt:
                                full_reasoning += rt
                                yield f"data: [REASONING]{rt.replace(chr(10), '{NL}')}\n\n"
                            # 出现工具调用后正文不再透传（模型可能把工具调用写成文本；最终以工具执行后的第二轮为准）
                            t = delta.get("content")
                            if t and not tool_call_acc:
                                full_content += t
                                yield f"data: {t.replace(chr(10), '{NL}')}\n\n"
                            # 工具调用（function calling 分片到达）
                            # ⚠️ DeepSeek 流式坑（2026-08-13）：name 分片带 id、arguments 分片常不带 id——
                            # 用 id 主 key（区分并行调用）+ index 桥（无 id 的分片靠 index 定位）。
                            tcs = delta.get("tool_calls")
                            if tcs:
                                for item in tcs:
                                    cid = item.get("id") or ""
                                    idx = item.get("index", 0)
                                    key = cid or index_to_id.get(idx) or f"idx_{idx}"
                                    acc = tool_call_acc.setdefault(key, {"id": "", "name": "", "arguments": "", "index": idx})
                                    if cid:
                                        acc["id"] = cid
                                        index_to_id[idx] = cid
                                    fn = item.get("function") or {}
                                    if fn.get("name"):
                                        acc["name"] = fn["name"]
                                    if fn.get("arguments"):
                                        acc["arguments"] += fn["arguments"]
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            buffer = line + "\n" + buffer
                            break
                    if done:
                        break
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 流式对话异常: {e}")
        yield f"data: [ERROR]{str(e)[:200]}\n\n"
    out["full_content"] = full_content
    out["full_reasoning"] = full_reasoning
    out["usage"] = first_usage
    out["tool_calls"] = tool_call_acc


async def _run_tool_loop(
    world_repo: WorldRepository, ctx: dict, world_id: int, turn_id: str,
    tool_call_acc: dict, turn_state: dict,
    first_content: str, first_reasoning: str, result: dict,
):
    """工具多轮循环：执行工具 → 注入插入消息 → 继续带 tools 调 LLM，直到不再调工具。

    ctx 为 _prepare_world_chat 的返回值；首轮正文/思考经 first_content / first_reasoning 传入。
    结果写回 result（full_content / full_reasoning）——**写在 finally 里**，异常路径也要把已生成
    的部分交回调用方落库，否则中断场景下 AI 回复会丢。
    异常直接向上抛，由调用方统一置 had_error / turn_error 并下发 [ERROR]。
    """
    world = ctx["world"]
    cfg = ctx["cfg"]
    api_key, api_base = ctx["api_key"], ctx["api_base"]
    model, thinking = ctx["model"], ctx["thinking"]
    tools_for_world = ctx["tools_for_world"]
    messages = ctx["messages"]
    sid_db = ctx["sid_db"]
    full_content = first_content
    full_reasoning = first_reasoning
    # 只有「产出最终正文那一轮」的思考才挂到最终回复上。
    # 中间轮的思考已经作为独立 note 落库，若再挂一次，历史里会出现两个一模一样的「思考过程」
    # （2026-09-15 修：world #45 实测同一段 2146 字的思考既在 note 又在 ai 回复上）
    reply_reasoning = ""
    try:
        from app.tools.world import execute_world_tool, tool_result_summary
        # 第一轮过渡叙述 + 对应思考过程：给用户看（role=note，不进 AI 上下文）
        # 2026-08-13：正文/思考拆两条独立 note（刷新后思考独立气泡，折叠生效）
        if full_content or full_reasoning:
            await _save_note_separated(world_repo, world_id, full_content, full_reasoning or "", sid_db)
        # 第一轮正文重置（最终以收尾轮为准）
        full_content = ""
        # 首轮思考只用于 assistant 消息链（thinking 模式要求回传 reasoning_content）；
        # 它已经作为独立 note 落库，因此不再挂到最终回复上（否则「思考过程」显示两遍）
        # 第一轮流式里收集到的 tool_calls（重构为 API 格式；content 用空串而非 None，避免部分接口/思考模式异常）
        # ⚠️ DeepSeek thinking 模式：首轮 assistant 也要回传 reasoning_content（2026-08-13 修复）
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": acc["id"] or f"call_{idx}",
                    "type": "function",
                    "function": {"name": acc["name"], "arguments": acc["arguments"] or "{}"},
                }
                for idx, acc in sorted(tool_call_acc.items())
            ],
            **({"reasoning_content": full_reasoning} if full_reasoning else {}),
        })
        # 双预算在 current_budgets 里定义（改动 / 只读各自一本账），**每轮重算**：
        # 用户在弹窗里批准的提额当轮就生效（旧写法在循环外算一次 → present_plan 的提额从来没生效过）
        turn_state.setdefault("write_rounds", 0)
        turn_state.setdefault("read_rounds", 0)
        async def _exec_pending_tools():
            """执行「当前待执行的那一批工具」+ 注入插入消息。

            ⚠️ 顺序不可颠倒：必须先执行工具、再注入插入消息。
            反了会把 user 消息插进 assistant(tool_calls) 与其 tool_response 之间，
            破坏 DeepSeek 消息链 → API 400。读的是外层当前绑定（每轮换新的一批）。
            """
            _wb, _rb = current_budgets(turn_state, cfg)
            async for event in _execute_tool_round(
                world_repo, world, world_id, tool_call_acc, messages, turn_state, sid_db,
                write_budget=_wb, read_budget=_rb,
            ):
                yield event
            # 此时 tool_response 已入 messages，user 追加在其后是合法链
            try:
                await _inject_pending_user_messages(world_repo, world_id, messages, sid_db)
            except Exception as e:
                logger.warning(f"🌐 世界 #{world_id} 插入消息注入失败（非致命）: {e}")

        # 进度口径（世界 AI 2026-09-21 反馈：只在剩 5 轮才提醒，来不及同步文档与记忆）：
        # 两本账分开报 —— 改动见底要收尾，只读见底可以申请提额，混着报 AI 会误判还能不能干活。
        final = ""
        hit_limit = False
        _r = 0
        while True:
            write_budget, read_budget = current_budgets(turn_state, cfg)
            turn_state["round_budgets"] = (write_budget, read_budget)   # 供 request_read_budget 读当前值
            write_left = write_budget - int(turn_state.get("write_rounds", 0))
            read_left = read_budget - int(turn_state.get("read_rounds", 0))
            # 两本账都见底（或总轮数触顶）才收工：改动见底时还可以只读查证
            if (write_left <= 0 and read_left <= 0) or _r >= write_budget + read_budget:
                hit_limit = True
                break
            # 执行本轮所有工具调用（执行→落库→[TOOL] 事件）
            async for event in _exec_pending_tools():
                yield event

            # 记账后重算：这一轮记在哪本账上，由 _execute_tool_round 按「本批有没有改动类工具」定
            write_budget, read_budget = current_budgets(turn_state, cfg)
            write_left = write_budget - int(turn_state.get("write_rounds", 0))
            read_left = read_budget - int(turn_state.get("read_rounds", 0))
            # 下一轮：继续带 tools，直到模型不再调用（同时捕获思考内容）
            # 轮次收尾：改动最后 5 轮软提醒，最后 2 轮改成硬约束——软提醒在批量替换/大改里会被忽略，
            # 结果是任务停在半途、下一轮还得重新摸文件（世界 AI 2026-09-18 反馈）
            if write_left <= 2:
                messages.append({"role": "system", "content": (
                    f"⛔ 改动预算只剩 {max(0, write_left)} 轮：禁止开启任何新工作（不新建文件、不开始新模块、不做批量替换）。"
                    "立刻收尾落盘：把已完成/未完成的进度写进 manage_records，直接在回复正文里给出总结"
                    "（已完成什么、还欠什么、下一步怎么做），必要时做一次自检或构建验证。"
                )})
            elif write_left <= 5:
                messages.append({"role": "system", "content": f"⚠️ 改动预算还剩 {write_left} 轮（共 {write_budget}），请尽快结束当前工作并给出总结！"})
            if read_left <= 0:
                messages.append({"role": "system", "content": (
                    f"📖 只读预算已用尽（{read_budget} 轮）：别再翻文件了。确实还需要读，就用 "
                    "request_read_budget 向用户申请增加（写清还要几轮、为什么），用户批准当轮立刻生效；"
                    "不需要就按现有信息收尾。"
                )})
            elif read_left <= 5:
                messages.append({"role": "system", "content": (
                    f"📖 只读预算还剩 {read_left} 轮（共 {read_budget}）：不够就提前用 request_read_budget 申请，别读到一半卡住。"
                )})
            elif (_r + 1) % 10 == 0 or (_r + 1) in {max(1, write_budget // 3), max(1, write_budget * 2 // 3)}:
                messages.append({"role": "system", "content": (
                    f"⏳ 进度：已用 {_r + 1} 轮（改动 {turn_state.get('write_rounds', 0)}/{write_budget}、只读 {turn_state.get('read_rounds', 0)}/{read_budget}）。"
                    "按开局预算推进；文档与长期记忆跟着代码节奏同步，别攒到最后几轮。"
                )})
            # 2026-08-13：工具轮流式化——逐 chunk 转发正文/思考（之前等整次调用结束一次性出）
            out: dict = {}
            async for event in _stream_llm_once(
                world_id, turn_id, _r + 1, model, thinking, api_base, api_key,
                messages, tools_for_world, cfg, out=out,
            ):
                yield event
            resp = out
            await _record_usage(world_repo, world_id, turn_id, str(_r + 1), model, (resp or {}).get("usage"), messages)
            content = (resp or {}).get("content") or ""
            reasoning = (resp or {}).get("reasoning_content") or ""
            tcs = (resp or {}).get("tool_calls")
            if reasoning:
                # 思考已在 _stream_llm_once 流式逐 chunk yield（[REASONING] 分片），此处只记录不重复发
                full_reasoning = reasoning
            if not tcs:
                # 收尾轮：正文作为最终回复（finally 落库 ai），不进 note
                final = content
                reply_reasoning = reasoning   # 本轮思考 = 最终回复的思考
                break
            # 中间轮（还要继续调工具）：正文已由 _stream_llm_once 逐 chunk yield，
            # 此处只落库 note（历史可见、不进 AI 上下文）——不再重复 yield 正文，
            # 否则前端 full 变量拼接导致内容重复显示
            # full_content 在此赋值是异常路径兜底：后续轮次若抛错，
            # finally 里"full_content 非空则不覆盖"，落库的就是这段中间叙述
            if content:
                full_content = content
            if content or reasoning:
                await _save_note_separated(world_repo, world_id, content, reasoning or "", sid_db)
            # 模型还要继续调工具：记录真实 tool_calls，进入下一轮
            # ⚠️ DeepSeek thinking 模式硬性要求：assistant 消息必须回传 reasoning_content，
            # 否则 400 invalid_request_error（2026-08-13 修复）
            messages.append({
                "role": "assistant", "content": content or "",
                "tool_calls": tcs,
                **({"reasoning_content": reasoning} if reasoning else {}),
            })
            tool_call_acc = {
                i: {"id": tc.get("id", ""), "name": tc["function"]["name"], "arguments": tc["function"].get("arguments") or ""}
                for i, tc in enumerate(tcs)
            }
            _r += 1
        if hit_limit:
            # 预算用尽（两本账都见底）：最后请求的那批工具也得执行——否则工作丢了，而且
            # assistant(tool_calls) 悬空会让收尾轮被 API 直接拒掉（2026-09-14 修的 400）
            async for event in _exec_pending_tools():
                yield event
            final = ""

        # 强制收尾轮：照旧带 tools —— 只删 tools 会让整段前缀掉出 DeepSeek 的前缀缓存
        # （线上实测：同一份 messages，带 tools 命中 76416/76584；去掉 tools 只剩 3328/52569）。
        # 此时模型已收到「最后 N 轮」提示，正常会直接给总结；万一它仍只回 tool_calls，
        # 下面取正文的 `or "（工具执行完成）"` 兜底，不会出现空回复。
        if not final:
            if heal_tool_chain(messages):   # 兜底：悬空 tool_calls 一并补齐，别让收尾轮 400
                logger.warning(f"🔧 世界 #{world_id} 收尾前补齐悬空 tool_calls（避免 400）")
            _log_llm_request(world_id, turn_id, "final", model, thinking, messages)
            # 2026-08-13：收尾轮流式化（之前等整次结束一次性出）
            out_f: dict = {}
            async for event in _stream_llm_once(
                world_id, turn_id, "final", model, thinking, api_base, api_key,
                messages, tools_for_world, cfg, out=out_f,
            ):
                yield event
            resp_final = out_f
            await _record_usage(world_repo, world_id, turn_id, "final", model, (resp_final or {}).get("usage"), messages)
            final = (resp_final or {}).get("content") or "（工具执行完成）"
            fr = (resp_final or {}).get("reasoning_content") or ""
            if fr:
                full_reasoning = fr
                reply_reasoning = fr
            full_content = final
        else:
            # ⚠️ 正常收尾轮（模型不再调工具 → final=content 已 break）：收尾总结也必须进 full_content，
            # 否则流式显示正常但落库的是中间轮最后一段叙述 → 刷新后总结消失
            full_content = final
        # 正文已由 _stream_llm_once 逐 chunk yield，不再重复 yield（避免前端 full 拼接导致重复）
    finally:
        # 异常路径也要把已生成内容交回：调用方 finally 里的落库依赖它
        result["full_content"] = full_content
        # 思考只落一次：中间轮的思考已随 note 落库，最终回复不复用它
        result["full_reasoning"] = reply_reasoning


async def stream_world_chat(
    world_repo: WorldRepository,
    world_id: int,
    user_id: int,
    items: list[ChatItem],
    turn_id: str = "",
):
    """世界 AI 对话（SSE 流式，参考大同差异分析流式实现）。

    事件格式（text/event-stream）：
      data: <内容增量>          — 正文逐 token
      data: [REASONING]<增量>   — 思考内容逐 token（开启 thinking 时）
      data: [ERROR]<信息>       — 错误
      data: [DONE]              — 结束
    内容里的换行用 {NL} 占位（SSE 行内不能有裸换行），前端还原。
    用户消息先落库；AI 回复流结束后落库（客户端中断也尽量保存已生成部分）。

    编排：准备（_prepare_world_chat）→ 命令/首轮流式 → 工具多轮循环（内联在本函数）→ 建议。
    """
    from app.models.world import WorldChatMessage

    ctx = await _prepare_world_chat(world_repo, world_id, user_id, items)
    if ctx is None:
        yield "data: [ERROR]世界不存在\n\n"
        yield "data: [DONE]\n\n"
        return
    world = ctx["world"]
    cfg = ctx["cfg"]
    api_key, api_base = ctx["api_key"], ctx["api_base"]
    model, thinking = ctx["model"], ctx["thinking"]
    tools_for_world = ctx["tools_for_world"]
    messages = ctx["messages"]
    sid_db = ctx["sid_db"]
    cmd_text = ctx["cmd_text"]  # 单条消息时即命令文本；_prepare_world_chat 已算好

    # ── 用户斜杠命令（不走 LLM，仅单条）；命令注册表见 world_chat_commands._COMMANDS ──
    if cmd_text.startswith("/"):
        _cmd_out: dict = {}
        async for event in _handle_slash_command(world_repo, world, world_id, cmd_text, user_id, sid_db, out=_cmd_out):
            yield event
        if _cmd_out.get("handled"):
            return

    # ── 第一轮前注入插入消息（2026-08-16 修复：原来只在工具轮循环内注入——
    # 若 AI 第一轮不调工具直接收尾，插入消息永远进不了上下文，用户要等下一轮才被 AI 看到）──
    try:
        await _inject_pending_user_messages(world_repo, world_id, messages, sid_db)
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 首轮插入消息注入失败（非致命）: {e}")

    # 本轮对话状态（温和去重 + 工作流记忆收集 + 审批门禁的轮次上下文）
    turn_state: dict = {"executed": {}, "tools_done": [], "turn_id": turn_id}

    # LLM 调用统一入口（工具轮/收尾共用）：与主系统一致走流式，规避非流式+tools+thinking 挂起
    async def _llm(messages: list, tools, round_no):
        from app.ai.llm import chat_completion
        _log_llm_request(world_id, turn_id, round_no, model, thinking, messages)
        return await chat_completion(
            messages=messages,
            model=model,
            api_base_url=api_base,
            api_key=api_key,
            temperature=cfg.get("temperature", 0.8),
            top_p=cfg.get("top_p", 0.9),
            thinking_enabled=thinking,
            stream=True,
            tools=tools,
        )

    # ── 首轮流式：正文/思考逐 chunk 透传，收集 tool_calls 与 usage ──
    _r1: dict = {}
    async for event in _stream_first_round(
        world_id, turn_id, model, thinking, api_base, api_key, cfg, tools_for_world, messages, out=_r1,
    ):
        yield event
    if _r1.get("aborted"):
        return
    full_content = _r1.get("full_content") or ""
    full_reasoning = _r1.get("full_reasoning") or ""
    first_usage = _r1.get("usage")
    tool_call_acc = _r1.get("tool_calls") or {}


    # 2.7：首轮用量落库（缓存命中统计）
    await _record_usage(world_repo, world_id, turn_id, "0", model, first_usage, messages)

    # ── 工具调用：多轮循环（list → write → …，最多 5 轮防死循环）──
    # 每轮：执行工具 → [TOOL] 事件显示 + 注入 AI → 继续带 tools 调 LLM，直到模型不再调工具
    # 收尾保障：工具场景的 AI 回复落库放 finally——客户端中断（流 aclose）也强制收尾，保证历史闭环
    # DeepSeek DSML/XML 兜底（2026-08-13）：首轮 content 里若有 XML 工具调用文本，解析为 tool_calls
    if not tool_call_acc and full_content:
        dsml_calls = _parse_dsml_tool_calls(full_content)
        if dsml_calls:
            tool_call_acc = {
                f"idx_{i}": {"id": tc["id"], "name": tc["function"]["name"], "arguments": tc["function"].get("arguments") or "{}", "index": i}
                for i, tc in enumerate(dsml_calls)
            }
            full_content = ""  # XML 已解析，正文不再透传

    had_error = False
    turn_error = None
    if tool_call_acc:
        _loop: dict = {}
        try:
            # 循环体已抽到 _run_tool_loop；finally/_closing/shield 的落库闭环刻意留在本函数
            async for event in _run_tool_loop(
                world_repo, ctx, world_id, turn_id, tool_call_acc, turn_state,
                full_content, full_reasoning, result=_loop,
            ):
                yield event
        except Exception as e:
            had_error = True
            turn_error = e
            logger.warning(f"🌐 世界 #{world_id} 工具执行/后续轮失败: {e}")
            yield f"data: [ERROR]{_friendly_llm_error(e)}\n\n"
        finally:
            # 从循环结果回填——成功与异常两条路径都要：
            # _run_tool_loop 在自己的 finally 里写入 out，异常时也带回已生成内容
            full_content = _loop.get("full_content", full_content)
            full_reasoning = _loop.get("full_reasoning", full_reasoning)
            # ── 落库 AI 回复（finally + shield：页面关闭/刷新导致任务取消，收尾照跑）──
            async def _closing():
                nonlocal full_content, full_reasoning  # 闭包内赋值：必须声明 nonlocal，否则 UnboundLocalError → 回复永不落库
                # 中断兜底：工具场景没生成最终回复 → 强制收尾轮；出错则落个闭环说明
                if tool_call_acc and not full_content:
                    if not had_error:
                        try:
                            heal_tool_chain(messages)   # 中断路径同样可能悬空：补齐再收尾
                            resp = await _llm(messages, None, "finalize")
                            full_content = (resp or {}).get("content") or "（工具执行完成）"
                            fr = (resp or {}).get("reasoning_content") or ""
                            if fr:
                                full_reasoning = fr
                        except Exception as e:
                            logger.warning(f"🌐 世界 #{world_id} 中断收尾失败: {e}")
                            full_content = full_content or "（工具执行中断）"
                    else:
                        full_content = _friendly_llm_error(turn_error) if turn_error else "（对话中断：工具执行出错，请重试或换个说法）"
                await _save_ai_reply(world_repo, world, full_content, full_reasoning, session_id=sid_db)
                # 工作流记忆：正常结束 → 清除；中断/出错（含余额不足这类"被迫终止"）→ 记录已做步骤，
                # 下次说「继续」接着做（判定收在 turn_completed 一处：出错提示不算正常结束）
                try:
                    cfg_all = dict(world.config or {})
                    if turn_completed(full_content, had_error):
                        cfg_all.pop("workflow_memory", None)
                    elif tool_call_acc:
                        cfg_all["workflow_memory"] = {
                            "interrupted_at": _now_local().isoformat(),
                            "tools_done": list(turn_state.get("tools_done", []))[-10:],
                        }
                    world.config = cfg_all
                    await world_repo.commit()
                except Exception as e:
                    logger.warning(f"🌐 世界 #{world_id} 工作流记忆保存失败: {e}")

            try:
                # shield：即使任务被取消（页面刷新/关闭），收尾与落库也跑完
                await asyncio.shield(_closing())
            except Exception as e:
                logger.warning(f"🌐 世界 #{world_id} 回复落库失败: {e}")
    else:
        # ── 非工具场景：落库 AI 回复（完整内容 + 思考过程）──
        try:
            await _save_ai_reply(world_repo, world, full_content, full_reasoning, session_id=sid_db)
        except Exception as e:
            logger.warning(f"🌐 世界 #{world_id} 回复落库失败: {e}")

    async for event in _emit_suggestions(world_repo, world, world_id, turn_state):
        yield event

    yield "data: [DONE]\n\n"


# ═══════════════════════════════════════════════════════════════
# 序列化
# ═══════════════════════════════════════════════════════════════