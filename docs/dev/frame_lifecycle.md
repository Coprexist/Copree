# 帧、锁与重建点（compact / clear 会不会重建什么）

> 单一事实源：`compact` / `clear` 到底动什么、不动什么，只在这里维护。
> [触发组合规则](./trigger_rules.md) 与[能力懒加载](./capability_lazy_loading.md) 只引用本表，不各自下结论。
> 范围：主站 AI（agent）的群 / 私信会话。世界 AI 的锁状态在 `worlds.config`、没有状态帧，关于"帧"的行不适用。

## 表

| 对象 | 存哪 | compact / clear 时 | 其它重建 / 归零点 |
|---|---|---|---|
| 前缀文本（system 段、昵称、提示词、记忆索引） | `capability_versions` 文本源快照（键含状态） | **换新**：该状态的 effective 对齐 latest，下一次请求用最新文本 | 只有解锁点（`apply_pending_changes`） |
| 请求里的 tools 数组 | effective 快照 ∩ 当前允许集 | **同名定义不动**（走快照）；增删工具、状态闸变化会改 | 平台发布增删工具、`thinking_enabled` / `delay_reply_allowed` 变化 |
| 账本历史 | `agent_history_entries` | **重写**成「摘要 + 事件原样搬运 + 最近 N 条」 | 无（段内只追加） |
| 事件条目（缺口 / 便签投递 / 便签撤下 / 能力变更通知 / 状态后事告知 / 空焦段告知） | 同上（账本条目） | 带 `drop_on_unlock` 的（便签投递、撤下通知、后事告知、空焦段告知）**离场**；其余**原样保留**（缺口为何保留、非压缩条目会累积，见[会话历史与前缀缓存](./conversation_history.md) §13） | 无 |
| 状态帧本身（会话帧） | `agents.state_stack`（全量存储） | **不重建**（`ensure_active_frame` 同 `context_ref` 直接 return） | 出运行集合只是改 status（`ended` / `retired`），**记录仍在**；只有 `finish_frame`（他表态后事办完）与平台代销会删记录，见「帧的存储与运行」 |
| `tool_uses` / `delivered`（触发规则状态 + 空焦段告知的投递进度） | 会话帧字段 | **归零**（解锁清单里的 `reset_trigger_state`） | 随帧状态复位：解锁归零、帧被弹出后即不在运行集合（不再参与）。**换会话不归零**，见下节 |
| 便签副本 `frame.notes` | 会话帧字段 | **清空**（`release_active_frame_notes`） | 无 |
| 环境（在哪个会话、接没接渠道、绑没绑世界） | 会话帧字段 + 帧上的锁定副本 | **换新**：锁定副本对齐现值，下一次请求用新环境 | 环境变化只置脏 + 落一条通知，不碰前缀；帧的锁定副本随帧记录一起保留（帧不再被静默裁掉） |
| 待通知脏位 | 会话帧字段 | **清空**（环境已随解锁并入前缀） | 同帧生灭 |

## 帧的存储与运行

帧由 `app/services/agent/state_stack_service.py` 管理。**存储与运行是两层**：

- **存储** = `agents.state_stack` 整个数组，全量保留（形状与帧字段不变，无需迁移）；
- **运行集合** = 数组里 `status ∈ {active, paused, suspended}` 的那串指针，"栈"只剩它。

顺序不变量：数组 = **[历史区][运行区，当前帧在末尾]**。读写各收口一次（`_get_stack` 读时归一化、
`_save` 唯一写入点），所以全仓把 `stack[-1]` 当"当前帧"用的地方不必改，也不会有漏改的半吊子。

| status | 含义 | 在运行集合 |
|---|---|---|
| `active` | 当前帧 | 是 |
| `paused` | 被压着，还要回来 | 是 |
| `suspended` | 会话切走了，等它回来 | 是 |
| `ended` | 已结束（`pop_state` / `close_state`），记录留在存储 | 否 |
| `retired` | 待交接：会话已消失、或容量超限被挑中 | 否 |

**删除点只有两处**（其余一律只改 status）：

- `finish_frame`（AI 表态"这帧的后事办完了"）——**主路径**。判据只有他能给，平台猜不出来
  （猜错就是静默丢状态）；
- 平台**代销**——绕过他表态的删除，因此必留下一条「平台代销」的账本条目（有据可查，不静默）。
  三种来由：挂起积压超 `capacity × 2`、这档 AI 不接手后事（见下）、会话已消失（群解散）。
  来由写进告知里：他得知道是自己没交接完，还是这档本来就不归他交接。

**谁办后事由档位定**：`agents.retire_handover_self`（预设 chat/immersive 关、digital_life 开，
AI 可自改）。开 = 超容量的帧挂起待交接（记录留着，办完调 `finish_frame` 销掉）；
关 = 平台直接代销（记录删掉，只留告知）。存量 AI 一律关——那正是改造前的行为
（旧实现 `stack[-MAX_STACK_DEPTH:]` 直接裁掉最旧的帧），只是现在不再静默。

容量闸：`agents.frame_capacity`（默认 31，AI 可自配）。运行集合帧数超限 → 按档位处置
**最久没被调用**的非当前帧（挂起待交接或平台代销）。"最久"的口径是 `last_active_at`，
即**最后一次调用**（LLM 调用与决策调用都记），不是创建时间——长期在用的会话帧不该因为建得早被挑走。

待交接与代销都要**告知他**，而且告知**落历史**（写一次、之后每轮命中缓存），不是每轮在尾部动态块里念：
一帧一条、按 `ref` 幂等（`handover:{帧 id}` / `drop:{时刻}`），解锁时随一次性条目离场，
帧还在就下段上下文重新提醒。文案与幂等键在 `app/utils/pure/handover.py`。

**空焦段**走同一个出口，但幂等依据不同：会话焦段里的会话全走光之后，锚在它身上的记忆在哪儿都
召不回（[焦段与记忆可达性](../memory_system/design/focus_and_memory_reach.md) §九）。
它是"当下成立的事实"而不是队列里的事件，所以投递进度记在**会话帧的 `delivered`** 里
（`emptyfocus:{焦段 id}`，与触发规则共用一份）——解锁一起归零，下段上下文条件还在就重新提醒；
焦段重新有人了就不再命中。以前它挂在每轮重拼的状态摘要里（每轮全价），现在走这一条。

**换会话不归零 = 每个会话各记各的账**：`ensure_active_frame` 把目标会话的帧从栈里取出、再压到栈顶，
帧对象连同它自己的状态原样带走，切回来还是原值。切到另一个会话读的是**那个会话的帧**——它自己没搜过
就是没搜过（照样算本帧第一次）。所以"不归零"说的是"切走再切回不会抹掉这个会话的进度"，
不是"所有会话共用一个计数"。compact/clear 同样不动别的会话的帧。

与"灭"相对的是另一种情形：换到一个**还没建过帧**的会话时，`make_state_frame` 建一个空帧，计数与投递
进度自然从零开始——这不是"归零"，是新帧本来就没有过状态。

## 环境与脏位

**环境**指「这个会话现在处于什么状况」——在哪个群 / 私信、有没有接渠道、绑没绑世界这类。它是**帧级**的：
跟着会话帧走，不挂 agent。A 群接了 QQ、B 群没接，两者必须各说各的，所以粒度只能是帧。

写进前缀的那一份（锁定副本）只在解锁点对齐：环境变化本身只**置脏 + 落一条通知**，不动前缀字节；
compact / clear 时才把锁定副本刷成现值、同事务清脏，这一步由解锁清单里的 `apply_environment` 完成。
判断与通知的三种方法（版本式 / 快照比较式 /
脏位式）见[能力懒加载](./capability_lazy_loading.md)。

## 不在本表范围

尾部读数（当前时间、世界时间）与尾部动态块（状态栈摘要、当前任务、通道规矩、好友申请、私信会话列表）
每轮重拼、不进前缀，本来就没有"锁"的问题；工具错误记录、未读兜底同理。本表只管会进前缀或挂在会话上的东西。

## 由此确定的四件事

1. `scope: frame` 的"帧"是**状态帧**：帧内投一次。重新开始计数的时机是"帧状态被复位"——解锁
   （compact/clear，`reset_trigger_state`）、或换到一个还没建过帧的会话。
   **帧对象本身不重建，重建的是它身上的触发规则状态**。
2. `session` 是**比帧更长**的生命周期（跨帧的生灭：帧被弹出、被代销、被 `finish_frame` 删掉，
   进度也得留着）——这正是能力版本进度**不放进帧对象**、而放在 holder 的两个 map 里以状态为键的原因：
   帧记录是可删的（他交接完就删），进度不能挂在一个会被删的容器上；何况世界 AI 根本没有帧对象。
   会话换出栈再换回时若按"新帧"处理，会把进度当作"新源"对齐 latest，反而使该会话前缀突变换字节。
3. "压缩后再投一次"就是这么实现的：解锁清单里有 `reset_trigger_state`（复位 `tool_uses` / `delivered`）。
   所以 web_search 的"先回复、再核实"在每段新上下文的第一搜都会重新投一次——正因如此，那句话
   **不该**再写进静态协议提示词（不搜索的 AI 不该每轮背着它）。
4. **解锁清单各步的参照点已统一为当前会话**：`rewrite_history` / `clear_note_copies` /
   `reset_trigger_state` / `apply_pending_changes` 均以当前会话为参照点。`cap_effective_versions` /
   `cap_known_versions` 的键为 `{状态}|{源}`，一个会话 compact 只换它自己的前缀字节。
   修正经过与另两个同源缺陷（通知只发一次、记忆索引正文永不更新）见
   [能力懒加载](./capability_lazy_loading.md)「已修：粒度与解锁点不一致」。

## 代码坐标

- 解锁清单与解锁动作：`app/ai/executor.py`（`UNLOCK_STEPS` / `_unlock_context`）
- 文本源与 tools 的解锁：`app/services/capability_versioning.py`（`apply_pending_changes` / `mark_effective_latest` / `get_effective_definitions`）
- 账本重写：`app/services/history/context_sync.py`（`rewrite_context`）
- 状态帧：`app/services/agent/state_stack_service.py`（`ensure_active_frame` / `release_active_frame_notes` / `load_trigger_state` / `dispose_context_frames`）
- 帧的存储/运行分界与容量闸：`app/utils/pure/state_stack.py`（`running` / `current` / `normalize_order` / `context_frames` / `_overflow` / `retire_overflow` / `drop_overflow` / `drop_retired_overflow`）
- 后事告知的文案与幂等键：`app/utils/pure/handover.py`；销帧工具：`app/tools/self_management/finish_frame.py`
- 告知落历史：`app/ai/llm.py`（`_deliver_handover` / `_deliver_focus_notices`，与便签/能力变更通知同一出口）
- 档位开关：`agents.retire_handover_self`（迁移 `0083`，预设值在 `agent_service.CONFIG_PROFILES`）
- 触发规则状态：`app/services/trigger/trigger_service.py`
