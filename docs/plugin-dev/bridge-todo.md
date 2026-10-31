# 生态转接 — 待办清单

> 配套：`bridge-protocol.md`（协议本体，已提交 83c11bf4）
> 口径：**不优雅的部分不做**。下面每条都标了"能不能优雅地做"，标 ❌ 的直接放弃，不进待办。

## 零、已经确定的事实（不用再调研）

| 事实 | 来源 | 影响 |
|---|---|---|
| AstrBot 与 DSH 的 skill 是同一套 Agent Skills 规范（`SKILL.md` + frontmatter） | AstrBot 官方文档「随插件提供 Skills」；`@deepseek-ai/dsh-skill` README | 技能层零成本，值得先做 |
| 小型 AstrBot 插件只用 6 个符号（`filter.command` / `Star` / `Context` / `AstrBotConfig` / `logger` / `AstrMessageEvent`） | 实测 `astrbot_plugin_bilibili_comment_query/main.py` | 兼容层薄，可控 |
| 大型插件另需 `context.send_message / add_llm_tools / register_web_api`、`MessageChain`、`Image/Plain/Node`、`FunctionTool` | 实测 `Soulter/astrbot_plugin_bilibili` | 推送链路可行，WebUI 页面不行 |
| Copree 后端就是 FastAPI/Starlette | 现状 | `astrbot.api.web` 可原样转发，不用仿造 |
| **后端目前没有 PyYAML** | `backend/requirements.txt` | `metadata.yaml` 解析要先解决依赖问题 |

## 一、P1 目录嗅探 + SKILL.md 导入（建议先做，收益最大）

- [ ] `backend/app/bridges/detect.py`：按优先级嗅探 `plugin.json` → `metadata.yaml+main.py` → `SKILL.md`，
      合成 manifest（`category: "bridge"` + `bridge.source`）
- [ ] `backend/app/services/plugin/catalog.py::_scan_dir`：没有 manifest 时调用嗅探，不再直接 `continue`
- [ ] `backend/app/bridges/skills.py`：解析 frontmatter（`name` / `description`）+ 正文，
      生成与 `skill.json` 同构的技能定义；每个 SKILL.md 一个技能类型（可单独开关）
- [ ] 注入处理器：`register_inject_handler(type)` 读文件正文注入（`evaluate_inject_skills` 同一条链路）
- [ ] `skill_bridge.BRIDGED_CATEGORIES` 加 `"bridge"`，加载/回收走现有 `_register_plugin_skills` / `_unload_plugin`
- [ ] 前端 `PluginManager.tsx`：`bridge` 类别的图标/标签/来源徽标（AstrBot / SKILL）
- **验收**：把 `Soulter/astrbot_plugin_bilibili` 的 `skills/` 目录原样丢进 `data/plugins/`，
      重启后 AI 的技能清单里出现它，启用后系统提示词里出现正文，一行代码没改。
- **工作量**：~250 行 + 2 个测试。**优雅度**：✅ 完全复用现有技能链路，无新概念。

## 二、P2 AstrBot 兼容运行时（命令类插件）

- [ ] 依赖决策：加 `PyYAML`（标准、一处依赖） vs 自己写扁平 YAML 解析（**不优雅，遇列表/嵌套就错**）→ 建议加 PyYAML
- [ ] `backend/app/bridges/astrbot/vendor/astrbot/`：`__init__` / `api.all` / `api.event` / `api.event.filter` /
      `api.star` / `api.message_components` / `api.web`(转发 starlette) / `core.star.filter.command.GreedyStr` / `core.agent.tool.FunctionTool`
- [ ] `install()`：仅当 `import astrbot` 失败时才注入 `sys.path`，绝不覆盖真实 AstrBot
- [ ] `AstrBotConfig`：由 `_conf_schema.json` 生成默认值，按 `plugin_configs` 覆盖（与 QQ 通道同一套加密）
- [ ] `@filter.command(alias/greedy)`、`@filter.regex` → Copree 消息触发器
- [ ] `event.plain_result / chain_result / stop_event` → 出站消息
- [ ] `Star.put_kv_data / get_kv_data` → 按 `plugin_id` 隔离的 KV
- **验收**：`astrbot_plugin_bilibili_comment_query` 原样放入，群里发 `/uid 2 1 5` 能收到回复（HTTP 打桩）。
- **工作量**：~600 行 + 3 个测试。**优雅度**：✅ 在自身进程里提供一个合法实现，不是"翻译源码"。

## 三、P3 LLM 工具 + 主动推送

- [ ] `@filter.llm_tool` / `FunctionTool` / `context.add_llm_tools` → 动态 `ToolPlugin`（`ToolRegistry.register`）
- [ ] `context.send_message(umo, chain)` → 按 umo 解析成 Copree 群/私信并发出（QQ 通道已有 umo 映射）
- [ ] `MessageChain` + `Plain/Image/At` → Copree 消息组件；`Node/Record/Video/Button` 降级为文本占位并记日志
- **验收**：B 站订阅插件的"新动态 → 推到订阅群"链路能跑通（不打桩，用真实订阅）。
- **工作量**：~400 行。**优雅度**：✅ 但依赖 P2 稳定。

## 四、明确不做（不优雅 / 成本远超收益）

| 项 | 为什么不做 |
|---|---|
| `context.register_web_api` 挂载插件 WebUI | 需要插件前端沙箱 + 路由命名空间隔离 + 权限矩阵；v1 只做 API、不做页面，硬接会把管理台变成插件后台 |
| AstrBot 平台原生事件（OneBot 原始事件、按钮回调） | 语义在 Copree 里没有落点，映射出来也是个四不像 |
| `requirements.txt` 自动 pip 安装 | 污染镜像、不可复现、装失败还把后端拖挂 |
| cordis JS 插件运行时 | 要整 Node 运行时 + DSH 服务契约，等于重写一个 DSH；只取它的 SKILL.md 与 MCP 连接即可 |
| 假装支持：调用到没实现的 API 却静默返回 | 插件作者会以为"在 Copree 也能用"，坏消息应该当场报 `BridgeUnsupported` |

## 五、顺序与代价

```
P1（技能直通）  → 立刻可用，覆盖 DSH 插件与 AstrBot 插件里的 SKILL.md
P2（命令兼容）  → 覆盖绝大多数小型 AstrBot 功能插件
P3（工具+推送） → 覆盖 B 站订阅这类"定时 + 主动发消息"的重型插件
```

风险提示：外来插件是**本进程执行的代码**，与 `WORLD_TOOLS_DIR` 同权限。P2 一旦落地，"装插件"就等于"跑别人的代码"，
安装页必须写明这一条（协议文档 §5 已有口径）。
