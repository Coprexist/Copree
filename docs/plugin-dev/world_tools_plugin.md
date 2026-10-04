# 世界工具插件开发（World Tool Plugin）

> 读者：想给群视界加一个工具的开发者 / 社区贡献者。
> 结论先说：**一个工具一个文件，写完不用改任何清单**，注册表在导入时校验契约缺一不可。

---

## 1. 三分钟上手

```
backend/app/tools/world/
├── base.py            # 插件基类 + 注册中心（框架件，不是工具）
├── shared.py          # 多个工具共用的东西（参数解析、群 id 解析、两段式下载）
├── _template.py       # 复制这个文件开新工具（下划线开头不会被加载）
├── file_read.py       # 一个工具一个文件
├── file_write.py
└── ...
```

复制 `_template.py` 成 `my_tool.py`，改类名与 `name`，重写 `execute` / `summary` 即可。
不需要注册、不需要改 `__init__.py`、不需要改任何 schema 清单——
`app/tools/__init__.py` 会扫描整个 `app/tools/` 目录并把每个模块导入一次，
`WorldToolPlugin` 的子类在**定义时自注册**。

## 2. 契约（缺一个都注册不进来）

| 成员 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 工具名，也是 LLM 的 function name；文件名与它保持一致 |
| `label` | ✅ | 卡片标题的中文名，如「读文件」 |
| `segment` | ✅ | 分组：`file` / `group` / `memory` / `net` / `world` / `self` / `approval`（审批与协作） |
| `description` | ✅ | 给 LLM 看的说明：做什么、什么时候用 |
| `parameters` | ✅ | JSON Schema 的 properties；无参数写 `{}` |
| `required` | ✅ | 必填参数名列表；无必填写 `[]` |
| `exposed` | ➖ | `False` = 只给斜杠命令用，不发给 LLM |
| `execute(ctx)` | ✅ | 返回 dict，成功带 `success=True`；失败带 `error` |
| `summary(result)` | ✅ | **卡片折叠时那一行**：刚做了什么、结果如何 |
| `detail(args, result)` | ➖ | 点开卡片后的详细说明；默认渲染「参数 + 结果」。它会随消息落库（`tool_detail` 列），刷新后仍可展开 |

**为什么要强制 `summary`**：不强制就会有人漏写，卡片上只剩一句没有信息量的兜底文案，
用户根本不知道刚才发生了什么。注册表因此在类定义时就检查，缺了直接抛 `TypeError`，
工具作者在导入阶段就会看到，而不是等用户在聊天里发现。

`execute` 拿到的 `ctx`（`WorldToolContext`）：

| 字段 | 说明 |
|---|---|
| `ctx.args` | 已解析好的参数 dict（**入口只解析一次**，别再自己 `json.loads`） |
| `ctx.arguments` | 原始 JSON 字符串（极少数需要原样的场景） |
| `ctx.world` | 当前世界（World 模型，`ctx.world.id` 就是世界号） |
| `ctx.world_repo` | 世界仓储；读写数据库走它，别自己开 session |
| `ctx.turn_state` | 本轮对话的可变状态（去重表、建议问题等），可为 `None` |
| `ctx.progress(note)` | 耗时工具的分阶段进度，前端会原地更新同一张卡片 |

## 3. 注册流程与失败行为

```
app.tools 导入
  └─ _discover_tools()     扫描 app/tools/**/*.py → importlib 导入
       └─ app.tools.world.<你的工具> 导入
            └─ class MyTool(WorldToolPlugin) 定义 → WorldToolRegistry.register()
                 ├─ 校验 label / segment / description / summary  → 不合格抛 TypeError
                 └─ 通过：进表，定义列表缓存失效
```

- 导入失败的模块**只记一条 warning**，不会拖垮启动（社区插件容错）；但内置工具漏注册会被 CI 挡住：
  `tests/test_world_tool_summaries.py` 断言「发给 LLM 的工具集合 == 已实现插件集合」。
- 缺少 `summary` 或 `label` 的类会在这里报错：

  ```
  世界工具 my_tool 必须实现 summary()（卡片那一行显示什么）
  ```

## 4. 性能

插件化**不是**靠牺牲速度换来的：

- 执行分发是**字典查表**（`WorldToolRegistry.get(name)`），比原来那条几十个分支的 if 链更快；
- 工具定义列表（`WORLD_TOOLS`）**首次访问时构建一次并缓存**，请求路径上不再重建；
- 参数只解析一次（入口 `run_world_tool`），工具里直接用 `ctx.args`；
- 发现（import 各工具模块）只在 `app.tools` 首次导入时做一次。

## 5. 社区插件：不改主仓库也能装

把插件目录（一个 `.py` 一个工具）放到任意路径，配环境变量即可：

```bash
WORLD_TOOLS_DIR=/opt/copree-tools docker compose up -d backend
```

启动时会扫描该目录并注册；文件导入失败只记日志。
注意：该目录里的代码与主仓库同权限运行，**只放可信代码**。

## 5.5 改了工具，AI 什么时候知道（版本式）

你的工具定义属于**世界源**（`world-{id}`）：定义变了 → 写一个新版本 + 自动 changelog，然后在 AI 下一轮
**落一条变更通知条目**（紧跟在历史后面）；前缀里的旧定义要等它 compact / 清空上下文才整体换新。

所以改定义**不会立刻换掉 AI 手里的工具描述**，但通知当轮就到，AI 会知道「有这个变化、以新描述为准」。
删掉一个工具时，锁定态的请求里仍带着旧定义（模型可能凭记忆调用），调用会回 `UNKNOWN_TOOL`——这是刻意的：
宁可回一个明确错误，也不在对话中途改工具数组、把整段前缀缓存打掉。

判断与通知的三种方法（三选一）见[能力懒加载](../dev/capability_lazy_loading.md)；通道类插件见
[写一个通道插件](./channel-plugins.md) §4.5。
## 6. 自测

```bash
# 在 backend 容器里确认注册与展示文案
docker exec -i ai_group_backend python -c "
from app.tools.world import WorldToolRegistry, WORLD_TOOLS, tool_result_summary
print(len(WorldToolRegistry.all()), len(WORLD_TOOLS))
print(tool_result_summary('my_tool', {'success': True}))
"

# 契约守卫 + 全量测试
PROD=$(docker exec ai_group_backend printenv DATABASE_URL); TEST=${PROD/\/ai_group_chat/\/ai_group_chat_test}
docker exec -w /app -e TEST_DATABASE_URL="$TEST" -e TEST_DATABASE_URL_SYNC="${TEST/+asyncpg/}" \
  ai_group_backend python tests/run_without_pytest.py test_world_tool_summaries
```

---

## English

### World tool plugins — one file per tool, zero registration

Drop a file under `backend/app/tools/world/`, subclass `WorldToolPlugin`, and you are done:
`app/tools/__init__.py` imports every module under `app/tools/` on startup, and the registry
picks up subclasses as they are defined. There is no manifest to edit and no schema list to sync.

**Required contract** (enforced at class definition time — a plugin missing any of these fails
to register and raises `TypeError` immediately):

| Member | Required | Meaning |
|---|---|---|
| `name` | ✅ | Tool name / LLM function name; keep the file name identical |
| `label` | ✅ | Human-readable Chinese title shown on the card |
| `segment` | ✅ | Grouping: `file` / `group` / `memory` / `net` / `world` / `self` / `approval` |
| `description` | ✅ | What the tool does and when to use it (written for the LLM) |
| `parameters` / `required` | ✅ | JSON Schema properties and required names; use `{}` / `[]` when empty |
| `execute(ctx)` | ✅ | Returns a dict; `success=True` on success, `error` on failure |
| `summary(result)` | ✅ | **The one line shown when the card is collapsed** |
| `detail(args, result)` | ➖ | Text shown when the card is expanded; defaults to args + result. It is persisted with the message (`tool_detail`), so it survives a reload |
| `exposed = False` | ➖ | Internal tool: dispatchable by slash commands, not sent to the LLM |

`summary` is mandatory on purpose: without it the chat card degrades to a meaningless
"tool executed successfully" line. The registry rejects such a class at import time.

Inside `execute` you get `ctx.args` (already parsed — do not re-parse), `ctx.world`,
`ctx.world_repo`, `ctx.turn_state` and `await ctx.progress("…")` for long-running tools.

### Performance

Dispatch is a dictionary lookup (faster than the previous if-chain), the LLM-facing definition
list is built once and cached, arguments are parsed once per call, and module discovery happens
once when `app.tools` is first imported.

### Out-of-tree plugins

Point `WORLD_TOOLS_DIR` at a directory of `.py` files and they are discovered at startup,
so community tools do not need to fork this repository:

```bash
WORLD_TOOLS_DIR=/opt/copree-tools docker compose up -d backend
```

Plugins run with the same privileges as the backend — only load code you trust.

### Tool changes — when does the AI learn?

Your tool definitions belong to a **world source** (`world-{id}`): any change writes a new version with an
auto-generated changelog, and the AI gets one change-notice entry in its ledger on the next turn. The tool
definitions inside its frozen prefix are swapped only when that conversation compacts or clears.

So a definition change is announced immediately but applied lazily. A removed tool stays in the frozen request
on purpose — calling it returns a clear `UNKNOWN_TOOL`, which is cheaper than invalidating the whole prefix
mid-conversation.

The three ways to decide and deliver a notice are in [capability lazy-loading](../dev/capability_lazy_loading.md);
for channel plugins see [writing a channel plugin](./channel-plugins.md) §4.5.
### Guard test

`backend/tests/test_world_tool_summaries.py` asserts that the set of tools exposed to the LLM
equals the set of registered plugins, and that every plugin produces a meaningful summary on
both the success and the failure path.
