# 工具执行：分派、超时、后台化与唤醒

**状态**：设计稿（未实现）。2026-11-25 由外部反馈（`error-handling-2026-11-25.md`）的「工具卡死整个项目」一节展开；参考 Dify / Home Assistant / MCP / DSH 的执行层做法。
**范围**：工具从「AI 调用」到「结果回到 AI」的整条路径——包括内置工具、插件工具、世界技能。不含 HTTP 层与前端。
**取代关系**：`docs/dev/error_handling.md` §3.3（工具超时）改为引用本文；本文是那一条的完整设计。

---

## 0. 一句话

**执行与分派分开：分派器只决定「谁跑、跑哪个会话」，执行器跑完另收结果——超时不是失败，是转后台。**

---

## 1. 问题

### 1.1 现状：分派与执行混在一起

`ai_response_worker` 是**串行消费**：

```python
while True:
    event = await message_queue.get()
    await _process_event(db, event)      # 整轮 LLM 工具循环都在这里
```

| 通道 | 现在的执行方式 | 卡住的后果 |
|---|---|---|
| 群聊 | `asyncio.create_task(_trigger_one(...))` | 占该群并发名额（默认 2），两次就全群哑 |
| 私信 | `await _trigger_dm_ai_reply(...)` | **整个事件队列停摆** |
| 闹钟 / 触发器 | `await _process_alarm_event(...)` | **整个事件队列停摆** |

外部反馈说「工具有问题会卡死整个项目」——属实，而且触发点在私信/闹钟这类串行通道。

### 1.2 两种卡法，只有一种能被超时救

| 卡法 | 例子 | `asyncio.wait_for` |
|---|---|---|
| 异步挂起 | 网络请求没设超时、死循环里 `await` | ✅ 能救：定时器还在跑，到点取消 |
| **同步阻塞** | `requests.get`、`time.sleep`、`subprocess.run` | ❌ 救不了：事件循环被占，定时器排不上队 |

插件工具与内置工具在这一点上**完全一样**：同一个 `dispatch`、同一个事件循环。区别只在于插件是外部作者写的，同步阻塞的概率高得多。

### 1.3 超时 = 失败，是个错误语义

当前把它变成 `TOOL_EXEC_FAILED`。于是：

- 一个只是**慢**的任务被说成「失败」
- 模型按失败处理，可能重试、可能换参数——但任务其实还在跑，可能已经产生了副作用
- 用户被告知「没做成」，实际上做成了

---

## 2. 参考：四种现成做法

| 项目 | 做法 | 代价 | 对我们 |
|---|---|---|---|
| [Dify](https://github.com/langgenius/dify-plugin-daemon) | 插件永远不在主进程：daemon 起子进程（STDIN/STDOUT）、debug 走 TCP、serverless 走 HTTP | 自建守护进程 + 协议 + 生命周期 | 长期方向，1.x |
| [Home Assistant](https://developers.home-assistant.io/docs/asyncio_blocking_operations/) | 同进程，但 2024.7 起**自动检测**事件循环阻塞，硬性要求 `run_in_executor` | 只有检测与规范，无隔离 | **1.0 就抄这个** |
| [MCP](https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/cancellation) | 工具跨进程，请求 ID + `notifications/cancelled` | 需要协议层 | 长期参考 |
| DSH 的 `bash` / `run_code` | 子进程执行；**超时转后台 job，不 kill**；结果另取 | 无（宿主自带的执行器） | **语义直接照搬** |

第 4 行最关键：DSH 的语义是「到期后命令移到后台成为一个 job」，而不是杀掉了事。这正是我们要的超时语义——**超时不是失败的判定，只是「不再等」的判定**。

---

## 3. 目标模型

### 3.1 三层

```
① 分派器（dispatcher）   取事件 → 鉴权 → 准入（帧 / 并发 / 容量）→ create_task
        ↓
② 执行器（runner）       跑一轮（LLM + 工具）；不被 worker 主循环 await
        ↓
③ 结果回灌（delivery）   说话 / 工具结果 / 任务完成 → 注入当前轮 或 投递新事件
```

分派器**不做任何等待**：取一个事件、算出「谁在哪跑」、扔给执行器，立刻回来取下一个。这样任何一条通道卡住都只卡自己那一轮。

**这是根治，不是打补丁**：有了这一层，超时只是执行器内部的一个策略，不是保命的唯一手段。

### 3.2 工具的三类

| 类 | 判定 | 执行 |
|---|---|---|
| 快工具 | 声明 `timeout_seconds <= 快阈值`（默认 30s） | 同步 `await`，正常返回 |
| 长工具 | 声明 `background = True`（世界运行、生图、大文件、外部爬取） | 提交后台任务，立即返回 `{task_id, status: "running"}` |
| 隔离工具 | 声明 `isolated = True`（世界技能走沙箱） | 子进程/沙箱，与主进程无共享 |

**不允许**「所有工具都能变长」这种模糊判定：声明是显式的，测试可以直接断言。

### 3.3 超时即转后台

```python
try:
    result = await asyncio.wait_for(plugin.execute(...), timeout=tool_timeout)
except asyncio.TimeoutError:
    # 不取消、不判失败：转成后台任务，结果完成后再回灌
    task_id = await background_tasks.spawn(
        agent_id=agent_id, tool_name=tool_name, arguments=arguments,
        context_ref=context_ref(conversation_type, group_id, session_id),
    )
    return {"success": False, "status": "running", "task_id": task_id,
            "message": f"{tool_name} 还没完成，已转为后台任务（{task_id}）；结果会回来。"}
```

三个细节：

1. **原协程不许直接丢弃**。`wait_for` 超时会 cancel 掉它——那就不是"转后台"而是"杀掉"。正确做法是**一开始就跑在独立 task 里**（`asyncio.shield` 或先 `create_task` 再等它），超时只是「我先不等了」。

   ```python
   job = asyncio.create_task(plugin.execute(...))
   done, _ = await asyncio.wait({job}, timeout=tool_timeout)
   if not done:
       background_tasks.attach(job, ...)   # 任务继续，只是不再等
       return {"status": "running", "task_id": ...}
   ```

2. **后台任务要限流**。不受控地开 task 等于把 CPU 打满。走同一个 `capacity.admission`。

3. **记账发生在后台任务结束时**，不在超时那一刻——否则出现「记了一次失败，后来又成功」的双重事实。

### 3.4 完成之后：唤醒由 AI 自己决定

**平台不猜意图，让 AI 声明。** 这是本节的核心决定，理由：

- 只有 AI 知道这个结果还需不需要它。平台按「turn 活没活着」猜，两种猜错都糟：该叫不叫（结果白跑）、不该叫乱叫（白烧一轮）
- 计费立刻合理：AI 自己说「叫醒我」= 它自己的意志，与闹钟同性质；平台替你叫醒才是平台行为
- 与 `end_turn` 的结算语义（`keep_thinking` / `key_note`）同构——都是轮末由它自己定

#### 3.4.1 一个工具：`wait_task`

超时后工具结果是 `{status: "running", task_id}`，同时给 AI 一句提示，它可以调：

```python
wait_task(task_id: str, wait_seconds: int = 0, wake: bool = False, note: str = "")
```

| 字段 | 语义 |
|---|---|
| `wait_seconds` | 再等多久（0 = 不等了）。等到了就直接把结果返回，不开新一轮 |
| `wake` | 没等到时，完成后要不要叫醒我 |
| `note` | 「我为什么在等它」。**必填**，唤醒时原样带回——否则半夜醒来看见「task_123 完成」想不起自己在等什么 |

#### 3.4.2 默认不唤醒

| 策略 | 触发 | 行为 |
|---|---|---|
| `notify`（**默认**） | AI 没声明 | 写进账本 + 轮末记一条「有个任务还在跑：task_x」，下一轮它自己看得到。**不唤醒、不计费** |
| `wake` | AI 显式 `wait_task(wake=True)` | 投递 `task_done` 事件 → 分派器 → 新的一轮，带结果与 note |
| `drop` | 工具声明「纯副作用」 | 只记账 |

默认必须是 `notify`：没声明就不该花这笔钱，也避免模型不管什么任务都随手 `wake=True`。

#### 3.4.3 唤醒的三条纪律

1. **走同一个分派器与准入**，不许绕过——否则十个任务同时完成会把并发名额打爆
2. **带帧上下文**：唤醒时恢复任务发起时的帧（与闹钟同一套 `restore_frame`）
3. **幂等 + 限频**：任务表有 `notified_at`，重复唤醒只认第一次；同一 AI 每小时任务唤醒上限（默认 5 次），防自激

#### 3.4.4 与闹钟的统一

闹钟是「时间驱动」，`wait_task` 是「事件驱动」，两者是同一个抽象的两种输入：

```
set_alarm(at=…)        → 到点唤醒
wait_task(until=…)     → 任务完成唤醒
```

共用四件套：投递事件、恢复帧、容量准入、幂等。但**不共用闹钟表**——闹钟是用户可见的列表，后台任务不该出现在那里。

#### 3.4.5 投递：三条通道仓库里都已经有了

「结果一定要给 AI 看见」不需要新造机制：

| 场景 | 通道 | 持久化 | 行为 |
|---|---|---|---|
| 当前轮还活着 | `_pending_interrupts` + `add_pending_interrupt` | ❌ 内存，上限 20，重启丢失 | 每轮 LLM 调用前按会话 `drain_pending_interrupts`，即时可见 |
| 当前轮已结束，等下次唤醒 | 账本：`experience_service.record` → `agent_experiences`，投递走 `deliver_recalled_experiences` / `deliver_switch_experiences` | ✅ | 按 `context_ref` 分场景，去重靠 `exp:<id>` |
| 要主动唤醒 | `task_done` 事件 → 分派器 | ✅（任务表） | 走同一份准入 |

**唯一的缺口**：账本投递现在是**条件命中**——常规轮按最近消息的关键词找，切场景时补上一场。任务结果不能靠关键词运气，需要给条目加一个「必投」标记（`always: true`），该 `context_ref` 的下一次轮次无条件带上。

#### 3.4.6 普通消息与任务结果：同样是「插入下一步」，可靠性不同

两者都走 `_pending_interrupts`（`add_pending_interrupt` → 下一轮 LLM 调用前 `drain`），但**兜底不同**：

| | 普通消息 | 任务结果 |
|---|---|---|
| 原件在哪 | `group_messages` / `dm_messages`（持久化） | 任务表（持久化） |
| 内存缓冲丢了会怎样 | 内容不丢，下一轮从历史里读到，只是晚一轮 | **不会自动重建**——任务表不会主动进上下文 |
| 所以投递要求 | 内存注入即可（按 `conversation_of` 分会话） | **必须双写**：内存注入 + 账本 `always` 条目 |

`_pending_interrupts` 是纯内存（上限 20，重启即丢），这条限制对普通消息无害、对任务结果有害。所以任务完成的投递顺序是：

```
任务完成
  ├─ 1. 任务表：结果落库 + finished_at
  ├─ 2. 当前轮还活着 → add_pending_interrupt（提前量，丢了不影响正确性）
  └─ 3. 无论当前轮在不在 → 账本写一条 always 条目（保证下次一定看得见）
```

**轮末的 `_seal` 不管这件事**：`_seal` 只认领 `_unsent_text`（AI 自己说过没发出去的）、`key_note`、`tool_ledger`，不包含注入的消息。任务结果必须走第 3 步，不能指望封存兜底。

**防重复**：两个通道共用一个 `delivered_at`（在任务表上）。账本条目被注入后即标已投递；内存那条被 `drain` 时同样标记。任一通道先到，另一条就不再投。

#### 3.4.7 降级必须可解释（已实现，2026-11-25）

丢东西可以，但**不能让它不知道**。三处降级各自要说清楚：

| 降级 | 是否告知 | 手段 | 怎么看 |
|---|---|---|---|
| 群消息超出窗口 | ✅ 已有 | `context_sync` 在批次前插一条 `gap` | `read_conversation` 往回翻 |
| 上下文压缩 | ✅ 已补 | 压缩提示带段号：`…已折叠（要看原文调用 expand_context(segment_id="a1b2c3d4")）` | `expand_context`（本次实现） |
| 注入缓冲溢出（>20 条） | ✅ 已补 | `drain` 时附一条 `notice`：「积压太多，有 N 条没送进来…用 read_conversation 往回翻」 | `read_conversation` |

**原则：告知存在 + 给索引，不重放内容。** 把原文重新塞回上下文等于让压缩白做——`expand_context` 的定位就是「按需回看」，不是「补回上下文」。

配套：`context_segments` 表存被折叠的原文（保留 7 天，启动时清理一次）。压缩函数只负责在提示里写段号，落库与清理在 `context_segment_service`。

> 历史教训：曾经那行提示写的是「用 expand_context 工具可展开」，但全仓只有那一句文案、没有工具——AI 被指引去撞 `UNKNOWN_TOOL`。**说了做不到，比不说更糟。**

### 3.5 同步阻塞：检测，不是隔离

同步阻塞无法被 `wait_for` 救，也无法靠 create_task 救——事件循环被占时什么都不跑。唯一能做的是**让它在开发期暴露**：

- **事件循环延迟监控**：每 100ms 一个心跳，延迟 > 1s 就 `logger.critical`，并带上「当前正在执行哪个工具/插件」（dispatch 进入时写一个 `_current_tool` 上下文变量）
- **`asyncio` debug 模式**：开发期开启，能报出慢回调
- **插件规范**：`docs/plugin-dev/` 加一页，明确「同步 IO 必须 `await asyncio.to_thread(...)`」，并给出 `requests` / `openai` 同步 client / `subprocess` 的正确写法
- **插件自检脚本**（`scripts/selfcheck_plugin.py`）：静态扫一遍 `requests.|time.sleep|subprocess.run|open(` 并提示

这四条不追求「拦住」，只追求「**上线前就知道**」。真正的隔离（子进程）留给 1.x。

---

## 4. 插件工具错误协议

当前插件作者只有 `raise`（语义丢失）或 `return {"error": True}`（无 code）。补一套协议：

```python
# app/tools/errors.py
class ToolError(Exception):
    """插件/工具作者主动抛的业务错误：平台会原样转成结构化结果给模型。"""
    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 outward: bool = False):
        ...
```

| 作者写法 | 模型看到 | 用户看到 |
|---|---|---|
| `raise ToolError(INVALID_ARGS, "缺少 group_id")` | `{error, error_code: INVALID_ARGS, message}` | 无 |
| `raise ToolError(TOOL_RATE_LIMITED, ..., retryable=True)` | 同上 + `retryable` | 无 |
| `raise ValueError(...)`（未捕获） | `TOOL_EXEC_FAILED` | 无 |
| 对外动作失败 | `TOOL_EXEC_FAILED` + 提醒 | owner 私信（`error_handling.md` §3.4） |

日志侧：`dispatch` 的失败日志必须带 **插件 id**（`plugin.owner`），否则插件作者无从定位。

---

## 5. 与帧、闹钟、账本的关系

| 概念 | 关系 |
|---|---|
| 帧 | 后台任务的 `context_ref` 就是帧的会话键；唤醒要恢复帧（与闹钟同一套） |
| 闹钟 | 共用「投递事件 + 恢复帧 + 准入」三件套，但**不是同一张表**：闹钟是用户可见的定时任务，后台任务不进闹钟列表 |
| 账本 | 任务完成后写一条 `tool` 条目（成功/失败/超时转后台各一条），下一轮 AI 自己看得到 |
| `last_task` | 后台任务应更新 `last_task`，否则 AI「忘了自己还在等什么」 |

---

## 6. 迁移路径

| 批次 | 内容 | 风险 |
|---|---|---|
| 一 | 分派/执行分离：`_process_dm_event` / `_process_alarm_event` 改为 `create_task` + 准入；`_run_serialized` 只保留「同 AI 同会话串行」语义 | 中：改动在热路径，需要并发测试 |
| 二 | 工具分类（`timeout_seconds` / `background` / `isolated`）+ 超时转后台 + `background_tasks` 表 | 低 |
| 三 | 完成唤醒（`wake/notify/drop` + 恢复帧 + 幂等） | 中 |
| 四 | 阻塞检测（loop lag + `_current_tool` 上下文）+ 插件规范与自检脚本 | 低 |
| 五 | 插件错误协议（`ToolError` + 日志带插件 id） | 低 |

**顺序理由**：先分离分派/执行（批次一），否则后面所有超时与唤醒都建在「worker 会被卡住」的沙地上。

---

## 7. 不变量

- **C1** 分派器不许 `await` 执行——只允许 `create_task`（或把协程交给准入队列）。
- **C2** 超时永不产生「失败」事实：只有「还在跑」或「完成（成功/失败）」两种终态。
- **C3** 后台任务受与前台同一份容量准入约束。
- **C4** 唤醒必须恢复发起时的帧上下文，并带上 AI 自己写的 `note`。
- **C7** 未声明的等待一律 `notify`：平台不替 AI 决定「要不要叫醒它」。
- **C5** 任何工具的失败日志都带 `plugin` 归属（内置工具记 `internal`）。
- **C6** 未捕获异常一律 `TOOL_EXEC_FAILED`，不改写为别的 code。

---

## 8. 失败形态

| # | 形态 | 现在 | 改后 |
|---|---|---|---|
| F1 | 私信里工具异步挂起 | 全站停摆 | 超时转后台，轮次结束，完成后唤醒 |
| F2 | 插件里同步 `requests` | 全站停摆且无提示 | 检测器在开发期报出；线上 loop lag 告警 |
| F3 | 长任务（生图 3 分钟） | 超时被当失败、模型重试 | 直接后台化，不占轮次 |
| F4 | 任务完成时 AI 已走 | 结果丢失 | 降级 `notify`，写账本 |
| F5 | 十个任务同时完成 | — | 唤醒走准入，排队而非打爆 |

---

## 9. 实现默认（非产品决策）

1. 快工具阈值 **30s**，全局兜底 **120s**，插件可 `timeout_seconds` 覆盖。
2. 超时转后台默认 `notify`（不唤醒）；只有 AI 显式 `wait_task(wake=True)` 才唤醒。
2b. `wait_task` 的 `note` 必填；唤醒事件必须带 note 与 task_id。
3. 后台任务表 `background_tasks`：`id, agent_id, tool_name, context_ref, status, result, notified_at, created_at, finished_at`。
4. loop lag 阈值 **1s**，采样 **100ms**。
5. 唤醒事件复用现有队列与容量准入，不新建第二套队列。
