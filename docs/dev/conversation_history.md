# 会话历史与前缀缓存：两卷历史 + 轮末结算（设计稿）

> 2026-09-25 讨论定稿的**待批设计稿**，代码未动。批准后按"表与骨架 → 写入与封存 → 世界 AI 并入 → 两级压缩 → 收尾"分批落地。
>
> 相关旧文档：[能力懒加载（锁/解锁）](./capability_lazy_loading.md)、[跨状态交接](./cross_state_context.md)、[群视界实现](../group_world/implementation.md)。

## 0. 一句话

请求体永远是固定三段：`[锁定 system 段] + [历史] + [尾部读数]`。历史段内**只追加**，只在 compact / 超时压缩（解锁点）时重写；每轮结束由 AI 在 `end_turn` 里决定思考留不留，并把要带给后面自己的关键信息写下来。

## 1. 要解决的四件事

| 症状 | 根因 |
|---|---|
| 能力变更通知说完就没了，AI 之后又按旧表述办事 | 通知只 append 进当次 messages，**没落库** |
| 便签投递后，记录过期/删除会把已投递那份撤掉 | 每轮跟记录对账，**来源侧仍能撤回已进上下文的内容** |
| 群聊窗口每轮滑动 → 序列开头前移 → 缓存 miss | 历史是**每轮现取的滑动窗口**，没有边界锚定 |
| 压缩之后 AI 变笨（改一点东西要翻一大堆文件） | 摘要丢了"为什么"，思考被剃掉 |

## 2. 三条判据（所有内容靠它分类）

1. **事件 vs 读数**：这条内容下一轮重拼还是同样字节吗？会 → **事件**，落库进历史；不会 → **读数**，只拼在尾部（当前时间、世界时间这类"此刻状态"）。
2. **段内只追加**：历史只 append 在可复用前缀之后，绝不回改中段；**重写只发生在解锁点**（compact / 超时压缩）。
3. **渲染即落库**：落库存的是**最终字节**，不是结构化字段每轮再渲染一遍——否则字节会漂，历史自己就成了缓存杀手。

## 3. 两卷历史

| | 工具轮历史（调用轮） | 普历史（账本） |
|---|---|---|
| 装什么 | 本轮进上下文的一切：新消息、缺口事件、便签、通知、工具调用与结果 | 冻结的、要长期带的内容 |
| 生命周期 | 一轮之内（可含多次工具调用），轮末封存 | 段内只 append，compact 才重写 |
| 字节 | 轮内可增长 | 写入即定稿、永不回改 |

轮末封存 = 一次结算（见 §5），把工具轮历史收成普历史条目。

## 4. 条目模型

字段（两站同一套服务，存储各自适配）：

- `id` / `owner`（agent 或 world）/ `context_ref`（群、私信、世界会话）/ `seq`
- `kind`（message / gap / tool / note / notice / suggestion / summary …）
- `actor`（我 / 用户 / 外界）/ `content`（**渲染好的最终字节**）/ `ref`（message_id、tool_call_id 等）
- `created_at` / `flags`（可压、已撤下…）

三类来源（按时间交织）：

- **我干了什么**：工具调用与结果、我说的话、我给用户的建议回复
- **用户干了什么**：发言、@我、发图、私信
- **外界带来了什么**：群里的新消息、便签投递与撤下、能力变更通知、好友申请、世界侧事件

两条硬规则：

- **缺口事件写在这批消息之前**，与这批消息**同一批写入**（事后再插就等于改中段 → 断缓存）。缺口**只报条数**；
  要读原文，AI 用 `read_conversation`（`view_unread` 只给未读概览，不给内容）。
  **「补看」（把缺口那批原文再补一段）明确不做**（2026-09-26 定）：它就是"把窗口从 2 万加到 2 万 3"，
  多一个机制、多一份漂移风险，不如把窗口本身定清楚。
- **一次性事件一律落成条目**：便签投递、便签撤下（原文保留 + "不要再执行"）、能力变更通知、建议回复。
  - 便签从此**没有** `retired` / `notified` 标记，也**不再每轮对账**——投递就是写一条，撤下就是再写一条。
  - 建议回复条目化后，UI 的建议按钮**从条目读**，`ui.suggestions` 退场（避免两份真相）。

## 5. 轮末结算：end_turn

- **语义通用化**：它是"本轮到此为止 + 结算"；"交还发言权"只是群聊/私信场景下的效果。
- **参数**：
  - `keep_thinking`（默认 false）：本轮思考原文留不留。
  - `key_note`：留给后面自己的关键信息（为什么这么定、结论、下一步）。
- **不保留时**：思考原文与工具过程原文都不留；平台给后面的自己一条**简要系统消息**（"上一轮你调用过 X、Y，结果是 Z"）。细节必须写进 `key_note`——不写就只剩骨架，再遇到"为什么这么改 / 改到哪了"就得重新翻文件。
- **删留规则写进工具描述**（AI 得知道会删什么、会留什么），并写清场景（还有想法没表达完但必须暂时收尾时选保留）与边界（**思考只在当前这段上下文有效，不会传给别的状态**）。
- **跨轮不回传 `reasoning_content`**（改用上面那条简要系统消息）；**轮内**仍按 DeepSeek 官方要求完整回传。
- **两站**：主站复用现有 `end_turn`（`app/tools/self_management/end_turn.py`）；世界 AI 新增同名工具，并把现有"强制收尾轮"（`world_chat_service.py` 的 finalize 分支）并进同一条路径；模型忘了给决定就按默认（不保留），没给 `key_note` 就用正文当"干了什么"，**永不空**。
- **写入条件**：以"真发出去了 / 工具成功"为准；失败只记"没做完"。

## 6. 压缩

**两级（先免费、后花钱）**

1. **确定性修剪**（不调模型）：超长工具输出换成"头部 + 中间标记 + 尾部"，保留工具调用、步骤、错误与元数据；原件留档可回放。
2. **摘要**（一次模型调用）：把最旧的一段换成摘要，最近的内容逐字保留。

**摘要的输入与产物**

- **输入必须带思考**（不许剃）：剃了摘要就只剩"做了什么"、丢了"为什么"，压缩后的 AI 立刻变笨。
- **产物必带**：关键想法（为什么）、当前状态（改到哪、文件、字段、取值）、未完成与下一步。
- **必留项**：精确标识符（ID/路径/URL/名字）｜错误原文｜用户纠正与负反馈｜具体取值/公式/配置｜技术约束｜进行中工作的精确状态。
- **裁剪优先级**：用户纠正 > 错误 > 进行中 > 已完成。

**阈值与解锁点**

- **三个阈值，别压成一个数**（2026-09-25 用户定）：
  - `T_post` = **压缩后的上限**：摘要 ≤ 触发量的 20%（`COMPRESSION_TARGET_MAX = 0.20`）再留最近 N 条，
    折算到窗口 ≈ 12%。它是「压完能有多小」的地板，**不是触发线**。
  - `T_hot` = **热触发线**：体积到这条线就**必须压**（不压迟早爆窗口）。主站 = DB `compression_threshold`
    （现值 60%）× **该模型自己的窗口**；世界 AI 同口径。
  - **窗口按模型取**（第四批 c）：`utils/pure/model_window.py: context_window_for(model)`——
    认得出就用准的（如 1M 的模型按 600K 触发），认不出退回保守默认 128K。以前是个全局常量，
    1M 的模型也按 128K 触发，等于把大窗口白扔。
  - **两个系数可配**（同表同模式）：`idle_threshold_percent`（k，1-99）与 `compress_target_percent`
    （`T_post = T_hot × 它`，1-99）；**NULL = 用代码默认**（1/e、20%）——默认值只留常量一处，
    管理员改过的才落库；越界一律回默认（配置写错不能变成「永不压缩」）。
    读取只有一个入口：`get_compression_thresholds(db)`（顺带把老的单值 `get_compression_threshold` 删了）。
  - `T_idle` = **久未活跃（缓存经济）触发线**：空闲到点、且体积 ≥ 它才压——缓存已经凉，顺手把冷前缀压小，
    下一次请求的未命中就只剩「摘要 + 最近 N 条」。**由两个已有端点插值，不新造数**（2026-09-25 用户定）：
    `T_idle = T_post + k × (T_hot − T_post)`，插值系数 `k` 默认 `1/e ≈ 0.3679`（`IDLE_THRESHOLD_FRACTION`）
    （代码写 `1 / math.e`，别写 0.3679）。当前 = **29.7% 窗口 ≈ 38.0K tokens**。
    - **区间约束自动满足**：插值系数落在 `(0, 1)` 就是 `T_post < T_idle < T_hot`，等价
      `ΔT_idle(= T_hot − T_idle) < T_hot − T_post`。不贴两端：贴 `T_post` 等于每次闲置都白跑一遍压缩
      （冷路径也要一次摘要调用，不是静默剃思考）；贴 `T_hot` 就退化成只有一个阈值。
    - **为什么是 1/e（别当推导结论）**：从 `T_post` 起覆盖带宽的 63.2% ≈ 一个单位衰减尺度——
      「自然常数 + 不贴两端 + 一眼可算」。缓存经济学解不出 e 来，**唯一的定盘星是 §6.1 末尾的
      `cached_tokens` 实测**。顺带一个巧合：`T_post = 0.2 × T_hot` 时它 ≈ `0.494 × T_hot`，约等于「热阈值的一半」。
- **两条路各自判**：
  `压 = (本轮已发过消息 AND size ≥ T_hot) OR (久未活跃到点 AND size ≥ T_idle)`
  热那路超了就压，只是**等本轮发完消息**再压（保全操作链）；冷那路门槛更低，因为省下的是整段冷前缀。
  三档数值（以及 12h/18h）都要用 `cached_tokens` 实测标定（见下），现在给的是起点不是结论。
  > 现状 `executor.py:641` 是 `stale or (_has_sent_message and should_compress(...))`——**两路共用同一个阈值**，
  > 且 `stale` 那路根本没有体积门槛。目标：`stale` 用 `T_idle`、`_has_sent_message` 用 `T_hot`。
- **超时压缩 = 解锁点，目的是缓存经济**：缓存被清后少付未命中的钱（见 §6.1）。主站 12h、世界 `compact_idle_hours`（默认 18，0=关）。
  判定改用**会话最后活跃时间**（别依赖消息正文里的时间戳）；**对齐缓存寿命、用数据定阈值**——DeepSeek 没公开 TTL，
  用我们已经在记的 `cached_tokens`（主站 `llm.py:283/373`；世界侧 `WorldLLMUsage.cached_tokens` +
  `routers/worlds.py` 的 `cache_hit_rate_pct`）看命中率随空闲时长怎么衰减，再定 12h/18h 还是别的值。
- **解锁必须"整套"**：重写历史（摘要 + 最近 N 条）+ 复位思考保留标记 + 卸载最旧的图 + 清便签副本/条目。
  少做一样就是半解锁（上次便签就是只清了一类）。
  **纪律做成了契约**（第四批 d）：`executor.UNLOCK_STEPS` 是唯一清单（新增动作只加一行、顺序也在这），
  `_unlock_context` 按清单执行并把**实际执行的步骤**返回；`tests/test_unlock_steps.py` 拿它跟清单对账
  ——改清单必须同时改测试（那道摩擦是故意的）。任何一步失败都**响**：日志写明哪步挂的、前面做完了什么。
  尚未实现的两条（复位思考保留标记 / 卸载最旧的图）随第四批的思考与图片落地时加进清单。

### 6.1 排程：把未来的动作写进一条链，而不是每次现算（2026-09-25 定）

用户提法：**在最后一次活跃时就把"未来该干什么"规划好**（要不要压、多久该压、多久一个闹钟、多久算超时），
写成一条排程链，到点触发——而不是每次事件现算。

**为什么要压（原因说准，2026-09-25 用户纠正）**：平台的上下文缓存（DeepSeek 等）**几个小时到一天就会被清掉**。
缓存一没，下一次请求的整段前缀都按未命中计费——上下文越大，白花的钱越多。
所以要**趁空闲先压小**：等下一次真有人说话时，未命中的只剩"摘要 + 最近 N 条"这么点，
而且这份新前缀很快又能重新命中。压缩时机对着**缓存寿命**，不是对着"用户体感"（体感变好只是顺带）。
代码里本来就写着这个理由：`executor.py:1161`「检查对话是否闲置——**缓存大概率已过期，应强制压缩**」、
`world_chat_service.py:1252`「空闲超时先压缩再继续，**趁缓存最大化利用**」、
`docs/group_world/implementation.md:102`「一次压缩一次 miss，之后稳定命中」。

**现状与它解决什么**：

- 现状是**懒检查**：主站构建上下文时比较（`executor.py:455/640`），世界 AI 发消息时比较
  （`world_chat_service.py:1252`）。复杂度其实是 O(1)/事件，**不是每帧×N**。
- 真正的缺陷是**语义**：没人再发消息 → 永远不触发压缩；等下一次有人说话才压，而**那一次请求是在缓存已经过期的情况下全量按未命中计费**。
- 排程的真正收益：① **到点主动压**（卡在缓存过期前后，把"必然全 miss 的那一次"挡在门外）；
  ② 判断收敛成一处（"最后一次活跃时算一次"），不再散在 executor 两处 + 世界一处；
  ③ 重新活跃时重排，用**世代号**防重复触发。
- 复杂度对比：定时扫描所有会话 = O(会话数)/tick（要避免）；排程链 = 写入时算一次 + 调度器只看最早 deadline（到点触发 O(1)）。

**落地形态（复用现有闹钟，不新造轮子）**：

- 现有设施：`ai/alarm.py`（`AgentAlarm` 表 + `alarm_scheduler` + `notify_alarm_changed()` 事件驱动，
  `bootstrap.py:223` 用 `spawn_task(..., restart=True)` 常驻）、`world_scheduler`、`trigger_sweep` 的 `alarm_fired`。
- 每个会话一条"下一次动作"排程：`(agent_id, context_ref, due_at, action, generation)`，`action = compact`。
- **写入时机 = 轮末封存时**（正好是"最后一次活跃"）：`due_at = last_active + idle_hours`。
- 重新活跃：`generation + 1` 重排；旧 deadline 到期时发现 generation 落后 → 跳过（幂等）。
- 到点执行"**整套解锁**"：重写历史（摘要 + 最近 N 条）+ 复位思考保留标记 + 卸载最旧的图 + 清便签副本/条目。
- **两条阈值都并进同一条链**：轮末封存时按体积写 deadline——超 `T_hot` → 「尽快」（`due_at = now`）；
  超 `T_idle` → `due_at = last_active + idle_hours`（到点再复核体积仍 ≥ `T_idle`，不满足就跳过并重排）。
  这样"时间"和"量"两条路都归一处执行，`executor` 里的两处 `if` 可以退场。

> 这一层和第四批（两级压缩）一起做——它是"什么时候压"的唯一入口。

## 7. 图片

- **没压缩之前保持原样**：图就是图（必要时按像素预算缩放，但别换成文字）——降级成 `[图片]` 等于让 AI 不知道那是什么。
- **确定性表示**：同一张图每次渲染成同一份字节或同一个引用（否则从那张图起 token 全部 miss）。
- **卸载只在 compact / 超预算时做**，最旧优先；卸载了才出现占位。
- 现状是 bug：`world_chat_service.py` 注释写着"只有最后一条带真实图片字节（同批靠前的降级成 `[图片]`）"——上一轮的图下一轮变历史就降级 → 那段前缀变 → 从那图起全部 miss。

## 8. 快照检测（保留，两件事都干）

- **生效**：锁定态用 effective 快照 → 老会话的 system / tools 字节不变 → **前缀稳定**。
- **告知**：known vs latest 的差异 → 决定**该不该通知、通知哪些变化**（增量 changelog）。
- **本次唯一改动**：通知**落成条目**（不再当轮 append 即丢）；解锁时按 latest 重建（新会话起点）。
- 涉及现有实现：`capability_versions` / `agents.cap_known_versions` / `agents.cap_effective_versions` / `ensure_text_source_version` / `build_change_notice` / `apply_pending_changes`。

## 9. 两站共用与差异

| | 共用 | 差异 |
|---|---|---|
| 服务 | `history_service`（append / read / seal_turn / compress / clear）、压缩提示模板、确定性修剪器 | —— |
| 存储 | —— | 主站新建表；世界侧复用 `world_chat_messages` 加列（它本来就是持久历史，不迁移） |
| 阈值 | `T_post / T_idle / T_hot` 三档口径与判定公式 | 主站 `T_hot` = 60%（DB 现值、128K 窗口）/ 12h；世界 60% / 18h（分档是**有意**的） |
| 思考 | `end_turn.keep_thinking` | 世界 AI 的 `reasoning` 从"展示用，不进上下文"改为**进上下文**（历史会变重，需接受） |

## 10. 迁移

- **老会话**：状态栈（`agents.state_stack`）与群视界历史（`world_chat_messages`）**一次性拆进历史**；老会话在新结构下重新拼一次（一次性重建，不额外付缓存代价）。
- **投递一条变更通知**告知 AI"上下文组织方式变了"（复用现有懒通知/能力变更通知通道）。
- 新会话直接按新结构走。

## 11. 验收

- 单测：条目渲染字节稳定｜缺口与它那批消息的顺序｜轮末封存｜`end_turn` 结算（含默认兜底）｜裁剪优先级｜修剪器边界｜图片确定性表示。
- 行为实测：两次请求的**公共前缀字节一致** + provider 报的 `prompt_cache_hit_tokens` 不下降；便签撤下只写一次；超时压缩之后仍能命中。
- 按既有四层闭环走（编译 → 冒烟 → 重启日志 → 真机行为），并补 CHANGELOG。

## 12. 待定与风险

**待你勾**

1. ~~现在工作区那批未提交改动：先提交，还是直接在其上改？~~ → **已提交（2026-09-25，7 个提交，见 git log）**
2. 超时阈值要不要统一（主站 12h / 世界 18h）？
3. 是否先跑一次实测钉死"不保留思考"会不会 400（两条 API 调用）。
4. ~~主站 `T_hot` 与 `context_window`~~ → **已定（第四批 c）**：窗口按模型取（`context_window_for`），
   1M 模型现在按 600K 触发；`T_hot` 仍是 60%（DB 可配）。剩下的是**表内容维护**：新模型加一行；
   要不要把「模型 → 窗口」也做成后台可配（现在在代码表里）。

**已知风险**

- 世界 AI 思考进上下文后历史变重（token 与窗口）。
- **主站压缩触发有两路，互不依赖，别把空闲那路当唯一**（2026-09-25 修正）：
  ① 空闲 12h：`_is_conversation_idle`，在 `executor.py:455`（LLM 调用前）与 `:640`（工具循环里）**两处检查点**；
  函数内**两种判定**：最后一条消息距今 > 12h、对话跨度 > 12h；
  ② **体积阈值**：`:641` `stale or (_has_sent_message and should_compress(...))`；要求本轮发过消息（保全操作链），
  且**两条路要分用 `T_hot` / `T_idle`**（见 §6）——现在两路共用同一个阈值。
  隐患只在①：它读消息正文里的时间戳（`_parse_ts`），历史被压成摘要 / 格式变了 / 窗口里没有可解析时间戳时失效——
  **后果是少一层保护，不是永不压缩**（② 仍会触发）。建议①改用**会话最后活跃时间**（DB 现成）判定，与②互不依赖。
- 用途不同、**不负责压缩**的其它超时（别和上面混）：免打扰到期（`group_members.dnd_until` / `dm.user*_dnd_until`）、
  主动发言意愿（`ai/decider.py:63 idle_seconds` → `utils/pure/willingness.py` 的 `hours_idle`）、
  聊天链深度（`chain_depth`）。世界里另有 `compact_idle_hours`(18) / `auto_new_time`(04:00) / `retention_days`(90)。
- 图片不降级会让请求体变大，需要"总像素 + 张数"预算兜底，参数要调。
- 压缩那次模型调用的成本（世界 AI 尤其）要观测。

## 13. 落地进度（实现细节）

> 每批落地后回填本节：**改了什么、在哪个文件、为什么**——后续变更时对照这里，别只看设计。

### 第一批：账本存储层（已完成 2026-09-25，未提交）

- **表** `agent_history_entries`（迁移 `f1e2d3c4b5a6`，down_revision=`b8d2e4f6a1c3`）：
  `id / agent_id(FK agents CASCADE) / context_ref / seq / kind / actor / content / ref / flags / created_at`；
  唯一约束 `uq_agent_history_seq(agent_id, context_ref, seq)`，索引 `ix_agent_history_ctx_seq`。
- **模型** `app/models/agent.py: AgentHistoryEntry`（注释里写了"为什么要这张表"）。
- **纯函数** `app/utils/pure/history.py`：`ACTORS` / `KINDS` / `ROLE_BY_ACTOR` / `NEVER_COMPRESSIBLE`、
  `make_entry` / `gap_text` / `gap_entry` / `is_compressible` / `entries_to_messages`。
  `gap_text` 是**唯一文案来源**（主站群聊截断提示将来也读它，避免两处各写一遍）。
- **服务** `app/services/history/history_service.py`：`append`（统一分配 seq，批次顺序即传入顺序，
  **缺口事件与它那批消息必须同一次 append**）/ `read(since_seq=…)`（compact 边界锚点）/
  `count` / `last_seq` / `clear`（解锁重写——只许解锁点调）。
- **测试** `tests/test_history_entries.py`（6 条：缺口文案唯一来源、投影、事件不可压、
  seq 按会话独立、since_seq 边界、clear 不动别的会话）。
- **验证**：全量 260/0（18.1s）；真库迁移 head=`f1e2d3c4b5a6`；`health=healthy restarts=0`。

### 第二批 a：`end_turn` 轮末结算（已完成 2026-09-25，未提交）

- `app/tools/self_management/end_turn.py`：语义升级为"结束本轮 + 结算"（不再是"交还发言权"），
  新增 `keep_thinking`（默认 false）+ `key_note`；描述里写清**删留规则、使用场景、边界**
  （思考只在当前这段上下文有效，不传别的状态）。
- `app/ai/executor.py`：新增 `_settlement` 收下这两个值（`_dispatch_one_tool` 里 `end_turn` 命中处），
  并在 end_turn 退出时打日志；**账本封存落地后在这里写条目**（就一处，别在别处再抄一份）。
- `app/prompts/core_identity.txt`：认知模型那条补"默认不留给后面的自己"；「收尾与连发」补结算规则。
- **测试** `tests/test_end_turn_settlement.py`（2 条：默认不保留、显式保留 + key_note 去空白）。

### 第四批 a：三档阈值落地（已完成 2026-09-25）

- `services/memory/context_compression_service.py`：`CompressionThresholds`（`post` / `idle` / `hot`）
  + `compression_thresholds(hot)` 由热阈值**一处推出三档**（调用点不各自乘系数）+ `IDLE_THRESHOLD_FRACTION = 1 / math.e`。
  为什么 1/e 写在常量旁边（不是推导结论，定盘星是 `cached_tokens` 实测）。
- `ai/executor.py`：`:458`（第一次 LLM 调用前的空闲检查）与 `:649`（工具循环里）两条路**各自过体积门槛**——
  冷路径 `T_idle`、热路径 `T_hot`；`stale` 变量现在 = 空闲**且**超 `T_idle`，日志文案不变。
- 测试 `tests/test_compression_thresholds.py`（3 条：区间内部 + 1/e 插值、三档随热阈值联动、`should_compress` 边界）。
- **验证**：全量 265/0；容器重启后 `health=healthy restarts=0`；真库读到
  `hot=0.60 → T_post=15.4K / T_idle=38.0K / T_hot=76.8K`；25 个真机会话体积全部 < `T_idle`（最大 34.7K）——
  旧逻辑逢 12h 闲置必压一次，现在一个都不压（这就是这条门槛要拦的浪费）。

### 第二批 b-1：主站群聊历史读账本（已完成 2026-09-25）

- **新入口** `services/history/context_sync.py`：`sync_group_history(db, agent, group_id, cap, max_len)` 是
  「群聊历史 → 账本」的**唯一入口**——读账本 → 从账本自己推水位（最后一条 message 条目的 `ref` = 消息 id，
  **不另立游标表**）→ 取水位之后的新消息（**从最新往回：最多 20 条 / 20 000 字，谁先到算谁**；
  字数按**渲染后**的长度算——超长消息折成 2048 + 省略标记后就占那么多，不是原文那么多；
  单条就超预算时至少也带它一条，否则水位永远不动）→
  装不下的折成**缺口条目**（只报条数）排在最前面、**同一次 append** → 返回整段条目。
  幂等：没有新消息就一条不写，渲染出的仍是同一份字节。
  （2026-09-26：字数窗口 40 000 → 20 000；更早的原文不另行补看，AI 自己用 `read_conversation` 翻。）
- `utils/pure/history.py`：`latest_message_ref(entries)`（水位推导，纯函数）。
- **渲染归位**：`resolve_speaker_names` / `gm_message_entry`（渲染即落库）落进 `chat/gm.py`；
  `chronological` / `keep_newest_within` 落进 `utils/pure/prompting.py`；`ai/llm.py` 里那三份私有副本删掉
  （同一个东西抄两遍，改一处忘一处）。
- `ai/llm.py:build_messages`：群聊历史段从「每轮按最新 N 条重建窗口」改成「读账本」——
  这是**前缀每轮前移 → 整段 miss** 的正主；未读兜底改读账本水位（原来读的 `recent_messages` 已不存在）。
- **验证**：全量 265/0；重启后 `health=healthy restarts=0`；真机 agent 24 / 群 64 **连续两次构建字节完全一致**
  （8382 bytes，公共前缀 27 条全同）；账本 21 条（1 缺口 + 20 消息）。
- **侧记（下一片要修）**：能力变更通知仍是「当轮 append 即丢」——实测两次构建刚好差这一条（27 → 28 条），
  正是 §4 说的「一次性事件要落成条目」；探针会把它标记成已告知，已把 `cap_known_versions` 回滚，留给下一轮真机。

### 第二批 b-3a：一次性事件落成条目（已完成 2026-09-25）

- `context_sync.append_events(db, agent, context_ref, events)`：一次性事件落账本条目（空内容不入账）；
  调用方把返回的条目接着渲染进本轮请求。
- `ai/llm._build_capability_notice(db, agent)`：能力变更通知抽成一处（群/DM 共用），
  不再各自抄一段 `build_change_notice` 调用。
- 群路径：**能力变更通知 + 便签撤下通知**都在读完账本后立刻 `append_events`，
  **紧跟历史、排在尾部读数之前**——这是刻意为之：若留在原来的末尾，下一轮它们会从末尾跑到中间，
  顺序一变就是从那条起断缓存。原来的「当轮 append 即丢」`db.commit()` 也一并去掉（同轮事务更正确）。
- **验证**：全量 266/0；重启 `health=healthy restarts=0`；真机 agent 24 / 群 64 连续两次构建
  **字节完全一致（28 条 / 8935 bytes）**——修前是 27/28 条（通知只在第一轮出现）；账本 22 条
  （1 缺口 + 20 消息 + 1 通知）。探针这次不再需要回滚 `cap_known_versions`：通知已经落在账本里，
  下一轮真机照常看得到（这正是本条要修的病）。

- DM 侧暂不动：`build_dm_messages` 还没读账本，事件落进去就是「写进一段没人读的账本」——等 b-2。

### 第四批 b：解锁点重写账本（已完成 2026-09-25）

- **补的洞**：`history_service.clear`（文档写的「解锁唯一重写口」）此前**一个调用方都没有**——
  账本只进不出。后果两条：① 压缩只改当轮内存，下一轮 `build_messages` 又从账本端回原文，
  压了等于没压还每轮白付一次摘要调用；② `read` 不带上限，请求体随会话单调增长。
- `context_sync.rewrite_context(db, agent, ref, *, summary, keep_last)`：**唯一**允许动中段的地方——
  `clear` 后写回「摘要 + 原样搬运的事件（缺口/便签/通知）+ 最近 keep_last 条」；账本空则什么都不做。
- `context_ref(*, group_id=None, session_id=None)`：会话键**唯一来源**（群 `group:{id}` / 私信 `session_id`），
  关键字参数逼调用点说清在哪个会话。
- `compress_messages` / `inline_compress` 的 stats 带上 `summary` 文本（不留就写不出摘要条目）；
  内联那条用同一句折叠文案，不另写一遍。
- `executor._unlock_context(...)`：**解锁整套**收一处——重写账本 + 复位便签副本 + 应用挂起配置/能力变更；
  调用前（`:494`）与工具循环（`:684`）两条路径共用。顺带修掉调用前那条「半解锁」（以前只压内存）。
- **验证**：全量 267/0；重启 `health=healthy restarts=0`；真机库（agent 24，草稿会话，跑完即清）
  40 条 / 10423 bytes → **22 条 / 5426 bytes**（结构 `summary + 1 缺口 + 最近 20 条`，seq 重排，二次重写稳定）。

### 第二批 b-2：DM 走账本（已完成 2026-09-25）

- `context_sync.sync_dm_history(db, agent, session_id, *, cap)`：与群聊同一套语义（水位从账本推、
  缺口同批在前、幂等）；私信不按字符二次裁剪（与旧路径同口径）。
- 渲染归位：`chat/dm.py:dm_message_entry`（渲染即落库，附件名注入正文） + `resolve_dm_sender_names`
  （同一人只查一次；旧路径是逐条查 `User.username`）。
- `build_dm_messages`：历史段读账本；**能力变更通知 + 便签撤下通知**同样落成条目（放历史之后、尾部读数之前）。
  至此群聊与私信两条路径共用同一套账本语义。
- **验证**：全量 267/0；重启 `health=healthy restarts=0`；真机 agent 24 / 会话 `1_40`：
  账本 176 条，连续两次构建**字节完全一致（182 条 / 65321 bytes）**。

### 第四批 c：系数可配 + 窗口按模型（已完成 2026-09-25）

- **两个系数从代码常量变成可配**：迁移 `a7b8c9d0e1f2` 给 `conversation_log_config` 加
  `idle_threshold_percent` / `compress_target_percent`（都可空，**NULL = 用代码默认**）；
  读取唯一入口 `get_compression_thresholds(db)`（越界回默认并告警），老的单值 `get_compression_threshold` 删除；
  `compression_thresholds(hot, *, idle_fraction=…, post_fraction=…)` 的系数是**参数**不是全局状态。
  管理链路照抄既有模式：`conversation_log_service.update_config`（1-99 校验）+ `routers/admin.py` 字段 +
  前端 `ConversationLogTab` 三个滑块（顺手把原先硬编码的中文标签也走了 i18n 三语）。
- **窗口按模型**：`utils/pure/model_window.py`（纯函数 + 一张表，认不出退回 128K）；
  `executor` 两处 `should_compress` 都带上 `context_window=context_window_for(model)`。
- **验证**：全量 270/0；`tsc --noEmit` 与 `node scripts/check-i18n.mjs` 均无输出；真库迁移 head=`a7b8c9d0e1f2`，
  两列为 NULL（用默认）；真机 `deepseek-v4-flash` → 15.4K/38.0K/76.8K，`gpt-4.1` → **120K/296.6K/600K**（1M 窗口用上了）。

### 第四批 d：解锁清单即契约（已完成 2026-09-25）

- `executor.UNLOCK_STEPS`（数据，不是散在函数体里的调用）：`rewrite_history` / `clear_note_copies` /
  `apply_pending_config` / `apply_pending_changes`；`_unlock_context` 按清单顺序执行并返回实际执行的步骤名。
- 任何一步抛异常都记 `logger.exception`（哪步挂的 + 前面做完了什么）再往上抛——静默半解锁正是便签那次的病根。
- 测试 `tests/test_unlock_steps.py`：① 清单 == 约定集合（改清单必须改测试）；
  ② 真跑 `_unlock_context`（真库草稿会话）断言执行步骤 == 清单且账本真被重写成摘要在前。
- **验证**：全量 272/0；重启 `health=healthy restarts=0`。

### 第二批 b-4：轮末封存（已完成 2026-09-25）

- `utils/pure/history.py` 新增三个条目构造器（文案唯一来源）：`tools_entry`（**一条**本轮工具总账：
  `send_gm(ok)；view_unread(失败：超时)`——逐个调用落账本会被工具淹没，细节本来在 ConversationLog）、
  `handoff_entry`（`end_turn.key_note` → kind `handoff`，**加入 NEVER_COMPRESSIBLE**：留给后面自己的不能揉进摘要）、
  `thinking_entry`（`keep_thinking=true` 时的整段推理 → kind `thinking`）。新增 kind `handoff`。
- `executor._seal_turn(...)`：轮末把工具总账 + 交接 + 保留的思考按序写进账本；三个出口
  （`end_turn` 工具 / `intent=end_turn` / 循环走完）共用一次 `_seal(...)` 闭包，失败只告警不致命。
- **顺手修掉一个真 bug（探针抓到）**：`sync_group_history` 里 `context_ref(group_id)` 是位置调用，
  而第四批 b 把它改成了关键字参数 → 群聊 `build_messages` 直接 `TypeError`（线上下一轮必炸）。
  已修 + 补回归测试（真起一个群、两条消息，跑 `sync_group_history`）。
- **再修一个**：`get_gm_messages` 在 `after_id` 有值时返回**倒序**，而 `chronological` 靠时间戳判先后
  （同一秒插入的两条判不出来）→ 增量同步会把新消息倒着追加。改成**按 id 归正**（水位本来就是按 id 记的）；
  回归测试覆盖增量路径（新消息只往后追加、顺序为 第一→第二→第三）。
- **验证**：全量 276/0；重启 `health=healthy restarts=0`；真机草稿会话：封存条目在请求里如实渲染
  （`[本轮工具] send_gm(ok)；store_memory(失败：超时)` / `[上一轮交接] …` / `[本轮思考] …`，两次构建字节一致）；
  增量后账本顺序 `第一条→第四条` 正确。
- **同一批 b-4 里修掉的第三个 bug（最贵的一个）**：**新消息一到，message 0 就变了**——
  `injected_skills`（记忆注入）的检索词就是「最近 5 条消息」（`build_messages` 的 `query_text`），
  而它被拼进了锁定 system 段。后果：群里有新消息时前缀从**第 0 条**起全 miss，
  前面所有缓存纪律白做（`_build_injected_skills` 自己的 docstring 都写着「这是最动态的段」）。
  修法：群聊与私信两条路径都把记忆注入**移出 message 0、沉到尾部读数**（`dynamic_readings` → `tail_blocks`），
  与任务/状态栈/通道规矩同一位置。
  **实测**：插一条新消息前后两次构建，`message 0` 字节一致；首个差异下标从 **0 → 4**
  （等于只能断在「新追加的那条」上，这正是只追加该有的样子）。
- **记忆索引挂上同一条版本链（冻结 + 差分通知都复用现成机制）**：`injected_skills` 段里的东西按
  「当轮输入」一分为二——**索引**（`format_db_records_for_prompt` 只看 `agent.id`，稳定）走
  `memory_index_source(agent.id)` → `ensure_text_source_version` / `get_effective_text`，**回锁定段**；
  **召回 + 技能注入**（检索词是最近 5 条消息）留尾部读数。`build_change_notice` 的源列表加上索引源，
  改动自动走既有尾部 changelog。
  **实测**（真机草稿会话）：写一条记忆前后 `message 0` 字节一致；尾部出现记忆索引变更通知；
  版本链 v1(161 字) → v2(185 字)、`known=2 / effective=1`——**AI 已被告知，但前缀仍用 v1，等解锁才对齐**。

### 第二批 b-3b：便签投递 = 账本条目（已完成 2026-09-25）

- **投递改成逐条条目**：`_deliver_frame_notes(db, agent, context_ref, entries)` 不再往 message 0 后面插前缀块，
  而是返回要追加的条目——`note_entry(note_id, text)`（`ref=note:<id>`，**账本里有了就不再投**，幂等锚点就是账本本身）；
  撤下通知同批返回（也标 `drop_on_unlock`）。文案唯一来源：`pure/cross_state_note.format_note_delivery`。
- 群聊 / 私信两个调用点都挪到「读完账本之后」并并入 `events`（与能力变更通知同一批、同一位置）。
- **解锁丢弃**：`rewrite_context` 新增通用规则——带 `flags.drop_on_unlock` 的条目**只活到解锁**，
  哪怕落在「保留最近 N 条」窗口里也走（解锁是便签唯一的退出点）。
- **便签的投递语义（2026-09-25 用户确认）**：40 次调用有效期内，**每进一个会话各投一份**——
  「已投过」的判据是**该会话自己的账本/帧副本**，不是全局标记；同一个会话只投一次。
  推论（用户同意）：**整套解锁**（重写账本 + 清帧副本）之后，只要记录还在有效期内，该会话会**重新拿到**它
  ——便签记录侧只管「还能不能投」，会话侧只管「我投过没有」，两边各自记各自的，不互相代表。
- **验证**：全量 278/0；重启 `health=healthy restarts=0`；新测试 `test_note_delivery.py` 走完五段——
  投递一条 note 条目｜再投一次一条不写（幂等）｜撤下补一条通知（不改已投的那条）｜解锁后两条一起清干净｜
  **整套解锁后有效期内重新投一次**。

### 跨对话回复的「读」：read_conversation（已完成 2026-09-25）

- **写早就通了**（`send_gm(group_id=…)` / `send_dm(target_user_id=…)` 都能发到任意目标），缺的是「知道那边发生了什么」——
  以前只有 `view_unread` 的未读数与预览，等于盲发。
- 新工具 `chat_social/read_conversation`：读**别的会话**最近几条**原文**（只读、不切状态）；
  群传 `group_id`、私信传 `target_user_id`（会话 id 取库里 `dm_sessions` 的真相，不在工具里重算规则）。
- `history_service.tail(db, agent_id, context_ref, n)`：读账本**最新 n 条**的唯一入口
  （`read(limit=)` 是从最早往后取、给 compact 边界用的，两者别混）。
- 权限边界天然成立：账本按会话分卷，**只有它参与过的会话才有条目**——读不到别人的会话。
- **验证**：全量 278/0；`test_read_conversation.py`：读尾部（不是最早几条）、别的会话不串进来、
  提示语说清「这不是当前会话」、不指定会话就报错而不是猜。

### 本轮线上问题修复（2026-09-25，与账本设计无关但同批提交）

- **两个 QQ 通道互相顶掉**（`0cc1184`）：出口注册表按**名字**存且「同名覆盖」，两个 qq-channel 实例都用插件类型 id 注册
  → 后注册的 `agent-8` 顶掉 `agent-24` 的出口，群 64 的 AI 回复被分发到「只认群 65」的 sink 并静默 return
  （现象：Copree 镜像有消息、QQ 收不到、日志干净）。改成**句柄制**（`register_sink` 返回句柄、同名不去重）+
  分发 `asyncio.gather` **并发**跑全部出口；插件用 `ServicePlugin.key`（带实例）注册并存句柄，stop 时精确注销。
  测试 `test_outbound_registry.py`：同名两个出口都要发 / 一个坏出口不拖累别的 / key 带实例。
- **向量维度静默写失败**（`7b50515`）：`models/*.py` 的向量列是 `vector_column(settings.embedding_dimension)`，
  **import 时**取静态值（容器无 `EMBEDDING_DIMENSION` → 默认 1536），而 DB 配置（768）、四个 `vector(768)` 列、
  本地 `nomic-embed-text`（768）三者本来一致 → INSERT 生成 `::VECTOR(1536)` 去转 768 向量必然失败，
  记忆一直写失败重排队（DB 覆盖在 bootstrap 之后加载，管不到已冻结的列类型）。
  修法：compose 补 `EMBEDDING_DIMENSION: ${EMBEDDING_DIMENSION:-768}`；
  **收尾**（`a3fb194`）：`check_dimension_consistency` 自检「ORM 列维度 / 生效配置 / 库里实际列维度」三者，
  `prestart.py` 迁移后调用——不一致 stderr 打 `[ERROR]` + 修法，一致打「向量维度自检通过」。
- **删池 Key 500**（`9005235`）：`api_usage_log.pool_key_id` 外键无 ON DELETE 规则，池 Key 一旦有用量记录就删不掉
  （`DELETE /admin/api-key-pool/1` → ForeignKeyViolationError）。迁移 `b8c9d0e1f2a3` 改 `ON DELETE SET NULL`
  （列本就可空）：用量历史保留、引用置空。
- **池 Key 管理补齐**（`30bdf6c`）：① `PUT /admin/api-key-pool/{id}` 之前**不收 `api_key`** → 密文解不开时没有修法
  （前端只能删了重建），现在支持重填明文；② 新增 `POST /admin/api-key-pool/{id}/test` 测通
  （探测策略/文案/脱敏全复用 `api_probe.probe_provider`，不另写一套）；前端 `ApiKeyPoolTab` 加「编辑」「测通」
  ＋ i18n 三语，并修标签键大小写（`admin.apikeyPool` vs 字典里的 `admin.apiKeyPool`）。
  真机验证：key#1 → `decrypt_failed`；临时 key 指本地 Ollama → `ok`「连接成功，8 个模型可用」。
- **用户已重填池 Key #1**：实测解密 OK（明文长度 35）——③ 收工。

### ④ 待做：QQ mention 双向映射（现状、证据、为什么卡住）

**入站（@别人 拿不到）**，两条证据：
- 真实载荷：`GROUP_AT_MESSAGE_CREATE` 字段 = `[author, content, group_id, group_openid, id, message_scene,
  message_type, timestamp]`，`mentions=null`；那条「@机器人 + @成员」的消息 content 到成员 @ 处就断了
  （DB `messages.id=1350` 存的就是 13 字，不是我们截的）。
- 官方文档（`tencent-connect/bot-docs` 的 `message_format.md`）：@ 是 `content` 里的内嵌格式 `<@user_id>` / `<@!user_id>`，
  且入站抄送会把 `<` `>` 转义——但那是**频道**体系；群聊（@机器人模式）实测载荷里连 mentions 字段都没有。
  **未验**：全量群消息模式（`GROUP_MESSAGE_CREATE`，需「接收所有消息」权限）是否带 mentions。

**出站（能不能 @ 别人）**：工具已落地（2026-09-25），**结论等你点一次按钮**。

- 端点 `POST /me/agents/{agent_id}/channels/{plugin_id}/self-test`（不是原计划的 `/admin/channels/{key}`）：
  和 start/stop 同一族，鉴权走「我的 AI」归属检查，实例 `agent-<id>` 由后端推——卡片本来就是按 AI 画的，
  走 admin 前端还得再拼一遍注册表 key。
- **必须在本进程内点**：被动回复凭据（`msg_id`/`seq`）与插件客户端都是内存态。实测：另起进程
  `PluginRegistry.get("qq-channel:agent-24")` 是 None（目录插件由 bootstrap 加载，import main 不算）；
  HTTP 请求落在服务进程里，才有活的路由表可问。
- 能力由插件声明（`ServicePlugin.self_testable`，和 `multi_instance` 同一种写法）：QQ 通道 True、
  NapCat 未实现 → 卡片**不画**那个按钮，而不是画一个点了只会报错的。
- 探针一句话里并排两种写法：`（通道自测，请忽略）内联@：<@!openid> ／ 纯文本：@名字`。
  群里那个 openid 是**发言人自己的 member_openid**（新存进 route 的 `peer_openid`）——
  腾讯侧只有它能当 @ 的目标。
- 只走被动回复（`_deliver(..., passive_only=True)`）：窗口过期就直接说「先去 @ 一次」，
  **不**退成主动消息——自测不该靠一条有配额的主动消息冒充「通」。
- 发送复用真实那条路：`_send_reply` 拆成 `_deliver`（频控 + 窗口 + 发送，失败一律抛）与后台包装
  （fire-and-forget 那侧把异常记进 `last_error`）；自测直接调 `_deliver`，把异常当**内容**返回（HTTP 仍是 200）。
- 怎么读结果：QQ 里「内联@」后面出现蓝色的你 = 支持内联 @ → 再去开「接收所有消息」权限验入站；
  显示成原文尖括号 = 不支持 → 保留纯文本「@名字」方案。
- **实测结果（2026-09-25，用户截图）**：探针正文里的 `<@!member_openid>` 在 QQ 群里显示成了
  `@书爱 Shu Ai Neumann LOVE`（昵称）——openid 能变成昵称，只能是腾讯**解析**了这个标记；
  若它当普通文本，第一处占位符就该是一串原文尖括号（探针两处占位符都显示了昵称，第一处来自内联标记、
  第二处来自我们写的纯文本，这本身就是对照）。
  **结论：群消息正文认内联 @，出站映射可行**。截图看不出颜色，唯一待确认的是它是否可点/会提醒。
- 验证：`test_self_test_sends_on_the_live_route`（无路由说人话 / 真发且带 `<@!OPENID>` / 窗口过期不退主动）、
  `test_channel_self_test_needs_a_live_instance`（能力位 + 没实例报「404 的料」）；插件 12/0、通道 9/0，
  tsc 与 check-i18n 均 exit 0，重启后 `healthy restarts=0`、两个 QQ 通道都已启动。

### ① 待做：通道文案

`app/services/plugin/channel.py:131 group_brief` 补一句：QQ 官方接口不转发 @其他成员 的内容，
句子在 @ 处突然断掉是通道限制、不是对方没说完整；可以直接问一句或忽略。
（若 ④ 证明全量模式能给 mentions，再升级成真映射。）

### @ 提及全面 id 化（2026-09-26 落地）

**病根**：平台内的 @ 一直是纯文本 `@名字`（前端补全插名字、后端 `check_mention` 按名字全字匹配）。
名字会改、会重名，QQ 昵称还带空格——`strip_leading_mention` / `check_mention` 那一堆"全字匹配、左括号不算边界"
的补丁，全是名字匹配的代价。

**统一约定**：`<@!id>`（平台内 = `users.id`，通道侧 = openid）。与 QQ 的内联 @ 同形，
所以同一条正文在通道出口只是"换个壳里的 id"。`@all`/`@ai` 仍是特殊标记。

**四层各管一段（单一来源）**：
1. 编解码住 `utils/text.py`（`_MENTION_STOP` 字符集本来就住那儿）：`mention_token` / `iter_mention_ids` /
   `mentions_user` / `link_mentions`（入口）/ `render_mentions`（出口）/ `take_trailing_msg_id`。
2. **入口归一**在 `chat/gm.py:send_gm_message`（人的手打、QQ 入站、工具调用、世界桥都经这里）：
   `@名字` → `<@!id>` 一次成型，之后全链路只认 id。只做群聊（私信没有 @ 这回事）。
   名字带空格按"全名 → 第一个词"两档认；**同一个写法指向两个人时弃用**（宁可留原文也不 @ 错人）；
   认不出的名字原样保留。
3. **识别兼容**：`check_mention(content, name, id)` 同时认新令牌与旧名字（历史消息、人的手打还在用名字；
   世界群是 mention_only，认不出就等于叫不醒）。四个判定点：`response_worker`（主唤醒 + 群助手按名字）、
   `group_delivery`（分发/离线暂存）、`agent_service`（回复场景）；未读小红点的 SQL 也加了令牌分支。
   群助手（`group_assistants`）没有 users 行 → 只能按名字认，入口也不会把它的名字归一（代码里写明）。
4. **展示**：前端 `utils/mentions.ts` 把 `<@!id>` 换成当前名字（改名自动跟着变），ChatView 传成员表；
   认不出的 id 原样留着（不编名字、也不新增文案）。旧消息不做迁移。

**通道出口真 @**：`channel_contacts`（`channel_user.py`，锚点邮箱约定收进 `anchor_email`）给出
"这条通道上认得的本地账号 → openid"；QQ 官方发 `<@!openid>`、NapCat 发 `[CQ:at,qq=…]`；
认不出的退 `@名字`（令牌原样发出去只会是乱码）。查库放在后台任务里，不拖慢发消息。
真机实测（2026-09-25 用户截图）：正文里的 `<@!openid>` 在群里渲染成**可点**的 @。

**AI 侧**：`send_gm` 说明改成"要 @ 谁就写 `<@!对方的id>`，id 见每条消息说话人后面的 `（id=N）`"（上下文本来就带 id）。
顺手收口另一件：`format_message` 给 AI 看的 `[msg_id=…]` 标记被模型当"回复语法"抄进正文
（私信实测 4 条、最早 2026-08-09、**与 QQ 无关**）→ 入口收掉并当成 `reply_to`。

**引用（回复）的现状与文档证据**（用户 2026-09-26 问"QQ 回复能不能也带引用"）：
- **出站**：发送群聊消息的请求体有 `message_reference`（官方原文「引用回复。填写后以引用形式展示，关联上下文」），
  其 `message_id`（`REFIDX_xxxxxx`）来源：群里人的消息 → 事件 `message_scene.ext.msg_idx`；
  机器人自己的消息 → 发消息响应的 `ext_info.ref_idx`。**我们现在没传它**，只用 `msg_id` 被动回复
  （QQ 自己挂在触发消息上显示成"回复某条"）。
- **入站**（对方引用的是哪条）：全量模式 `GROUP_MESSAGE_CREATE` 才有——`msg_elements` 的
  `message_type=103`（引用消息，带被引内容快照）与 `message_scene.ext.ref_msg_idx`；
  同一份文档里还有 **`mentions`（消息中@的用户列表）**，即 ④ 的入站那半在**全量模式**下可解。
- **@机器人模式的载荷里已经有 `message_scene`**（实测 8 字段含它），但我们从没读内容 → 很可能不用开全量权限。
  已加一行只打键名的日志（`scene_ext键` / `msg_idx 有/无` / `ref_msg_idx 有/无`；`auth_token` 的值不进日志），
  **待你在 QQ 里引用一条消息再 @ 一次**即可判定。
- 若要"出站精准引用某条"，得给每条 QQ 来消息记住 REFIDX（新列或小表 + 迁移）——**动库，等拍板**。

### 消息撤回（2026-09-26 落地）

**语义（用户定）**：撤回**不是**"把那条从历史上抹掉"——账本段内只追加，AI 上下文里那句删不掉。
所以撤回 = ① 站内标记（原文留库、任何渲染都不显示）+ ② 给**已经看过这条**的 AI 补一条「这条已作废」通知
（`revoked_notice`，**不重复被撤内容**）+ ③ 群里那条变占位 + ④ 通道侧一起撤。

- **唯一入口** `app/chat/revoke.py`：`revoke_group_message` / `revoke_dm_message`（同形）；
  窗口 **2 分钟**（与 QQ 一致，用户定）；自己发的都能撤，群主/管理员能撤别人的；幂等（撤过就拒）。
- **渲染**：撤回之后才进历史的一律只给 `revoked_text()` 占位（渲染层看 `revoked_at`，不额外判断）；
  撤回前已进历史的那些条目按设计**不动**——由那条通知兜底。
- **接口**：`POST /gm/{gid}/messages/{id}/revoke`、`POST /dm/{sid}/messages/{id}/revoke`；
  WS 广播 `message_revoked`；前端气泡变「{名字} 撤回了一条消息」，本人消息在窗口内出现「撤回」按钮。
- **AI 工具** `recall_message`：只能撤自己的（is_admin 恒 False，哪怕它是群主 AI）。
- **通道联动**：出站注册表加 **revoke 出口**（与发消息相反：**等**每个通道回答，因为要把"撤没撤掉"
  如实告诉用户）；QQ `DELETE /v2/groups/{openid}/messages/{id}`（官方：超 2 分钟不可撤；
  机器人是群管理员时还能撤普通成员的消息）；结果进返回值 `channel`，前端提示条显示半成功。
  为它加了 `messages.channel_msg_id`：入站存事件 `d.id`、出站存发消息响应里的 id（另开 session 回写）。
- **已知不做**：QQ 侧"用户撤回"的事件官方没有（实测也收不到）→ 平台这边那条**不会**跟着变；
  这条通道限制要写进文案（并入 ①）。

### 待落地

**本轮已完成（2026-09-26）**：① 通道文案（`group_brief` 讲清「@其他成员的内容不转发」与「撤回不同步」）；
④ 通道自测（QQ 内联 @ 实测可点）、入站引用实测可拿（@模式事件就带 `ref_msg_idx`）、全量模式兼容、
出站精准引用（`message_reference` + `channel_ref_idx`）；消息撤回（人 + AI，含账本作废通知）；
@ 提及统一 id；长消息折叠 2048；会话预览/导出等次要渲染面换名字；前端消息操作菜单（回复/复制/撤回）；
QQ 入站把正文里的原始提及 `<@openid>` 摘掉（曾漏进正文，AI 只看到一串 openid）、
出站摘开头 @ 按「这条是不是点名进来的」分场景、群推送模式（全量开关）靠事件类型观测，
通道说明随模式换文案 + 模式翻转给账本投一条「通道变更」通知；
出站摘开头 @ 改成按**事件类型**判（只有 @ 事件的回复腾讯才自带 @对方，全量事件的不摘）、
纯文本模式 @ 退成 @名字（真机自测：腾讯的纯文本消息不渲染内联 @，带 ! 与不带 ! 都显示成尖括号）。

**待办**：

1. **NapCat 侧补齐**：通道撤回、引用映射、通道自测（协议端有能力，尚未接）。
2. **次要渲染面收尾**：DSH 桥接页与群视界若直接显示群消息正文，仍可能露出 `<@!id>` 令牌；
   私信里若 AI 写了令牌，前端没有成员表可查（需要一次全局用户名兜底）。
3. **消息操作菜单的边界与可达性**：列表最底部可能被滚动容器裁掉（改成向上弹）；
   触屏与键盘没有 hover（群视界那边有 `focus-within` + `@media(hover:none)`，消息菜单还没跟上）。
4. **全量模式真机**：**开**这一档已验（每条都进、@ 机器人的消息也只来 `GROUP_MESSAGE_CREATE` 一条、
   正文完整、模式翻转有账本通知）；**关**（@ 模式）还没真机跑过——需要群主在 QQ 群设置里关掉
   「获取群内全部消息」再 @ 一次，看事件变回 `GROUP_AT_MESSAGE_CREATE`、正文/唤醒/出站都对。
   另外：开了全量后"两种事件各来一次"实测不成立，"按消息 id 去重"只是切换开关期间的兜底。
   **纯文本 vs Markdown** 也已用通道自测问清（三种写法并排）：纯文本不渲染内联 @，已在出站退成 @名字；
   要真 @ 就得把正文格式设成默认或 Markdown（agent-24 现在配置是 plain，所以它的 @ 是名字形态）。
5. **全量模式下没点名也会被叫醒（挂起，用户 2026-09-26 定）**：全量事件送进来的普通消息走主站同一条
   投递链路，唤醒与否由**意愿模型**决定（实测：基础分 50 + 安静加成 → `decision=reply`），
   所以"只有点名才叫醒"目前**不成立**。两条路待定：做 mirror-only 闸（没点名只入库、不进模型），
   或只在文档/通道文案里如实说明。**定了之后才能写"只有点名才会叫醒你"这类话。**
6. **不可为**：QQ 里别人撤回，官方没有这个事件（实测也收不到）→ 平台这边不跟着变（已写进通道文案）。
7. **第三批**：世界 AI（新增同名 `end_turn` + 现有强制收尾轮并入 + 历史走同一套服务）。
8. **第四批**：两级压缩（确定性修剪器 + 摘要模板：关键想法 / 必留项 / 裁剪优先级）+
   用 `cached_tokens` 标定三档数值 + 图片不降级。
9. **第五批**：老会话迁移（状态栈 + 群视界历史一次性拆进历史 + 投递变更通知）+ 全量验证与文档收尾。
