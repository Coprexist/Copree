# 计划与闹钟：绑状态的节奏

> **状态**：设计定稿，待实施（记忆注入那一半已落地，见 [焦段与记忆适用范围](../memory_system/design/focus_and_memory_reach.md) §8）
> **单一来源**：计划/闹钟怎么进上下文、怎么绑状态、怎么唤醒，只在这里维护。
> 相关：[帧与重建点](./frame_lifecycle.md)、[会话历史与上下文纪律](./conversation_history.md)、[跨状态交接](./cross_state_context.md)、[能力懒加载](./capability_lazy_loading.md)

## 一、要解决的问题

- **计划从来没有进过 AI 的视野**：`agent_workspace.plan` / `todo`（AI 的规划与待办）在任何一条提示词路径里都不注入，
  正常回合只注 `current_task`（`get_current_task_text`），AI 只能自己想起来调 `check_workspace` 才看得到。
- **`force_alarm_on_end` 是死开关**：模型、schema、三档预设（chat/immersive=False、digital_life=True）、
  `merge_preset_values` 的强布尔名单、自改工具 `update_self_config`、`get_effective_config` 的返回全都有它，
  但整个 backend 里没有一处读它来做事——「对话结束强制闹钟」从未生效过。
- **闹钟本身是通的**：`agent_alarms`（wake_at/task/status）、`alarm_scheduler`、决策层 `event_type=alarm`（优先级 85）、
  唤醒链路 `_process_alarm_event`、四个工具（set/list/update/cancel）都在。缺的只是「把这列表摆到 AI 眼前」。
- **要绑状态**：计划到点是在**某个状态里**执行的（唤醒时构建的就是那个状态的请求体），所以归属必须落在状态上，
  否则唤醒出来的上下文和计划说的不是一回事。

## 二、实测事实（决定了实现方式，别推翻）

| 事实 | 数据 | 含义 |
|------|------|------|
| 状态栈是 `agents.state_stack` 一列 JSON，同时装两类帧 | 全站 39 帧**全是情景帧**（dm/group_chat），任务帧 0 个；`paused`/`completed`/`closed` 一个都没有 | `push_state`/`close_state` 这套从上线到现在没被真正用过 |
| **帧 id 会变** | 摘要结尾写着「完成后调用 pop_state」；实测某个 AI 每轮 `end_turn` 前都 `pop_state` → 栈清空 → 下次触发 `ensure_active_frame` 重建新帧（新 uuid） | 绑 `frame_id` 必须配离栈规则，不能假设它是长期标识 |
| 栈深上限 10 | `MAX_STACK_DEPTH`；`ensure_active_frame` 超了从**栈底裁**，`push_state` 满了**拒绝** | 本机最深 5 层，裁剪从未触发；触发条件是「同一 AI 被 11 个以上不同会话触发过」 |
| `agent_alarms` 是**可变行** | `update_alarm` 就地改 wake_at/task，cancel/fire 改 status | 没有版本列、没有事件表：所以「变没变」只能投递时比渲染文本现算（§3.2），而不是查版本号 |
| 前缀缓存的命中边界 | 修前：命中恒等于「工具定义 + system 段」约 17k token，某 AI 从 190 条涨到 235 条 `cached_tokens` 死钉在 17,280。修后（同一 AI 同一群）：233 / 235 / 239 条消息时 `cached_tokens` 27,264 / 27,520 / 27,648，占 prompt 94%~97%；相邻两次请求首个不同下标 225/227（总长 233/239），`messages[0]` 4894 字符逐字节相同 | 整段历史现在都进缓存。规矩：**开头每个字节只许随「配置/能力版本」变**，随对话/时间变的一律去尾部读数或账本条目 |
| 状态摘要本身是字节稳定的 | 同一帧 8 连轮 243 字节完全一致，且位置在历史**之后** | 它不需要改；计划和它同区即可 |

## 三、设计

### 3.1 一句话

计划与闹钟共用一份「节奏」，按**状态帧**归属，用**账本条目**进上下文（进一次 + 只补改变量），到点时在它所属的状态里唤醒。

### 3.2 进上下文的机制：一条按会话归属的账本条目

- 条目 `kind=plan`，位置**紧挨着当轮新消息之前**（构建请求时先投计划、再 `sync_*_history` 落当轮消息）。
  落账本而不是拼在历史之前——账本只追加，位置定下就固定，前缀字节从头到尾一致。
- 键是**会话**：`ref = plan:<context_ref>`。不用帧 id 当键——实测帧 id 每轮都可能重建（AI 自己 `pop_state`），
  拿它当键会让计划板每轮都被当成"没见过"重投一遍；会话轴稳定，正好是「这个状态下次被触发」的那个粒度。
  没有内容指纹、没有 alarm id、不需要新列。
- **变化判据是渲染文本本身**：账本里该会话最后一条 `plan` 条目的正文就是"上次投出去的是什么"，
  这次渲染的板子和它逐字节比——不同才追加一条。首投 `【计划】`，变了 `【计划更新】以这份为准`。
  板子里只出现绝对时间（本地时区），不出现"还有 N 分钟"这类相对量，所以"没变"就是真的没变。
- AI 主动调 `list_alarms` / `check_workspace` 看到的就是完整列表；比文本的机制下不需要额外「标记已投」——
  板子没变就不投，变了才投（那点重复的内容它刚从工具结果里看过，无害）。
- 条目带 `drop_on_unlock`（compact 后重投一轮）并进 `NEVER_COMPRESSIBLE`（与 `handoff` 同款）。

### 3.3 渲染口径（四栏，一起出现）

1. **本会话的计划**：`origin_context_ref == 当前会话` 且未到点，按 `wake_at` 升序列出。
2. **本会话排给别处的**：`origin_context_ref == 当前会话`、目标帧不是当前帧、未到点——写清到点会在哪个状态里叫醒它。
3. **其他会话的计划数目**：只报数，带目标帧的 type / doing 当标签。
4. **已执行的**：`status=fired` 的留一行 `✅ 已执行`，只留最近几条——这就是「下次这个状态被触发时更新显示一下」，
   下一次任何变化带来的重渲染会自然带上它，然后随时间淡出。
5. **没记归属的（迁移前排的老闹钟）**：两列都空的行不能猜成"这个会话的"，单独报数并把 id 给出来
   （`另有 N 条早先排的闹钟没记归属：#1、#2`），让 AI 自己用 `list_alarms` 看全量后决定去留。

语义焦段只作为**标签**（排计划时快照当时的 `semantic_focus`，显示用），不参与归属——
焦段是记忆的适用范围，而且它的锚点还没参与召回，拿它做归属既绕又不可靠。

### 3.4 唤醒与状态

唤醒侧保持薄：**说清「你被哪条闹钟唤醒、当初要做什么」+ 把状态恢复回去**，不做别的。
- 恢复按 `alarm.frame_id`（该唤醒谁）：帧还在栈里 → 回跳到它；已离栈 → 按 `origin_context_ref` 重建同型帧压栈，再跑任务。
  这样「叫醒的是那个状态下的他」，任务描述里的上下文才成立。
- 归属只记两个事实，都在闹钟行上（单一来源）：`origin_context_ref`（谁拉起的，口径就是账本会话键：群 `group:{id}`、
  私信 `session_id`）、`frame_id`（该唤醒谁）。
  **「该通知谁」不落列**——它是这两者推出来的两类读者（本会话的、排给别处的），存一份就是第二份真相。
- 帧离栈（`pop_state` / `close_state` / 栈底裁剪）时**不静默删计划**：记一条「状态 X 已关闭，名下还有 N 条没到点」，
  让 AI 下次醒着时自己改派或取消。静默删等于丢东西。

### 3.5 开关

- `agents.plan_injection_enabled`（**普通布尔列**，不加进 `context_config`：那份 JSON 现在前后端都没有 API 和 UI，塞进去等于把开关藏起来）。
- 进 `utils/pure/presets.py` 的 `_STRONG_BOOL_PARAMS`；`CONFIG_PROFILES` 三档默认：chat 关 / immersive 关 / digital_life 开。
- **细档也要给默认值**：细档（`chat_low_power` / `immersive_roleplay` / `digital_thinker` …）是**前端概念**，
  后端只认三档 `config_profile`，所以默认值要同时写进 `frontend/src/components/agent-create/presets.ts` 的 `SUB_OPTIONS[i].params`。
- UI：`AgentSettingsModal.tsx` 加一格、`CreateAgentModal.tsx` 同步；三语文案加在 `frontend/src/i18n/translations.ts` 的 `preset.*` 附近。
- AI 自改：`tools/self_config/update_self_config.py` 参数表加一项（与 `force_alarm_on_end` 并列）。

### 3.6 变更「什么时候发现」

不插桩、不建表：写入侧完全不动，判据在投递时算——**比渲染文本**（§3.2）。
代价：一条计划改了、而它所属的会话当时不活跃，要等那个会话下次被触发才看到。这是可接受的：
计划板本来就是「这个状态下次醒来时看到的东西」，不是实时推送。

（早先的版本想用「内容指纹 + alarm id 进 ref」回答"上次投的是哪一版"。比文本把这一层整个省掉：
账本条目自己就是上次的渲染结果，跟它比逐字节比算指纹更直接，也不用维护哈希。）

### 3.7 「每次触发都叫他规划」

- **先做软的**：注入块末尾一句提醒（核对计划是否仍成立、按外界环境自主调整、有新事就排上）。不额外花一轮。
- **硬的**（本轮没写计划就补一轮收尾要求）留给 `force_alarm_on_end` 复活，单独决定：它会改变存量数字生命档 AI 的行为与花费，
  建议存量保持关、新 AI 按预设，避免升级即涨价。

## 四、明确不做

- 不解析 `plan` markdown 里的时间自动建闹钟——自然语言解析太脆，排点让 AI 用 `set_alarm` 自己排。
- 不把闹钟**运行时**插件化：`AgentAlarm` 模型、`alarm_scheduler`（`bootstrap` 里 spawn）、决策层 `event_type=alarm`、
  `response_worker` 的 alarm 分支、`self_management` 工具段都在核心，插件的 `category`（skin/skill/world/service）装不下，硬塞会断唤醒链路。
  但**注入层**要做成可拔插的注入源（见 ⑤）——那才是「其他开发者也能加一份注入」的正确接缝。
- 不用焦段做计划归属；不给记忆做淡出通知（理由同：只是「不再自动想起」，让 AI 去忘反而制造噪音）。
- 不为计划新增表。

## 五、实施步骤（照这个顺序做）

1. **迁移 0078**（当前 head `0077_chat_list_indexes.py`）：`agent_alarms` 加 `frame_id`(String(12), 可空) 与 `origin_context_ref`(String(64), 可空)；
   `agents` 加 `plan_injection_enabled`(Boolean, default false)。帧 id 是字符串（帧活在 `agents.state_stack` 的 JSON 里），不是外键。
2. **`backend/app/utils/pure/plan_entry.py`**：`plan_ref(context_ref)` + 计划板渲染（本会话 / 排给别处 / 其他计数 / 已执行）
   + 条目构造（首投与「以这份为准」两种头）。纯函数、无 IO、无指纹。
3. **`backend/app/services/agent/plan_service.py`**：投递器——读账本里该会话最后一条 `plan` 条目的正文，
   与本次渲染逐字节比，不同才 `append_events`。照 `services/memory/memory_delivery.py` 的形状。
4. **`backend/app/ai/llm.py`**：群聊与私信两条路径都接上（在 `sync_*_history` **之前**投递）；
   顺手把 `_deliver_frame_notes` / `_build_capability_notice` / 计划投递三个源收成一个 `_collect_injection_events`
   （现在那三行在 1146-1151 与 1528-1533 各抄了一遍）。
5. **`backend/app/ai/alarm.py`**：`set_alarm` 落 `frame_id` / `origin_context_ref`（读 `get_frames` 的栈顶）；
   `_process_alarm_event` 先按 `frame_id` 恢复状态（`restore_frame`）再执行，并把「被 #几 唤醒、当初写的是什么」说清楚。
6. **开关链**：`schemas/agent.py` → `services/agent/agent_service.py`（`CONFIG_PROFILES` + `current_values` + `get_effective_config`）
   → `utils/pure/presets.py` → `routers/agents.py`（创建/更新/预设预览）→ 前端四处 → i18n 三语 → `update_self_config`。
7. **验证（四层闭环）**：新单测 `backend/tests/test_plan_delivery.py`（照 `test_memory_delivery.py`：只投一次、改过只重投最新板、
   请求体里没有状态帧 id——闹钟 #id 是故意显示的，AI 靠它 update_alarm / cancel_alarm）→ 全量套件 `docker exec -w /app ai_group_backend bash -c 'export TEST_DATABASE_URL="${DATABASE_URL}_test"; python tests/run_without_pytest.py'`
   → `docker restart ai_group_backend` 看启动日志到 uvicorn running、health 200 → 真机看新增日志里 `kind=plan` 条目的位置
   与 `cached_tokens` 是否随消息数增长。

## 六、代码坐标

| 位置 | 干什么 |
|------|------|
| `backend/app/models/alarm.py` | `AgentAlarm`：agent_id / wake_at / task / status(pending/fired/cancelled) / fired_at |
| `backend/app/ai/alarm.py` | `set_alarm` / `update_alarm` / `cancel_alarm` / `fire_alarm` / `list_alarms` / `get_due_alarms` / `alarm_scheduler` / `_process_alarm_event` |
| `backend/app/ai/decider.py` | `_decide_alarm_action`（优先级 85） |
| `backend/app/services/agent/state_stack_service.py` | `get_frames` / `restore_frame`（闹钟唤醒时按帧恢复状态）/ `ensure_active_frame` / `push_state` / `pop_state` / `close_state` / `get_state_stack_summary` / `frame_turn_context` |
| `backend/app/utils/pure/state_stack.py` | `make_state_frame`(48) / `format_state_stack_summary`(147) / `MAX_STACK_DEPTH`(21) |
| `backend/app/models/agent.py` | `state_stack`(152) / `cross_state_notes`(162) / `foci`(166) / `state_stack_max_chars`(169) |
| `backend/app/ai/llm.py` | 群聊路径：锁定段 + 尾部读数(`tail_blocks`) + 账本段（记忆投递在 `sync_group_history` 之前）；私信路径同形。`message 0` 之后**直接进历史**，开头没有注入插槽 |
| `backend/app/services/history/context_sync.py` | `sync_group_history`(131) / `sync_dm_history` / `append_events`(40) / `rewrite_context`(52) |
| `backend/app/utils/pure/history.py` | `KINDS` / `NEVER_COMPRESSIBLE` / `make_entry` / `entries_to_messages` / `latest_message_ref` |
| `backend/app/utils/pure/memory_entry.py`、`services/memory/memory_delivery.py` | 记忆那一半（指纹 + id 去重）；计划的形状更薄：会话键 + 文本比对 |
| `backend/app/services/agent/workspace_service.py` | `get_current_task_text` / `set_workspace_file`（写 plan/todo 的地方） |
| `backend/app/tools/self_management/` | `set_alarm` / `list_alarms` / `update_alarm` / `cancel_alarm` / `check_workspace` / `manage_workspace` |

## 七、可选前置与后续（不做也能上）

1. **情景帧与任务帧分家**：现在两类帧挤在一个数组、共用 10 层上限，而情景帧本该按会话长期保留。
   做了它，帧 id 才可能是长期标识；不做，就靠 3.4 的离栈通知兜底。
2. **`make_entry` 的 kind 兜底**：现在是 `message`（未声明/未知的 kind 会被改写成它），建议改成通用 `system` 类。
   kind 只活在账本里（`entries_to_messages` 投影只留 role + content，前端日志读的是投影后的 messages），加 kind 不影响前端显示。
3. **唤醒提示词里也摆一次计划板**：闹钟唤醒走的是独立提示词（不读账本、不进历史），所以那一轮看不见板子。
   要做就在 `_process_alarm_event` 里按 `origin_context_ref` 渲染一次、当尾部 system 块塞进去（与它现在注入记忆的方式同形）。
   本次没做：唤醒轮本来就是冷启动，而板子已经会在那个状态的下一次正常轮次里出现。

## 八、顺带记录：记忆注入那一半的现状

已落地并**真机验证通过**（`utils/pure/memory_entry.py` + `services/memory/memory_delivery.py` + `ai/llm.py` 两条路径 + 注入文案去掉相似度），
守卫用例 `backend/tests/test_memory_delivery.py`；计划板那一半也已落地（2026-10-03），全量套件 **551 通过**。
   真机数字（同一 AI 同一群、单次调用）：
改前 201 条消息 `cached` 11,904 / prompt 26,444（45%）；改后 233 / 235 / 239 条消息时 27,264 / 27,520 / 27,648，
占 prompt 94%~97%；`messages[0]` 4894 字符逐字节相同，相邻两次请求首个不同下标 225/227（总长 233/239）。
注意重启后的**第一轮仍会全 miss**（布局变了），从第二轮起对齐。同一批收尾还删掉了 `dynamic_readings` 这个
「当轮注入区」（技能注入按它自己的注释沉到尾部），开头现在只剩走版本链冻结的锁定段。
