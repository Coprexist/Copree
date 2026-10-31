# 生态转接协议 v1（bridge）

> 状态：**设计定稿，兼容层实现中**（2026-10-31）
> 前置：`plugin-protocol-v3.md`（目录即插件 + 服务类）、`skill_catalog_and_modes.md`（技能目录）
> 一句话：**外来插件不改一行代码，靠「目录嗅探 + 原语映射」进入 Copree 的能力体系。**

## 1. 为什么要"协议"而不是"移植"

三个生态各有各的运行时：

| 生态 | 插件形态 | 运行时 | 能力载体 |
|---|---|---|---|
| Copree | `plugin.json` + 可选 `plugin.py` | Python（本进程） | `ToolPlugin` / `@skill` / `@service` |
| AstrBot | `metadata.yaml` + `main.py`（`Star` 子类） | Python | `@filter.command` / `@filter.llm_tool` / `context.send_message` |
| DSH | cordis JS 插件包 + 内嵌 `SKILL.md` | Node | `ctx.skills.register` / 工具 / MCP |

把 AstrBot 插件"移植"到 Copree = 逐个改写 = 生态永远追不上。
所以这里定义的是**转接**：目录原样放进来，Copree 认出它是什么，把它的**能力原语**翻译成自己的原语。

关键事实（决定了成本）：**AstrBot 与 DSH 的 skill 都是同一套 Agent Skills 规范**——
`SKILL.md` + YAML frontmatter（`name` / `description`）+ 正文 + 可选 `references/`。
AstrBot 官方文档明确支持插件目录下的 `skills/<name>/SKILL.md`，DSH 的 `@deepseek-ai/dsh-skill` 读的也是它。
**这一层转接成本为零**，不是"支持一下"，是天生可读。

## 2. 目录嗅探（不写 plugin.json 也能装）

`app/services/plugin/catalog.py` 扫描 `backend/plugins/` 与 `<数据根>/plugins/`。
一个目录没有任何 manifest 时，按下列顺序判定来源，命中即合成一份 Copree manifest：

| 优先级 | 判据 | 来源 | 合成结果 |
|---|---|---|---|
| 1 | `plugin.json` 存在 | copree | 原样（现状不变） |
| 2 | `metadata.yaml`（或 `.yml`）且 `main.py` 存在 | astrbot | `category: "bridge"`, `bridge.source: "astrbot"` |
| 3 | `SKILL.md` 存在，或 `skills/*/SKILL.md` 存在 | agent-skills | `category: "bridge"`, `bridge.source: "skills"` |
| 4 | 都不满足 | — | 不登记（记一条 warning，不吵） |

合成的 manifest 只补**展示与调度必需**的字段（`id` 取目录名、`name` 取 `metadata.yaml.name` 或首个
SKILL 的 `name`、`description` 取同源字段），并把来源写进 `bridge`：

```json
{
  "id": "astrbot_plugin_bilibili_comment_query",
  "name": "B站评论查询",
  "category": "bridge",
  "bridge": { "source": "astrbot", "config": "_conf_schema.json", "entry": "main.py" }
}
```

**写了 plugin.json 就以作者写的为准**：合成只发生在"作者没写"的情况下，绝不覆盖。
这也给了插件作者一个升级路径：想标成 Copree 原生，就自己写一份 manifest。

## 3. 原语映射表（协议本体）

### 3.1 agent-skills 来源（DSH 插件 / AstrBot 的 `skills/` / 任何 SKILL.md 包）

| 外来 | Copree | 说明 |
|---|---|---|
| `SKILL.md` frontmatter `name` | 技能 `type` | 统一小写连字符；冲突加 `-<插件id>` 后缀 |
| `description` | 技能 `description` | 同时进技能目录，AI 靠它决定要不要用 |
| 正文 | 注入正文 | `category: "inject"`，与现有 `skill.json` 等价 |
| `references/**` | 可读附件 | 正文里按相对路径引用，AI 用 `file_read` 打开 |
| 无 | `config_schema` | 空；纯提示词技能不需要配置 |

### 3.2 astrbot 来源（功能型插件）

| 外来原语 | Copree 原语 | 覆盖程度 |
|---|---|---|
| `metadata.yaml` | `plugin.json` 的 name/description/version/author | 完整 |
| `_conf_schema.json` | 插件配置 schema（`plugin_configs` 表） | 完整（`secret` 走同一套加密） |
| `@filter.command(name, alias)` | 命令触发器 | 完整 |
| `@filter.regex(pattern)` | 正则触发器 | 完整 |
| `@filter.event_message_type(...)` | 消息事件钩子 | 部分（只映射"收到消息"这一层，平台细分事件无对应） |
| `@filter.llm_tool` / `FunctionTool` / `context.add_llm_tools` | `ToolPlugin`（function calling） | 完整 |
| `event.plain_result` / `chain_result` | 出站消息 | 完整（`Plain` / `Image` 支持；其余组件降级为文本占位） |
| `context.send_message(umo, chain)` | 主动发消息（按 umo 找群/私信） | 完整 |
| `self.put_kv_data/get_kv_data` | 插件 KV 存储 | 完整（按 `plugin_id` 隔离） |
| `context.register_web_api(...)` | 挂到 Copree 的 FastAPI 路由 | 部分（前缀隔离，仅管理员可见可选） |
| `terminate()` | 插件卸载时的回收 | 完整 |
| 平台适配器事件（OneBot 原生事件、按钮回调） | — | **不支持**，明确报错而非静默 |
| 插件自带 WebUI 页面（`pages/`） | — | **不支持**（v1 内不做前端沙箱） |
| `requirements.txt` | — | **不自动安装**：镜像里没有的依赖会让插件加载失败，需管理员自行装 |

## 4. 兼容运行时（`app/bridges/astrbot/`）

不是"翻译代码"，是**在 `sys.modules` 里提供一个叫 `astrbot` 的实现**：

```
app/bridges/astrbot/
  vendor/astrbot/          # 兼容包：import astrbot 时命中它（仅当真实 AstrBot 未安装）
    __init__.py            # logger / AstrBotConfig / FunctionTool
    api/__init__.py        # 同上导出
    api/all.py             # * 导出（多数插件写 from astrbot.api.all import *）
    api/event.py           # filter, AstrMessageEvent, MessageChain, MessageEventResult
    api/event/filter.py    # command / regex / event_message_type / llm_tool / ...
    api/star.py            # Context, Star
    api/message_components.py  # Plain / Image / At / Node / File / Record / Video
    api/web.py             # 直接转发 starlette：Copree 本身就是 FastAPI
    core/star/filter/command.py  # GreedyStr
    core/agent/tool.py     # FunctionTool
  host.py                  # Copree 消息 ⇄ AstrMessageEvent；结果 ⇄ 出站消息
  loader.py                # 导入 main.py、实例化 Star 子类、注册触发器
```

三条硬规矩：

1. **只在自己没有真实 AstrBot 时才注入**：`import astrbot` 成功且 `__version__` 存在 → 不覆盖。
2. **不支持的原语要响**：调用到未实现的 `context.*` 或组件时抛 `BridgeUnsupported`，
   插件里被 try 包住也不影响宿主，但日志里能看见"这个插件要的东西我们没有"。
3. **外来代码与核心同权限**（与 `WORLD_TOOLS_DIR` 同口径）：只放可信来源，安装前后台会提示。

## 5. 安全与信任

- 外来插件是**本进程执行的代码**，能读 `data/`、能发网络请求。定位与现状的 `WORLD_TOOLS_DIR` 一致：
  可信代码才装，这不是沙箱。
- 配置项里的秘密走 `plugin_configs.is_secret` + Fernet，与 QQ 通道同一条链路，API 只回 `has_value`。
- `register_web_api` 暴露的接口默认挂在 `/plugins/<id>/api/` 下，继承 Copree 的鉴权中间件；
  v1 不做"插件自带前端页面"。

## 6. 分阶段

| 阶段 | 内容 | 状态 |
|---|---|---|
| P1 | 目录嗅探 + manifest 合成 + SKILL.md 导入（零代码改动） | 本文档同批实现 |
| P2 | AstrBot 兼容运行时：command / regex / plain_result / config / KV | 本文档同批实现 |
| P3 | `llm_tool` / `FunctionTool` → Copree 工具；`send_message(umo)` 主动推送 | 排期中 |
| P4 | `register_web_api` 路由挂载；真实插件（B站订阅）端到端验证 | 排期中 |
| 不支持 | cordis JS 插件本体、AstrBot 插件前端页面、平台原生事件 | 明确不做 |

判定标准只有一条：**拿真实插件跑，不改它一行。** 自测用的是 `astrbot_plugin_bilibili` 与
`astrbot_plugin_bilibili_comment_query` 两个真实仓库，不是自己写的玩具。
