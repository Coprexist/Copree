# 帧、锁与重建点（compact / clear 会不会重建什么）

> 单一事实源：`compact` / `clear` 到底动什么、不动什么，只在这里维护。
> [触发组合规则](./trigger_rules.md) 与[能力懒加载](./capability_lazy_loading.md) 只引用本表，不各自下结论。
> 范围：主站 AI（agent）的群 / 私信会话。世界 AI 的锁状态在 `worlds.config`、没有状态帧，关于"帧"的行不适用。

## 表

| 对象 | 存哪 | compact / clear 时 | 其它重建 / 归零点 |
|---|---|---|---|
| 前缀文本（system 段、昵称、提示词） | `capability_versions` 文本源快照 | **换新**：effective 对齐 latest，下一次请求用最新文本 | 只有解锁点（`apply_pending_changes`） |
| 请求里的 tools 数组 | effective 快照 ∩ 当前允许集 | **同名定义不动**（走快照）；增删工具、状态闸变化会改 | 平台发布增删工具、`thinking_enabled` / `delay_reply_allowed` 变化 |
| 账本历史 | `agent_history_entries` | **重写**成「摘要 + 事件原样搬运 + 最近 N 条」 | 无（段内只追加） |
| 事件条目（缺口 / 便签投递 / 便签撤下 / 能力变更通知） | 同上（账本条目） | 带 `drop_on_unlock` 的（便签投递、撤下通知）**离场**；其余**原样保留**（缺口为何保留、非压缩条目会累积，见[会话历史与前缀缓存](./conversation_history.md) §13） | 无 |
| 状态帧本身（会话帧） | `agents.state_stack` 栈顶帧 | **不重建**（`ensure_active_frame` 同 `context_ref` 直接 return） | 被 `pop_state` / `close_state` 弹出即离栈；栈超 `MAX_STACK_DEPTH` 时从栈底裁掉 |
| `tool_uses` / `delivered`（触发规则状态） | 会话帧字段 | **归零**（解锁清单里的 `reset_trigger_state`） | 随帧的 dict 生灭：帧被弹出 / 被裁掉也归零。**换会话不归零**，见下节 |
| 便签副本 `frame.notes` | 会话帧字段 | **清空**（`release_active_frame_notes`） | 无 |

## 帧的生灭

帧由 `app/services/agent/state_stack_service.py` 管理。只有两条路会让一个会话帧连同它的
`tool_uses` / `delivered` 一起消失：

- `pop_state` / `close_state`（AI 自己的工具）：弹出指定帧或栈顶帧，帧对象离栈；
- 栈超 `MAX_STACK_DEPTH`：`ensure_active_frame` 只留最近 N 层，从栈底裁。

**换会话不归零 = 每个会话各记各的账**：`ensure_active_frame` 把目标会话的帧从栈里取出、再压到栈顶，
帧对象连同它自己的状态原样带走，切回来还是原值。切到另一个会话读的是**那个会话的帧**——它自己没搜过
就是没搜过（照样算本帧第一次）。所以"不归零"说的是"切走再切回不会抹掉这个会话的进度"，
不是"所有会话共用一个计数"。compact/clear 同样不动别的会话的帧。

与"灭"相对的是另一种情形：换到一个**还没建过帧**的会话时，`make_state_frame` 建一个空帧，计数与投递
进度自然从零开始——这不是"归零"，是新帧本来就没有过状态。

## 不在本表范围

尾部读数（当前时间、世界时间）与尾部动态块（状态栈摘要、当前任务、通道规矩、好友申请、私信会话列表）
每轮重拼、不进前缀，本来就没有"锁"的问题；工具错误记录、未读兜底同理。本表只管会进前缀或挂在会话上的东西。

## 由此确定的三件事

1. `scope: frame` 的"帧"是**状态帧**：帧内投一次。重新开始计数的时机是"帧状态被复位"——解锁
   （compact/clear，`reset_trigger_state`）、帧被弹出/裁掉、或换到一个还没建过帧的会话。
   **帧对象本身不重建，重建的是它身上的触发规则状态**。
2. `session` 是**比帧更长**的生命周期（跨帧的生灭：帧被 pop/close、被栈裁掉，进度也得留着），故意未实现：
   要它得先把投递进度挂到帧以外的地方。`version` 同理。
3. "压缩后再投一次"就是这么实现的：解锁清单里有 `reset_trigger_state`（复位 `tool_uses` / `delivered`）。
   所以 web_search 的"先回复、再核实"在每段新上下文的第一搜都会重新投一次——正因如此，那句话
   **不该**再写进静态协议提示词（不搜索的 AI 不该每轮背着它）。

## 代码坐标

- 解锁清单与解锁动作：`app/ai/executor.py`（`UNLOCK_STEPS` / `_unlock_context`）
- 文本源与 tools 的解锁：`app/services/capability_versioning.py`（`apply_pending_changes` / `mark_effective_latest` / `get_effective_definitions`）
- 账本重写：`app/services/history/context_sync.py`（`rewrite_context`）
- 状态帧：`app/services/agent/state_stack_service.py`（`ensure_active_frame` / `release_active_frame_notes` / `load_trigger_state`）
- 触发规则状态：`app/services/trigger/trigger_service.py`
