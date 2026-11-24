# 错误处理：分层兜底 → 可归因、可止损、可告知

**状态**：设计稿（未实现）。2026-11-25 由外部反馈（`data/users/1/files/error-handling-2026-11-25.md`）+ 当前 HEAD 逐条核实得出；凡「助手推断」处均非用户构思。
**范围**：一个错误从产生到「有人知道」的整条路径——HTTP 层、工具层、AI 轮次层、后台任务层。不含前端错误边界与部署层（那两项各自独立）。
**取代关系**：无既有文档。本文落地后，`docs/dev/` 下关于「异常怎么兜」的说法只此一份。

---

## 0. 一句话

**错误可以不处理，但不可以不知道**——每一处 `except` 都要能回答「吞掉之后谁负责」，答不出来就是在制造静默失败。

---

## 1. 反馈与核实

### 1.1 反馈的五条，逐条核实

来源：用户转来的 `error-handling-2026-11-25.md`（环境写的是 Copree 0.4.16）。

| # | 反馈的说法 | 在当前 HEAD 上核实 |
|---|---|---|
| 1 | 后台任务错误完全静默，实测一次自激循环跑了 70,299 次无告警 | **成立**。`failure_watch` 只统计「按 agent 的连续失败」，而那次循环每一轮都是「成功但无进展」，一次失败都没记 |
| 2 | 用户可见的错误通知只覆盖 API 四种 | **成立**。`executor.py:288` 的 `guidance` 只有 `no_api_key / insufficient_balance / auth_error / all_failed` |
| 3 | 删 AI 的 `ForeignKeyViolationError` 直接 500 | **已修**。0128 之后 `agents` 关联表全部 `ondelete="CASCADE"`（`models/agent.py`），`delete_agent` 不再撞 FK。反馈贴的是老代码（待真机复测确认） |
| 4 | 错误没有聚合，同一个错刷几万次也没人知道 | **成立**。全进程没有错误计数器，`/health` 只有 `status/version/build/migration` |
| 5 | 工具失败没有提醒、也没超时 | **成立**。`executor.py:1468` 的提醒只管「有文字没工具调用」；`tools/base.py:570` 是裸 `await plugin.execute(...)`，无 `wait_for` |

反馈里「全项目 `except Exception` 1111 次」这个数字不对：当前仓库 `backend/app` 是 **618 处**（`ai/` 99、`tools/` 72、`routers/` 75）。数量本身不是问题，问题是这 618 处里有多少答不出「吞掉之后谁负责」。

### 1.2 反馈没提、但同类的两处

| 位置 | 现状 | 归类 |
|---|---|---|
| `tools/base.py:592` 触发规则 | `except Exception` → `logger.debug` | 可接受（调试级），但要能计数 |
| `response_worker` 轮询主循环 | 只有单轮失败统计，没有「同一事件反复来」的检测 | 缺口（§3.5） |

---

## 2. 现状：四层与四个缺口

### 2.1 四层

```
① HTTP 层      main.py 全局兜底 + RequestValidationError→422 + FileBudgetExceeded→429
       ↓
② 工具层       tools/base.py 捕获 + _recover_session + 结构化错误回填给模型
       ↓
③ AI 轮次层    executor.py _send_system_error → 给 owner 发私信（带解决链接）
       ↓
④ 后台任务层   response_worker / bootstrap / 定时任务：except Exception → 只记日志
```

四层各自职责清楚，**问题不在分层，而在三处「最后一公里」没有出口**：

1. 工具失败回到了模型手里，但没有任何机制推动模型补救 → **静默失败**（§2.2）
2. 对外动作（发消息/发文件）失败，用户不知情 → **以为发了**（§2.3）
3. 后台反复空转，没有任何人有义务叫停 → **烧 CPU 直到有人发现**（§2.4）

外加一个「看不见」：错误只有日志行，没有计数与 Top N（§2.5）。

### 2.2 缺口一：工具失败没人接手

`executor.py:1468` 的提醒链路只覆盖「返回文字但没调工具」。工具返回 `{"error": true}` 之后：

```
AI 调用 send_gm → 工具层返回 {"error": true, "message": "你不是该群成员"} → 回填 role=tool
→ 下一轮 LLM 看到 → 模型选择 end_turn（实测行为）
→ 用户看到 AI 回了别的，消息从未发出
```

平台侧没有任何一次「你刚才失败了」的推动。这是**不对称**：只说「你忘了用工具」，不说「你用了但失败了」。

### 2.3 缺口二：对外动作失败，用户不知道

`send_gm` / `send_dm` / `send_file` 是「对外动作」——失败的后果由**第三方**承担。
当前这类失败只回到模型 + 一行 `logger.error`，owner 完全不知情。

### 2.4 缺口三：后台反复空转，无人叫停

实测那次「AI 正忙 → 注入 → 重排」循环 30 秒 70,299 次：

- 每一轮都是**成功**（没抛异常）→ `failure_watch` 不记
- 每一轮都**没有进展**（同一条事件被反复重新排队）
- CPU 拉满、日志刷屏，用户只看到「AI 不说话」

`failure_watch` 的前提是「有异常」，而自激恰恰没有异常。这是**检测维度错了**。

### 2.5 缺口四：错误只在日志里

没有计数器、没有 Top N、`/health` 不反映。运维只能 `grep | wc -l`。

---

## 3. 设计

四条原则：

- **R1 每一层只做自己那层的事**，不越层兜底（工具层不改 HTTP 状态码，轮次层不改数据库）。
- **R2 吞掉必须记账**：凡是 `except` 不向上抛的，要么给模型/用户一条可读信息，要么记进计数器。
- **R3 对外动作失败，必须告知 owner**（不能只回模型）。
- **R4 止损优先于告警**：先停下（冷却/中止），再通知。

### 3.1 错误分类表（唯一来源）

新建 `backend/app/ai/error_catalog.py`：一张表把「异常/错误码」映射到「用户可读文案 + 解决入口 + 是否对外失败 + 是否可重试」。

```python
@dataclass(frozen=True)
class ErrorSpec:
    code: str                 # 稳定标识，日志与计数器用
    user_text: str            # 给用户的一句话（可带 {占位}）
    action_label: str | None  # 「去设置」按钮文案
    action_path: str | None   # 前端路由
    outward: bool = False     # 对外动作失败（失败后果由第三方承担）
    retryable: bool = False

CATALOG: dict[str, ErrorSpec] = {
    "no_api_key":            ErrorSpec("no_api_key", "还没有可用的 API 配置", "去配置", "/settings/api"),
    "insufficient_balance":  ErrorSpec("insufficient_balance", "API 余额不足", "去充值", "/settings/credits"),
    "auth_error":            ErrorSpec("auth_error", "API Key 无效", "去检查", "/settings/api"),
    "tool_outward_failed":   ErrorSpec("tool_outward_failed", "{tool} 没有成功：{detail}", None, None, outward=True),
    "tool_timeout":          ErrorSpec("tool_timeout", "{tool} 执行超时，已跳过", None, None, retryable=True),
    "session_polluted":      ErrorSpec("session_polluted", "数据状态异常，本轮已跳过", None, None),
    "quota_exceeded":        ErrorSpec("quota_exceeded", "文件配额已满", "去清理", "/settings/storage"),
    "all_failed":            ErrorSpec("all_failed", "这次没成功：{detail}", None, None, retryable=True),
}
```

`_send_system_error` 改为**只查表**，不再在函数里硬编码文案。这是「唯一来源」——新增错误类型只改这张表与它的测试。

### 3.2 工具失败：对称提醒（已实现 2026-11-25）

在 `executor.py` 的提醒链路旁加一条**工具失败提醒**，与「没调工具」对称、共用同一份文案出口：

```python
# 本轮工具结果里有 error=True 且还没提醒过 → 注入一条 user 消息（额度外）
if failed_tool_names and not _tool_fail_reminded:
    messages.append({"role": "user", "content": tool_failure_hint(failed_tool_names)})
    _tool_fail_reminded = True   # 一轮只提醒一次，避免自我打转
```

文案要求（`ai/prompts.py` 或就近常量，与 `_NO_TOOL_HINT` 放一起）：

> 你刚才调用的工具失败了：{tool}（{reason}）。如果这条内容必须让对方看到，**换一种方式重试或直接说明失败**；不要当作已发送。

只在**本轮有对外动作失败**或**重试预算内**触发，避免给「无关紧要的读工具失败」也刷一轮 token。判定：

| 工具结果 | 处理 |
|---|---|
| `error_code == TOOL_EXEC_FAILED` 且 `outward=True`（send_*） | 提醒 + owner 私信（§3.4） |
| `error_code == TOOL_EXEC_FAILED`（读类） | 提醒（一次） |
| `error_code == INVALID_ARGS` | 不提醒（模型自己知道改参数） |
| `error_code == UNKNOWN_TOOL` | 不提醒（工具表里没有，改了也没用） |

### 3.3 工具超时（详见 `docs/dev/tool_execution.md`）

**超时不是失败**：长任务到期后应转后台、完成后唤醒，而不是判 `TOOL_EXEC_FAILED`。
完整设计（分派/执行分离、后台任务、唤醒分级、阻塞检测）在 `docs/dev/tool_execution.md`。
本文只保留最小实现，等那份落地后这里改成调用它。

`tools/base.py` 的 dispatch 包一层：

```python
from app.tools.config import TOOL_TIMEOUT_SECONDS  # 默认 120，可按工具覆盖

try:
    result = await asyncio.wait_for(
        plugin.execute(db, agent_id, group_id, arguments, context),
        timeout=getattr(plugin, "timeout_seconds", TOOL_TIMEOUT_SECONDS),
    )
except asyncio.TimeoutError:
    await cls._recover_session(db, context, tool_name)   # 超时同样要收拾会话
    logger.error("工具 %s 执行超时（%ss）", tool_name, timeout)
    return _build_tool_error(ToolErrorCode.TOOL_EXEC_FAILED, "执行超时")
```

**默认值取值**：长任务（世界运行、文件处理、生图）按工具声明覆盖，不用一个全局 60s 把正常慢工具砍掉。声明方式：插件类属性 `timeout_seconds: float | None`。

⚠️ 上面这段是**临时版本**（2026-11-25 已落地）：它把超时判成失败，语义是错的——
一个只是慢的任务被说成失败，模型会重试、用户以为没做成。批次二按
`tool_execution.md` §3.3 改成「转后台 + 完成唤醒」。

超时后 `_active_runs` 的释放属于帧轮生命周期（见 §5）。

### 3.4 对外动作失败 → owner 私信（已实现 2026-11-25）

`send_gm` / `send_dm` / `send_file` 失败时，除回模型外，走 `_send_system_error` 的通道发一条给 owner：

> ⚠️ 你的 AI「{name}」没能把消息发到 {target}：{detail}
> 这轮它选择了别的做法，那条消息没有发出去。

**去重**：同一 agent 同一工具 10 分钟内只发一次（`failure_watch` 同款内存桶），否则一个坏参数会把 owner 刷屏。

### 3.5 自激循环熔断

新建 `backend/app/ai/loop_guard.py`，与 `failure_watch` 并列、内存态：

```python
@dataclass
class LoopBucket:
    count: int = 0
    first_at: float = 0.0
    cooled_until: float = 0.0

class LoopGuard:
    """按 (agent_id, kind) 记「短窗口内的重复次数」。

    与 failure_watch 的分工：那个数「连续失败」，这个数「反复发生的事件」——
    自激循环里每一轮都是成功的，只数失败永远数不到它。
    """
    def hit(self, agent_id: int, kind: str, *, now=None) -> bool:
        """记一次；超过阈值返回 True（调用方应冷却该 agent 的队列）。"""
```

接入点两处：

1. `response_worker` 从队列取任务时：`loop_guard.hit(agent_id, "turn")`，超阈值 → 该 agent 的后续任务 **冷却 30s**（丢弃并记一条 `critical`，冷却结束前同一 kind 不再处理）。
2. `_enqueue` 侧：同一 (agent_id, event_key) 在 5s 内重复入队 N 次 → 直接合并（这是源头治理，比下游熔断更省）。

**告警**：冷却触发时 → `logger.critical` + 管理员可见（有 `system` 通知就发，没有则靠 §3.7 的计数在健康检查里暴露）。

阈值与冷却可注入，单测不睡真实时间（`failure_watch` 同款）。

### 3.6 业务异常 → 4xx / 409

业务写操作的 `IntegrityError` 统一转语义化响应，而不是冒到全局兜底：

```python
# app/utils/pure/db_errors.py（纯函数，可单测）
def fk_hint(exc: IntegrityError) -> str | None:
    """从外键违例里抽出「被谁挡住的」，返回用户可读的一句；判断不出返回 None。"""
```

`routers/agents.py::delete_agent` 等写操作：

```python
try:
    result = await delete_agent_svc(...)
except IntegrityError as e:
    await db.rollback()
    hint = fk_hint(e)
    raise HTTPException(409, detail=hint) from e
```

**原则**：能说出人话的就 409 + 人话；说不出的仍走 500（不猜）。

### 3.7 错误聚合

`backend/app/services/infrastructure/error_stats.py`：内存 `Counter`，`(code, source)` 计数 + 最近一次时间 + 一条样例。定期（启动后每 60s）把 Top N 快照进内存快照区，避免逐次加锁开销。

暴露两处：

- `GET /health` 增 `errors: {"top": [{"code","source","count","last_at"}], "window": "1h"}`（窗口内）
- 管理面板「运行状态」卡片：最近 1 小时错误 Top 10（平台样式：`.card` + `Badge`）

计数点（只记「我们真正吞掉/降级」的地方，不记正常业务异常）：工具失败、会话污染、触发规则失败、后台任务失败、熔断触发。

### 3.9 插件工具错误协议（已实现 2026-11-25）

插件作者面向的报错约定单独成文：`docs/plugin-dev/tool-errors.md`。代码侧：

- `app/tools/errors.py` 的 `ToolError(code, message, retryable, outward)`
- `dispatch` 捕获后原样转给模型（`error_code` 不被改写成 EXEC_FAILED），不触发回滚
- 未捕获异常仍是 `TOOL_EXEC_FAILED`，日志带 `plugin=<插件 id>`
- 超时同样带 `plugin=`，并标 `retryable`

### 3.8 后台任务的 except 纪律

618 处 `except Exception` 不可能一次改完，也不该。落地节奏：**新代码必须带记账**，老代码只改「吞掉后无人负责」的（用 grep 找 `except Exception:\s*\n\s*pass` 与只有 `logger.debug` 的后台路径）。评审口径写进本文 §8 C2。

---

## 4. 分层分工（禁止越层）

| 层 | 可以做 | 不可以做 |
|---|---|---|
| HTTP | 映射状态码、`request_id`、脱敏 | 改业务数据 |
| 工具 | 会话恢复、结构化错误、计数 | 直接给用户发私信 |
| 轮次 | 分类→私信、重试推动、`round_end` 记账 | 改 HTTP 响应 |
| 后台 | 计数、熔断、冷却 | 静默 `pass` |

---

## 5. 与帧轮生命周期的分工

帧落地时（`docs/dev/frame_turn_lifecycle.md`）会重写 `executor` 主循环与 `_active_runs` 释放路径。两份文档的边界：

| 事项 | 归谁 |
|---|---|
| 工具超时与后台化 | `tool_execution.md`（本文 §3.3 只留临时版本） |
| 超时/失败后 `turn` 如何收尾、`_active_runs` 如何释放 | 帧文档（`turn_state`） |
| 自激循环的「无进展」判定 | 本文 §3.5（入口侧），帧文档提供 `steps_used` 作为第二个信号 |
| 轮次产出为空时是否算失败 | 帧文档 |
| 对外动作失败私信 | 本文 §3.4 |

**顺序：本文先做**——帧改造要动主循环，先让错误有出口，改主循环时才有观测手段；否则改完主循环，出问题时又只能看日志。

---

## 6. 不动的部分

反馈里「做得好的」五点，全部保留、不做重构：

1. `_recover_session` 的会话恢复
2. `request_id` 三处贯穿
3. 错误私信带解决链接 → 只是把文案搬进 §3.1 的表
4. 工具层防连坐（一个工具炸了不影响整轮）
5. 启动自检不阻塞（有争议，但这是产品决策，不在此改）

---

## 7. 批次

| 批次 | 内容 | 涉及文件 | 可独立发布 |
|---|---|---|---|
| 一 ✅ | §3.1 分类表 + §3.2 工具失败提醒 + §3.3 超时 + §3.4 对外私信 + §3.9 插件错误协议 | `ai/error_catalog.py`(新)、`tools/errors.py`(新)、`executor.py`、`tools/base.py` | 是（2026-11-25 已落地，含测试） |
| 二 | §3.5 熔断 + §3.7 聚集 + `/health` 暴露 | `ai/loop_guard.py`(新)、`response_worker.py`、`routers/system.py` | 是 |
| 三 | §3.6 业务异常转 409 + 管理面板 Top 10 | `utils/pure/db_errors.py`(新)、`routers/agents.py`、前端 `运行状态` | 是 |

批次一做完就能消掉反馈里 P0 的「静默丢失」；批次二解决「无人止损」；批次三收尾「说不清为什么不行」。

**批次一落地清单（2026-11-25）**：

- `ai/error_catalog.py`：文案唯一来源，加 `tool_outward_failed` / `tool_timeout` / `session_polluted`
- `tools/base.py`：`asyncio.wait_for` 超时（默认 120s，插件可覆盖）、`ToolError` 协议、失败日志带 `plugin=`
- `executor.py`：工具失败提醒（一个 turn 一次）、对外动作失败私信 owner
- 测试：`tests/test_error_catalog.py`(7)、`tests/test_tool_timeout.py`(4)、`tests/test_tool_errors.py`(5)

---

## 8. 不变量

- **C1** 每一处吞掉异常的 `except`，要么返回可读结果，要么进 §3.7 的计数器；二者皆无视为缺陷。
- **C2** 新代码禁止裸 `except Exception: pass`。允许 `logger.debug` 的前提是「失败不影响本次结果可观测性」。
- **C3** 对外动作（`outward=True`）失败必须到达 owner：模型一次、用户一次。
- **C4** 熔断只冷却、不删除任务；冷却结束自动恢复，恢复后第一次成功清零。
- **C5** 用户可见文案一律来自 §3.1，禁止在各调用点现拼。
- **C6** 错误码稳定：`code` 是日志/计数器/前端共用的键，改文案不改 `code`。

---

## 9. 失败形态

| # | 形态 | 现在 | 改后 |
|---|---|---|---|
| F1 | 工具失败 → 模型收工 | 静默 | 提醒一轮；对外动作另发 owner 私信 |
| F2 | 工具卡死 | 占住整轮 | 超时 → 回收会话 → 返回错误 → 提醒 |
| F3 | 后台自激 | 烧到有人发现 | 30s 冷却 + critical + Top N 可见 |
| F4 | 删 AI 撞 FK | 500（已由级联解决）；其它写操作仍可能 | 409 + 人话 |

---

## 10. 实现默认（非产品决策）

以下为实现细节，用户未逐条过目，落地时按此写，被指出再改：

1. 工具默认超时 **120s**，插件可用 `timeout_seconds` 覆盖；不设全局 60s。
2. 工具失败提醒**一轮一次**，与「没调工具」提醒共用额度外白送轮次。
3. 对外动作私信去重窗口 **10 分钟**。
4. 熔断阈值：同 `(agent, kind)` **60s 内 50 次**触发，冷却 **30s**。
5. `/health` 的错误 Top N 取 **10**，窗口 **1 小时**，只读内存快照。
