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
| `group_message` | content / content_clean / content_len / sender_id / sender_name / sender_type / group_id / is_mention / is_at_all / group_type | 群消息触发链路（`response_worker._maybe_trigger_ai_reply`） |
| `member_join` | member_id / member_name / operator_id / operator_name | `chat/gm.add_member`（人类成员） |
| `member_leave` | member_id / member_name / operator_id / operator_name | `chat/gm.remove_member` / `leave_group`（人类成员） |
| `scheduled` | trigger / task / alarm_id | 闹钟唤醒前（`ai/alarm._process_alarm_event`） |
| `friend_request` | requester_id / requester_name / message / request_id | 好友申请唤醒前（`ai/alarm._process_friend_request_event`） |
| `world_event` | name / title / world_id / group_id / payload_* | 世界发来的事件（`services/world/world_ai_events.py`，契约见 `docs/group_world/design/world_ai_events.md`） |

规则结构：`{name, when:{event, conditions}, do:{action,...}, notify}`；
条件 DSL 为递归逻辑树（and/or/not + 字段等于/contains/starts_with/matches/gt·gte·lt·lte/similar）。
**所有情景**都能读到判定时刻的公共字段 `now`（HH:MM）/ `today` / `weekday` / `hour`——
由 `run_decision_engine` 在入口补一次（六个情景各写一遍迟早漏一个），调用方给了同名字段以调用方为准。

关键词的三种口径也都在这一棵树上（可多选、可自由组合 and/or/not）：

| 口径 | 写法 | 说明 |
|------|------|------|
| 全等 | `{"content_clean": "签到"}` | 整条消息**就是**它。判 `content_clean` 而不是 `content`：后者带着 `<@!id>` 令牌，永远对不上（见 `clean_message_text`） |
| 包含 / 相似 | `{"content_clean_contains": "签到"}` / `{"field":"content_clean","op":"similar","value":"签到"}` | 相似用标准库 difflib 比"与关键词等长的窗口"，默认阈值 0.8，可写 `{"text":"签到","ratio":0.75}`；错别字、多几个字都认 |
| 长度 | `{"content_len_lte": 20}` | `content_len` 是 `content_clean` 的字数，配合 gt/gte/lt/lte 组条件 |

每个实体最多 20 条，同名覆盖。

## 3. 校验

- `when.event` 必须在 §2 表内 —— 写个不存在的事件名等于永远不触发，不如当场拒绝并列出可选值。
- `do.action` 四选一，各自必填项校验（`reply` / `name` / `code|entry`；`silent` 无必填项）。
  两个上限不是一回事：代发文本（`reply`）4000 字，脚本正文（`code`）50000 字。正文更长的脚本先落到
  文件空间（`run_script` 的 `path`），技能里只写 `entry` 指它——存的地方就是跑的地方。
  列表与试跑回显过长的正文时只给开头 + 全文长度（`brief_do`）：技能存在 config 里，原样回显
  等于每次列表都把几万字灌进上下文。
- `code` 与 `entry` **二选一**：沙箱的入口判定是 entry 优先，两个都给会跑 entry、`code` 静默失效，
  所以写入时就拒掉。入口文件本身抛异常时，回执与 `code` 模式逐字一致（同一个 runner 模板、
  同一条 `stderr` 末行）。
- 试跑回显的 `wakes_owner` 与真跑口径对齐：脚本没跑成时它必须是 `true`——真跑的失败与 `notify`
  无关，一律经 `failure_note` 交回本体；只照抄 `notify` 会出现「success=false + 不唤醒」的自相矛盾。
- `silent` 是「不回应」的正式表达：命中即静默（不代发、不唤醒本体）。此前只能给 `reply_template`
  塞一句空话，或让 `run_script` 打印空 JSON 绕过去。
- 关键词运算与阈值写入时校验（`utils/pure/conditions.py`）：`similar` 的关键词/阈值、
  正则的形状、条件树规模（8 层 / 64 节点）都当场说清——安静地不命中比报错难查得多。
- 工具描述与 schema 说明只有**一处**（`decision_skill.rule_schema_desc()`），
  平台工具与世界链路共用同一份文案，避免加情景时漏改一处。

## 4. 返回与 notify 语义

`run_decision_engine` 返回：

| 情况 | 返回 | 调用方动作 |
|------|------|-----------|
| 未命中 | `{hit: False}` | 按原流程（唤醒判定/意愿评分） |
| 判定阶段就出错 | `{hit: False, error, note}` | 同上；`note` 顺带说明"这不是没命中，是技能瞎了" |
| 命中，`notify=false`，办成了 | `{hit: True, handled: True, reply}` | `reply` 非空则代发，**不唤醒** |
| 命中，`notify=true` | `{hit: True, handled: False, name, result, note}` | **继续唤醒**，把 `note` 注入本轮上下文 |
| 命中但**没办成** | `{hit: True, handled: False, name, result, note}` | 同上：事件交回本体，`note` 说清哪一步没成 |

「没办成」= `do` 的返回里 `success=False`（脚本崩了、工具报错、脚本排队没轮上）。
它**不**按"办完了"静默跳过：那样等于把这个事件吃掉——人还在等回应、申请还挂着，本体永远不知道。
翻回 `handled=False` 就走原来的唤醒路径，由 `note` 说明是技能挂了，不是没人找它。

`note` 文案有三处出口，都在 `decision_skill` 里各管一种情况：`notify_note`（要本体判断）、
`failure_note`（没办成）、`engine_error_note`（判定出错）。群消息链路注入到 `build_messages` 之后，
闹钟链路注入到系统提示里；**没人被唤醒的那些**（`mention_only` 拦下的群消息、入群退群这类没有唤醒
链路的情景）由 `leave_ledger_notice` 记进账本——账本才是它一定会看到的地方。

入群/退群这类情景**没有唤醒链路**（平台本来不为它叫 AI）：`notify=true` 只是把执行结果
以账本通知（`notice`）留给那个 AI 下一轮看到，不会有人被立刻唤醒。

## 5. do 的分派

| action | 入驻 AI（agent） | 群助手（group_assistant） |
|--------|------------------|---------------------------|
| `reply_template` | 返回文本，由调用方代发（群消息/入群等群级情景发到群） | 同左 |
| `call_tool` | `ToolRegistry.dispatch` —— 平台工具，**AI 自己的身份** | `run_world_tool` —— 世界工具，世界身份 |
| `run_script` | `sandbox/agent_sandbox.run_agent_code` —— 在**自己的文件空间**里跑，禁网络/禁 fork；同一 AI 的脚本在这一层**串行**（一把锁只包执行段，等不到按失败算，于是翻回 `handled=False`） | 世界沙箱（`skill_sandbox`，世界配额）；世界侧不走那把 per-agent 锁 |
| `silent` | 到此为止：`reply` 为空，调用方不代发、不唤醒本体（与 `notify=true` 互斥，校验时拒绝） | 同左 |

脚本的返回值即"要说什么"：从 `stdout` **最后一行往回找**第一个 JSON 对象，取 `{"reply": "..."}`，由宿主代发
（脚本常在结果后头再 print 一句给人看的日志，只认最后一行会把要说的话吃掉）。
什么都不 print = 这次只想改账本，正当的静默；print 了却给不出 `{"reply": ...}` 则按失败报回本体——
格式写错了要让人看见，不能让它以为"说了但没人收到"。
脚本的**事件上下文**走 `DECISION_CTX`（JSON 环境变量）：`json.loads(os.environ["DECISION_CTX"])`
就是本次情景的全部字段（含 `sender_id` 与公共时间字段）——"按人分开记账"这类玩法靠的就是它
（工具描述里写着这条，否则 AI 只会在脚本里把 sender_id 写死）。
脚本没有联网与平台句柄，能力边界停在"算"。

`reply_template` 支持占位 `{sender_name}` `{sender_id}` `{group_id}` `{content}` `{now}`
（`render_reply_template`；认不出的 `{…}` 原样留着——正文里的花括号可能是字面意思），
所以零唤醒的固定回复也能叫出对方名字。

代发一律经 `decision_skill.send_group_reply`：标 `source="world"`（不回灌世界程序钩子），
且 AI 唤醒队列只收人类消息，因此不存在"自己说一句又把自己叫醒"的环。

代发路径自己**不 commit**：提交由开会话那一步负责（`app/database.py::work_session`，出块即提交）。「代发与落库同生共死」是契约——2026-10-04 群 69 丢的 2311/2313 就是漏在调用方没提交上。

## 6. 试跑工具（`test_decision_skill`）

`app/tools/decision.py` 的 `test_decision_skill`（描述来自 `decision_skill.test_rule_desc()`）能**不落库**地试一条规则——
草稿规则或已存规则都行，省得“规则写完、等真事件来了才知道命中没有”。

- 返回 `checked[{name, hit, reason, why}]`（每条规则为什么命中/没命中），外加 `hit` / `would`（真触发的话会做什么）/
  `reply` / `wakes_owner`（会不会唤醒本体）。
- `execute=false` 时 `run_script` 只回显不真跑：沙箱里置 `DRY_RUN=1`（`agent_sandbox.run_agent_code(dry_run=True)`），
  真跑也不落外部效果。
- 试跑不落库、不代发、不唤醒——只回答“这条规则在这么一条事件上会怎么样”。
- 规则描述里教两种口诀（`rule_schema_desc()`）：整句才触发 `{"content_clean":"签到"}`；
  提到就触发且不通知你（慎用，可能误触）`{"content_clean_contains":"签到"}`。

## 7. 触发链路上的位置

- **群消息**：决策层在 `mention_only` 拦截**之前**（AI 自写规则优先于平台默认兜底）。
- **性能**：一条消息要给群里所有 AI 过一遍，规则按消息**批量预取**（`load_rules_map`，一条 in 查询），
  不再按 AI 各查一次；候选是 `group_members.member_id`（= user_id），规则按 `agent.id` 存，
  预取时把映射一并取出。
- **不绑世界**：引擎不再要求 AI/群绑定世界。`world` 参数只服务群助手的 `call_tool`/`run_script`。

## 8. 作用域（待落地）

技能挂在**实体**上（AI / 群助手），存储里没有群字段：现状是**一处配置、处处生效**——在哪个群 @ 它、
说中关键词都会命中，`run_script` 的账本（AI 自己的文件空间）也共用同一份。要限定范围，现在只能靠条件
DSL 里的 `group_id`：由 AI 自己写，写漏了就是到处生效。

目标口径与记忆、环境、变更通知**共用同一套**（见[焦段与记忆适用范围](../memory_system/design/focus_and_memory_reach.md) §六）：

- 作用域可填**具体状态帧**（`group:{id}` / `session_id`）或**会话焦段**（一组同类会话）；
- **空 = 只有当前会话**——不存在隐式全局；
- 要全局，必须显式锚平台预置的「所有聊天」。

原生作用域字段（引擎在命中前按帧 / 焦段过滤）尚未实现；在此之前 `group_id` 条件仍是唯一手段。

## 9. 未落地

| 情景 | 卡在哪 |
|------|--------|
| `command` | **不做**：`world_chat_commands` 的 7 个命令（/new /sessions /use /pin /unpin /clear /compact）只服务群视界页面对话，群消息链路没有斜杠入口，没有可挂的事件 |

> `friend_request` 与 `world_event` 已落地（2026-09-26）。前者允许带话：`reply_template` 只写进
> 日志或实际私信，取决于执行 do 之后两人是否已是好友（通过申请即成为好友，拒绝则发不出）。
> 后者一律唤醒本体，唤醒链路见 `ai/alarm._process_world_event`。

## 10. 验证

```
docker exec ai_group_backend bash -c 'export TEST_DATABASE_URL="${DATABASE_URL%/*}/${DATABASE_URL##*/}_test"; \
  export TEST_DATABASE_URL_SYNC="${DATABASE_URL_SYNC%/*}/${DATABASE_URL_SYNC##*/}_test"; export PYTHONPATH=/app; \
  cd /app && python tests/run_without_pytest.py test_decision_layer'
```

覆盖：不绑世界也命中、`call_tool` 走平台身份、`run_script` 由 stdout 决定回复、`silent` 静默不代发不唤醒、
`silent`+`notify` 被拒、`notify=true` 带 note 继续唤醒、未知事件被拒、批量预取与逐个读同源、
入群情景端到端代发、定时情景匹配、关键词三态（全等/相似/长度）与 `content_clean` 收令牌、
相似阈值写错被拒、公共时间字段、`reply_template` 占位。
本批新增：`test_a_reply_survives_a_trailing_log_line`（末行日志别把要说的话吃掉）、
`test_a_broken_script_hands_the_event_back_with_the_reason`（脚本崩了交回本体）、
`test_a_script_that_prints_no_json_is_not_silence`（print 了却给不出 reply 算失败）、
`test_engine_failure_says_so_instead_of_looking_like_a_miss`（判定出错不是“没命中”）；
另有 `test_sandbox_layers` 的 `test_same_agent_scripts_never_overlap` 与
`test_script_that_never_gets_its_turn_fails_loudly`（per-agent 串行锁，见 `docs/dev/code_sandbox.md` §6）。
