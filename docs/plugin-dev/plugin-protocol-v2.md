# Copree 全面插件化 — 阶段二设计方案 v4：语言中立行为插件

> 状态：设计稿 v4
> v3 → v4：执行器语言中立（插件可写 JS，Node 子进程执行；Python 进程内为默认快路径），
> 声明与行为合一（单文件单装饰器，消灭双写）
> 原则：backend-refactor-review + 现状导向（不为历史包袱服务，选当前最优）

## 1. 设计目标（从目标出发，不受历史路径绑定）

1. 插件能携带**可执行行为**，装好即可用，开关可控
2. 插件**语言中立**：作者可写 JS（与 DSH 生态一致）或 Python（与后端同语言）
3. 概念最少：**一个插件 = plugin.json（展示）+ 一个入口文件（声明+行为合一）**
4. 核心改动最小，现有声明插件完全兼容

## 2. 为什么语言中立

- 行为插件执行点在 LLM 回复链路：LLM 秒级 vs 插件毫秒级，语言性能差异不可感知
- 但**语言选择影响作者心智与生态**：DSH 插件是 JS，Copree 插件也应允许 JS
- 执行器可插拔 → 系统不替作者决定语言，默认 Python（零桥接）快路径，JS 走 Node 子进程

## 3. 插件契约（核心：单文件合一）

```
plugins/
  my-plugin/
    plugin.json          # 展示元数据（id/name/description/category/icon/version）
    plugin.js            # 【新】行为入口（JS 版，Node 子进程执行）
    plugin.py            # 【新】行为入口（Python 版，进程内执行，可选）
```

**二者必有其一**（都没有 = 纯声明插件，走旧 skill.json 兼容路径）。

### 3.1 JS 版入口（一等公民，与 DSH 生态同语言）

```js
// plugins/keyword-autoreply/plugin.js
module.exports = {
  skills: [
    {
      type: 'keyword_autoreply',
      category: 'action',           // action | inject
      name: '关键词自动回复',
      description: '命中关键词时注入回复指令',
      config_schema: {
        keywords: { type: 'array', items: { type: 'string' } }
      },
      handler: async (ctx) => {     // ctx = 统一上下文（纯 JSON）
        const keywords = ctx.config?.keywords || []
        if (keywords.some((k) => ctx.content.includes(k))) {
          ctx.result.injectPrompts.push('请优先回复与关键词相关的内容')
        }
      }
    }
  ]
}
```

- 一个文件 = 声明（type/category/name/schema）+ 行为（handler）→ **无双写**
- handler 收到**纯 JSON 上下文**，返回纯 JSON 结果 → 契约语言无关
- 插件作者不需要懂 Python；与 DSH 插件同语言，心智成本最低

### 3.2 Python 版入口（默认快路径）

```python
# plugins/keyword-autoreply/plugin.py
from app.services.plugin.api import skill

@skill(type="keyword_autoreply", category="action",
       name="关键词自动回复", description="命中关键词时注入回复指令",
       config_schema={"keywords": {"type": "array", "items": {"type": "string"}}})
def handle(ctx):
    keywords = ctx.config.get("keywords", [])
    if any(k in ctx.content for k in keywords):
        ctx.result.inject_prompts.append("请优先回复与关键词相关的内容")
```

## 4. 执行器架构

```
                         ┌─────────────────────────────┐
                         │   skill_engine 分发器（现有） │
                         │   evaluate_action_skills     │
                         └──────────────┬──────────────┘
                                        │ 按 type 查处理器
                    ┌───────────────────┴───────────────────┐
                    │          handler 注册表（现有）          │
                    │  _ACTION_HANDLERS / _INJECT_HANDLERS   │
                    └───────────────────┬───────────────────┘
                     (owner, handler)   │ 两种实现
        ┌───────────────────────────────┼───────────────────────────────┐
        │ Python 进程内（默认）           │ JS Node 子进程（可选）           │
        │ handler = Python 函数          │ handler = {language:'js',      │
        │ 直接调用（微秒级）              │          module, type}          │
        │                               │ 惰性 spawn Node + stdio RPC    │
        │                               │ （毫秒级，进程池复用）            │
        └───────────────────────────────┴───────────────────────────────┘
```

### 4.1 JS 执行器（`app/services/plugin/js_runner.py`，新增）

- 惰性启动单例 Node 子进程（`node runner.js`），stdio JSON-RPC
- `handler(ctx) → {ok, result}`：序列化上下文 → 子进程执行 → 反序列化结果
- 子进程崩溃 → 单次调用失败，核心捕获异常继续（隔离）
- 进程池复用（默认 1，可配），避免每次调用 spawn 开销

### 4.2 runner.js（随包携带）

```js
// backend/app/services/plugin/runner.js — 由 Node 加载插件并执行 handler
process.stdin.on('data', async (buf) => {
  const { pluginDir, type, ctx } = JSON.parse(buf)
  try {
    const mod = require(path.join(pluginDir, 'plugin.js'))
    const skill = mod.skills.find((s) => s.type === type)
    const result = await skill.handler(ctx)
    process.stdout.write(JSON.stringify({ ok: true, result }))
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(e) }))
  }
})
```

## 5. skill_engine 改动（与 v3 相同，最小）

- 注册表条目 `dict[str, tuple[str | None, Callable | JsHandler]]`，owner 并入条目（单一来源）
- `register_action_handler(type, owner=None)` / `register_inject_handler(type, owner=None)` 签名扩展
- 2 处分发查找：`entry = table.get(type); handler = entry[1] if entry else None`
- 内置 handler owner=None，永不回收；插件 handler owner=plugin_id，停用精确回收

## 6. skill_bridge 改动（加载/卸载，单一来源）

```python
def _load_plugin(plugin_id: str, plugin_dir: Path) -> None:
    """加载行为插件：plugin.py 进程内注册 / plugin.js 登记给 JS runner。"""
    py = plugin_dir / "plugin.py"
    if py.exists():
        # importlib 导入，触发 @skill 装饰器注册（owner=plugin_id）
    js = plugin_dir / "plugin.js"
    if js.exists():
        # 扫描 skills 声明，注册 {language:'js', module, type} 处理器（owner=plugin_id）

def _unload_plugin(plugin_id: str) -> None:
    """从两个注册表回收 owner == plugin_id 的条目。"""
```

生命周期与 skill 元数据一致（启动 + 开关切换 + rescan 三处，均已有调用点）。

## 7. 诚实标注改动面

| 文件 | 改动 | 性质 |
|---|---|---|
| `skill_engine.py` | 注册表条目 `(owner, handler)`，2 处分发查找，签名扩展 | 核心小改 ~15 行 |
| `skill_bridge.py` | +`_load_plugin` / `_unload_plugin`，apply 内调用 | 纯新增 |
| `plugin/js_runner.py` + `runner.js` | Node 子进程执行器 | 纯新增（无此需求可不装 Node） |
| `plugin/api.py` | `@skill` 装饰器（Python 版声明+行为合一） | 纯新增 |
| 示例插件 | keyword-autoreply（JS + Python 双版本） | 新增验证用 |
| 单测 / CHANGELOG / README | 语言中立插件说明 | 文档 |

## 8. 验证闭环（四层）

1. 加载层：plugin.py/plugin.js 均被正确加载注册，owner 正确
2. 分发层：evaluate_* 命中两种执行器（Python 进程内 / JS 子进程）
3. 开关层：停用 → owner 条目精确回收；启用 → 重载；同名类型 A 卸载不误删 B；JS 子进程崩溃隔离
4. 集成层：装 keyword-autoreply（JS 版）→ 发消息 → 触发注入

## 9. 边界

- 不做：事件总线、消息钩子、命令系统（YAGNI，现有 skill 机制已覆盖注入诉求）
- 不做：world 类别实现（与本次正交，保持契约占位）
- 不做：C/Rust 原生扩展（LLM 秒级链路里收益为零，仅计算密集型插件才需要——Copree 目前没有）
- 安全：行为插件=代码执行，仅管理员安装；JS 子进程天然进程隔离；README 明示
- Node 为**可选依赖**：无 plugin.js 插件时不需要 Node 运行时

## 10. 为什么这是优雅的

- **语言中立**：契约纯 JSON，执行器可插拔，系统不替作者决定语言
- **单文件合一**：声明+行为一个文件一个装饰器/一个 export，消灭 v3 的双写
- **单一来源**：owner 在注册表条目内；加载/卸载收敛在 skill_bridge
- **最小改动**：skill_engine ~15 行，其余纯新增；旧声明插件完全兼容
- **隔离**：JS 插件进程隔离，崩溃不拖垮核心；Python 插件进程内零桥接
- **现状导向**：不为历史包袱服务，按当前最优选路，可替换旧方案
