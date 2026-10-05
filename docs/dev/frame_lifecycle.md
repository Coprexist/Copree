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
| 事件条目（缺口 / 便签投递 / 便签撤下 / 能力变更通知 / 状态后事告知 / 空焦段告知） | 同上（账本条目） | 带 `drop_on_unlock` 的**投递过的才离场**（`flags.seen` 在 LLM 响应回来时打；没投出去的——比如轮次之外落的失败通知——原样搬进新账本）；其余**原样保留**（缺口为何保留、非压缩条目会累积，见[会话历史与前缀缓存](./conversation_history.md) §13） | 无 |
| 状态帧本身（会话帧） | `agents.state_stack`（全量存储） | **不重建**（一个 `context_ref` 只有一帧：`ensure_active_frame` / `push_state` 认出同会话就切回原帧，`_save` 再兜底归并） | 出运行集合只是改 status（`ended` / `retired`），**记录仍在**；只有 `finish_frame`（他表态后事办完）与平台代销会删记录，见「帧的存储与运行」 |
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

### 同一段会话至多一帧

**身份是 `context_ref`**（`group:{id}` / `dm:{session_id}` / `world:{id}` / `file:xxx`），不是帧实例 id：
一段会话的"现在"只有一个说法。`push_state` 认出运行集合里已有同 `context_ref` 的活帧就**切回那一帧**
（就地更新、**帧 id 不变**——帧 id 是闹钟之类记着的长期指针），只有真新会话才压新帧；`_save`
（唯一写入点）再兜一道归并，任何写栈的路径都过它，不必每个 push 点各自记得。

**归并规则**（`app/utils/pure/state_stack.py::merge_same_context`；字段冲突一律照此表，不另作判断）：

| 字段 | 归并时 |
|---|---|
| `id` / `status` | 幸存者的——**帧身份不变** |
| `created_at` | 幸存者的：它是「这条记录的生日」（`restore_frame` 重建时归零），不是「会话开始时间」——全仓只有容量闸排序拿它当 `last_active_at` 的兜底，闹钟/计划板按的是**帧 id**，所以取最早没有意义 |
| `retired_at` / `merged_into` | 幸存者优先（`merged_into` 只长在被归并的帧上） |
| 内容字段：`type` / `label` / `why` / `doing` / `todo` / `plan` / `journal` / `emotion` / `emotion_text` / `source_emotion` / `tools` / `skills` / `env_locked` / `env_notified` / `semantic_focus` / `group_id` | **幸存者优先**；被合并方的同名值**有意丢弃**（同一段会话的"现在"只有一个说法），幸存者为空才补 |
| `last_active_at` | 取组内最大——这帧刚被用过，不该因为时间戳旧被当成"最久没用" |
| `call_count` | 求和 |
| `tool_uses` | 逐键取大（计数不重复累加） |
| `delivered` | 键并集，值取幸存者（投过就是投过） |
| `pending_notices` / `notes` | 并集去重（还没告诉他 / 还挂在他帧上的事实，各有 id 幂等） |
| `tail` 与 `handoff` / `completed_handoff` 的 `tail` | 并集去重（一次性交接的原文尾巴不该因为归并丢掉） |
| 被归并的帧 | `status=ended`（记录留着，不删）+ `merged_into=幸存者 id`，留痕：它不是他自己结束的 |
| **未列出的字段** | 一律**幸存者优先**，被合并方的值丢弃——将来给帧加字段不必回头改这张表 |

**留谁**：当前帧优先（`active`），其次最近被激活的那帧（`last_active_at` 最大）。被归并帧只可能来自
`push_state` 之外的老数据（线上实测某 AI 的 `group:59` 攒了三帧）——下一次写栈时自愈，无需迁移。

| status | 含义 | 在运行集合 |
|---|---|---|
| `active` | 当前帧 | 是 |
| `paused` | 被压着，还要回来 | 是 |
| `suspended` | 会话切走了，等它回来 | 是 |
| `ended` | 已结束（`pop_state` / `close_state`，或同会话重复帧被归并），记录留在存储 | 否 |
| `retired` | 待交接：会话已消失、或容量超限被挑中 | 否 |

**删除点只有两处**（其余一律只改 status）：

- `finish_frame`（AI 表态"这帧的后事办完了"）——**主路径**。判据只有他能给，平台猜不出来
  （猜错就是静默丢状态）；
- 平台**代销**——绕过他表态的删除，因此必留下一条「平台代销」的账本条目（有据可查，不静默）。
  三种来由：挂起积压超 `capacity × 2`、这档 AI 不接手后事（见下）、会话已消失（群解散）。
  来由写进告知里：他得知道是自己没交接完，还是这档本来就不归他交接。

**待交接帧（retired）的归宿**：它不会自己消失，只有两条路——

1. **他交接完**：调 `finish_frame`（主路径；它只拒在跑的帧，retired 不在运行集合里，可销），销完留一条账本告知；
2. **平台代销**：挂起积压超过 `capacity` × 2 时由 `drop_retired_overflow` 删掉最旧的那批，留一条「平台代销」条目。检查发生在**下一次写栈**（`_save`）里——
   所以一个不再被写栈的 AI 会一直留着待交接帧；这是有意的：代销必留痕，不做后台静默清扫。

会话已消失（群解散）的帧也走这两条：`dispose_context_frames` 只是按档位把它放进挂起（retired）或直接代销（dropped），之后的归宿同上。

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

尾部读数（当前时间、世界时间）与尾部动态块（状态栈摘要、当前任务、私信会话列表）
每轮重拼、不进前缀，本来就没有"锁"的问题；工具错误记录、未读兜底同理。本表只管会进前缀或挂在会话上的东西。
**通道规矩**已不在这张清单里：它进锁定前缀（`channel_rules`），归上面的「环境与脏位」管（变更只落通知、
`apply_environment` 到解锁点才对齐）；**好友申请**也移出——一条申请落一条账本通知（只投一次）；
**当前任务**只在闹钟真的派了任务时才有（不再有伪造的「调用工具 X」）。

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

- 解锁清单与解锁动作：`app/services/history/context_unlock.py`（`UNLOCK_STEPS` / `unlock_context`；`app/ai/executor.py` 只转发）
- 文本源与 tools 的解锁：`app/services/capability_versioning.py`（`apply_pending_changes` / `mark_effective_latest` / `get_effective_definitions`）
- 账本重写：`app/services/history/context_sync.py`（`rewrite_context`）
- 状态帧：`app/services/agent/state_stack_service.py`（`ensure_active_frame` / `release_active_frame_notes` / `load_trigger_state` / `dispose_context_frames`）
- 帧的存储/运行分界、容量闸与同会话归并：`app/utils/pure/state_stack.py`（`running` / `current` / `normalize_order` / `context_frames` / `merge_same_context` / `_overflow` / `retire_overflow` / `drop_overflow` / `drop_retired_overflow`）
- 后事告知的文案与幂等键：`app/utils/pure/handover.py`；销帧工具：`app/tools/self_management/finish_frame.py`
- 告知落历史：`app/ai/llm.py`（`_deliver_handover` / `_deliver_focus_notices`，与便签/能力变更通知同一出口）
- 档位开关：`agents.retire_handover_self`（迁移 `0083`，预设值在 `agent_service.CONFIG_PROFILES`）
- 触发规则状态：`app/services/trigger/trigger_service.py`
