# 决策层（Decision Layer）

> 状态：已落地并扩展到平台通用（2026-08-13 起为群视界阶段二；2026-09-26 摘掉"必须绑定世界"）。
> 实现：`app/services/world/decision_skill.py`；工具：`app/tools/decision.py`；用例：`backend/tests/test_decision_layer.py`。

## 1. 它解决什么

AI 不该被每条消息唤醒。事件先过一层决策：**AI 自己写的规则**能处理的，程序直接处理
（不产生 LLM 调用）；只有规则判定"必须本体来"或没命中时，才唤醒 LLM。

这是 QQ 全量消息的漏斗：全量模式下群里每条消息都进平台（镜像完整对话），
唤醒链只认人类消息（`chat/group_delivery.wake_group_ai`），而决策层跑在唤醒判定之前——
消息越多，省下的调用越多。

## 2. 情景表

情景是平台维护的标准化事件，规则只能挂在这些事件上（写错当场拒绝，见 §3）。

| 情景 | 字段 | 触发点 |
|------|------|--------|
| `group_message` | content / sender_id / sender_name / sender_type / group_id / is_mention / is_at_all / group_type | 群消息触发链路（`response_worker._maybe_trigger_ai_reply`） |
| `member_join` | member_id / member_name / operator_id / operator_name | `chat/gm.add_member`（人类成员） |
| `member_leave` | member_id / member_name / operator_id / operator_name | `chat/gm.remove_member` / `leave_group`（人类成员） |
| `scheduled` | trigger / task / alarm_id | 闹钟唤醒前（`ai/alarm._process_alarm_event`） |
| `friend_request` | requester_id / requester_name / message / request_id | 好友申请唤醒前（`ai/alarm._process_friend_request_event`） |
| `world_event` | name / title / world_id / group_id / payload_* | 世界发来的事件（`services/world/world_ai_events.py`，契约见 `docs/group_world/design/world_ai_events.md`） |

规则结构：`{name, when:{event, conditions}, do:{action,...}, notify}`；
条件 DSL 为递归逻辑树（and/or/not + 字段等于/contains/starts_with/matches/gt·gte·lt·lte）。
每个实体最多 20 条，同名覆盖。

## 3. 校验

- `when.event` 必须在 §2 表内 —— 写个不存在的事件名等于永远不触发，不如当场拒绝并列出可选值。
- `do.action` 四选一，各自必填项校验（`reply` / `name` / `code`；`silent` 无必填项），文本与脚本上限 4000 字符。
- `silent` 是「不回应」的正式表达：命中即静默（不代发、不唤醒本体）。此前只能给 `reply_template`
  塞一句空话，或让 `run_script` 打印空 JSON 绕过去。
- 工具描述与 schema 说明只有**一处**（`decision_skill.rule_schema_desc()`），
  平台工具与世界链路共用同一份文案，避免加情景时漏改一处。

## 4. 三态返回与 notify

`run_decision_engine` 返回：

| 情况 | 返回 | 调用方动作 |
|------|------|-----------|
| 未命中 | `{hit: False}` | 按原流程（唤醒判定/意愿评分） |
| 命中，`notify=false` | `{hit: True, handled: True, reply}` | `reply` 非空则代发，**不唤醒** |
| 命中，`notify=true` | `{hit: True, handled: False, name, result, note}` | **继续唤醒**，把 `note` 注入本轮上下文 |

`note` 文案由 `decision_skill.notify_note` 唯一给出（"你的决策技能「X」已命中并执行，结果：…。
这条消息仍需你亲自判断"），群消息链路注入到 `build_messages` 之后，闹钟链路注入到系统提示里。

入群/退群这类情景**没有唤醒链路**（平台本来不为它叫 AI）：`notify=true` 只是把执行结果
以账本通知（`notice`）留给那个 AI 下一轮看到，不会有人被立刻唤醒。

## 5. do 的分派

| action | 入驻 AI（agent） | 群助手（group_assistant） |
|--------|------------------|---------------------------|
| `reply_template` | 返回文本，由调用方代发（群消息/入群等群级情景发到群） | 同左 |
| `call_tool` | `ToolRegistry.dispatch` —— 平台工具，**AI 自己的身份** | `run_world_tool` —— 世界工具，世界身份 |
| `run_script` | `sandbox/agent_sandbox.run_agent_code` —— 在**自己的文件空间**里跑，禁网络/禁 fork | 世界沙箱（`skill_sandbox`，世界配额） |
| `silent` | 到此为止：`reply` 为空，调用方不代发、不唤醒本体（与 `notify=true` 互斥，校验时拒绝） | 同左 |

脚本的返回值即"要说什么"：`stdout` 最后一行是 JSON 时取 `{"reply": "..."}`，由宿主代发。
脚本没有联网与平台句柄，能力边界停在"算"。

代发一律经 `decision_skill.send_group_reply`：标 `source="world"`（不回灌世界程序钩子），
且 AI 唤醒队列只收人类消息，因此不存在"自己说一句又把自己叫醒"的环。

## 6. 触发链路上的位置

- **群消息**：决策层在 `mention_only` 拦截**之前**（AI 自写规则优先于平台默认兜底）。
- **性能**：一条消息要给群里所有 AI 过一遍，规则按消息**批量预取**（`load_rules_map`，一条 in 查询），
  不再按 AI 各查一次；候选是 `group_members.member_id`（= user_id），规则按 `agent.id` 存，
  预取时把映射一并取出。
- **不绑世界**：引擎不再要求 AI/群绑定世界。`world` 参数只服务群助手的 `call_tool`/`run_script`。

## 7. 未落地

| 情景 | 卡在哪 |
|------|--------|
| `command` | **不做**：`world_chat_commands` 的 7 个命令（/new /sessions /use /pin /unpin /clear /compact）只服务群视界页面对话，群消息链路没有斜杠入口，没有可挂的事件 |

> `friend_request` 与 `world_event` 已落地（2026-09-26）。前者允许带话：`reply_template` 只写进
> 日志或实际私信，取决于执行 do 之后两人是否已是好友（通过申请即成为好友，拒绝则发不出）。
> 后者一律唤醒本体，唤醒链路见 `ai/alarm._process_world_event`。

## 8. 验证

```
docker exec ai_group_backend bash -c 'export TEST_DATABASE_URL="${DATABASE_URL%/*}/${DATABASE_URL##*/}_test"; \
  export TEST_DATABASE_URL_SYNC="${DATABASE_URL_SYNC%/*}/${DATABASE_URL_SYNC##*/}_test"; export PYTHONPATH=/app; \
  cd /app && python tests/run_without_pytest.py test_decision_layer'
```

覆盖：不绑世界也命中、`call_tool` 走平台身份、`run_script` 由 stdout 决定回复、`silent` 静默不代发不唤醒、
`silent`+`notify` 被拒、`notify=true` 带 note 继续唤醒、未知事件被拒、批量预取与逐个读同源、
入群情景端到端代发、定时情景匹配。
