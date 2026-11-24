# 插件工具怎么报错

**适用**：在本平台写 Python 工具（`@tool` / `ToolPlugin`）的插件作者。
**一句话**：能说清原因的错误用 `ToolError` 声明；说不清的让异常抛出去。**永远不要在工具里同步阻塞。**

---

## 1. 三条路，只有两条对

| 写法 | 模型看到 | 平台日志 | 评价 |
|---|---|---|---|
| `raise ToolError(INVALID_ARGS, "缺少 group_id")` | `{error, error_code, message, retryable?}` | `工具 x 报业务错误 INVALID_ARGS（plugin=你的插件）` | ✅ 推荐：语义完整 |
| `return {"error": True, "message": "..."}` | 只有 `error` + `message` | 无 | ⚠️ 能用，但没有 code，模型不知道要不要重试 |
| `raise ValueError("...")` | `{error_code: TOOL_EXEC_FAILED, message: "执行失败：..."}` | 带 traceback + 插件 id | ⚠️ 兜底，语义丢失 |
| `time.sleep(5)` / `requests.get(...)` | —— | —— | ❌ **会卡死整个平台**，见 §4 |

---

## 2. ToolError

```python
from app.tools.errors import ToolError, INVALID_ARGS

class MyTool(ToolPlugin):
    name = "my_tool"
    ...

    async def execute(self, db, agent_id, group_id, arguments, context):
        group_id = arguments.get("group_id")
        if not group_id:
            # 参数问题：模型改一下就能成功
            raise ToolError(INVALID_ARGS, "缺少 group_id，请传要操作的群编号", retryable=True)

        try:
            data = await fetch_something()
        except UpstreamBusy:
            # 外部服务忙：换个时间可能成功
            raise ToolError(TOOL_RATE_LIMITED, "对方服务忙，稍后再试", retryable=True)
        except UpstreamGone:
            # 对方没了：重试也没用，不设 retryable
            raise ToolError(TOOL_UNAVAILABLE, "目标不存在或已删除")

        return {"success": True, "data": data}
```

### 字段

| 字段 | 作用 |
|---|---|
| `code` | 稳定标识，日志/计数器/前端共用。**改文案不改 code** |
| `message` | 给模型和用户看的一句话。别放堆栈、别放密钥 |
| `retryable` | `True` = 换种方式可能成功（模型会更倾向重试）。默认 `False`，**别乱开**：一个永远失败的操作配 `retryable=True` 会被反复重试 |
| `outward` | `True` = 失败后果由第三方承担（发消息、发文件）。平台会**额外私信通知 AI 的主人**，避免「以为发了」 |

### 可用的 code

| code | 什么时候用 |
|---|---|
| `INVALID_ARGS` | 参数缺/类型错/业务前置不满足（未登录、不在群里、状态不允许） |
| `TOOL_RATE_LIMITED` | 对方限流、配额用完，稍后可能好 |
| `TOOL_UNAVAILABLE` | 目标不存在、服务下线，重试无用 |
| `TOOL_TIMEOUT` | 你内部有个带超时的调用超时了 |
| `TOOL_EXEC_FAILED` | 兜底（一般不用自己抛，未捕获异常会自动归到这里） |

**不要自造 code**：模型与前端按这几个值判断，新 code 只会被当作未知。

### 抛出去之后

平台会：

1. 把 `{error, error_code, message, retryable?}` 作为工具结果回给模型；
2. 记一次失败调用（指标 + 日志，日志带 `plugin=你的插件 id`）；
3. 同一轮里如果模型看到失败却继续 end_turn，平台会补一条提醒，推动它补救或说明。

**不会**：回滚事务。ToolError 视为「没有半个写操作」，所以抛出前若你已经写了库，那些写操作会照常提交。

---

## 3. 未捕获异常

```python
raise RuntimeError("我真的炸了")
```

平台会：回滚会话（防止脏事务拖垮整轮）→ 记 `TOOL_EXEC_FAILED` 带 traceback 与插件 id → 把「执行失败：…」回给模型。

**这是兜底，不是用法。** 用它意味着模型拿不到任何可操作的语义。

---

## 4. 绝对不要同步阻塞 —— 这条会卡死整个平台

插件与平台**在同一个事件循环里**运行。工具里出现同步 IO 时：

- 事件循环整个停住；
- 定时器排不上队 → **超时机制也失效**；
- 私信、闹钟、触发器是串行消费的 → **整个平台不再处理任何消息**。

会被点名的写法：

| 别写 | 改成 |
|---|---|
| `time.sleep(5)` | `await asyncio.sleep(5)` |
| `requests.get(url)` | `await httpx.AsyncClient().get(url)` |
| `open(path).read()`（大文件） | `await asyncio.to_thread(pathlib.Path(path).read_text)` |
| `subprocess.run([...])` | `await asyncio.create_subprocess_exec(...)` |
| 同步版 LLM/数据库客户端 | 用它的 async 版本；没有就 `await asyncio.to_thread(fn, ...)` |

```python
import asyncio
from functools import partial

# 第三方库只有同步版时：丢到线程池，别占事件循环
data = await asyncio.to_thread(partial(blocking_lib.query, sql, limit=10))
```

平台侧有事件循环延迟监控：单次阻塞超过 1 秒会在日志里打出 `loop lag` 与当时正在执行的工具名。看到它，先找是不是自己的插件。

---

## 5. 超时

工具默认 120 秒超时。确实需要更久的（生图、大文件、外部爬取），在插件类上声明：

```python
class LongTool(ToolPlugin):
    timeout_seconds = 300   # 秒
```

超时的后果：本次调用返回 `TOOL_EXEC_FAILED`（带 `retryable`），会话会被复原。**不要**靠「声明一个超大的超时」绕过设计——真正长到分钟级的任务，应该等平台的后台任务能力（见 `docs/dev/tool_execution.md`），而不是占住一轮对话。

---

## 6. 自检清单

- [ ] 参数校验抛 `ToolError(INVALID_ARGS, ...)`，不是 `ValueError`
- [ ] 外部服务失败区分「可重试」与「不可重试」，只给前者 `retryable=True`
- [ ] 对外动作（发消息/文件）失败时 `outward=True`
- [ ] 工具里没有 `time.sleep` / `requests.` / `subprocess.run` / 大文件同步 `open().read()`
- [ ] 长任务声明了 `timeout_seconds`
- [ ] 自己没有造新的 error code
