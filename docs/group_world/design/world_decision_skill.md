# 世界 AI 决策技能与触发模式（Decision Skill & Trigger Mode）

> 状态：已落地。触发模式（阶段一）与决策技能（阶段二）均已实现；2026-09-26 起决策技能
> 不再要求绑定世界，改为平台通用（AI 的沙箱就是它自己的文件空间）。现行实现、情景表与
> 分派规则以 [决策层](../../dev/decision_layer.md) 为准，本文保留设计演化过程。
> 关联：`world_skill_design.md`（世界侧技能）、`world_agent_capabilities.md`（能力边界）、`capability_lazy_loading.md`（能力版本化）

## 1. 背景与目标

世界 AI（群 AI 本体）的每次响应都意味着一次 LLM 调用。但大量群事件是**常规的、可程序化处理的**：

- 有人问了一句不指向 AI 的话；
- 群里有人进进出出；
- 定时检查、状态同步、例行上报。

这些事件如果每次都唤醒 LLM 本体，成本高、响应慢、且让 AI 显得"什么都要插嘴"。

**目标**：事件先经过一层**决策层**——预置规则与 AI 自写的决策技能能处理的，由程序直接处理（不唤醒本体、不产生 LLM 成本）；只有决策层判定"关键时刻"（@AI、明确求助、规则匹配到需本体介入）才唤醒 LLM 本体。

```
群事件 / 世界事件
      │
      ▼
┌─────────────────┐   命中（程序化处理）   ┌──────────────┐
│   决策引擎       │ ───────────────────► │ 执行 do 动作   │（沙箱/工具/回复模板）
│ （触发模式 +    │                        └──────────────┘
│  决策技能匹配）  │
│                 │   未命中 / notify=true  ┌──────────────┐
└─────────────────┘ ─────────────────────► │ 唤醒 LLM 本体 │（现有对话/工具轮流程）
                                          └──────────────┘
```

## 2. 现状链路（2026-08-13 对照）

绑定群视界的群，一条群消息会走两条并行通道：

| 通道 | 入口 | 行为 | 是否唤醒 LLM |
|---|---|---|---|
| 世界程序感知 | `world_event_hook.notify_group_message` | **首条立即**喂给世界程序 `handle(event)`，随后 `worlds.config.group_trigger_interval`（默认 2s）窗口内的消息合并成一条再喂一次；处理与否由世界程序自决 | 否（沙箱内程序逻辑） |
| 群助手 LLM 触发 | `response_worker._process_group_event` | 群内 AI 成员（群助手 agent）逐一决策（`decide_action`）→ 是否 LLM 回复 | 是 |

问题：通道 2 对**每条**群消息都会执行决策（LLM 层判断），即便最终"不回复"也消耗了决策与上下文构建的开销；且默认行为是"所有成员都触发、AI 自主决定"，与"AI 不该一直触发"的定位不符。

## 3. 阶段一：触发模式（本阶段落地）

### 3.1 配置

- 键：`worlds.config.group_trigger_mode`
- 取值：
  - `mention_only`（**默认**）：群消息除非 @ 世界 AI（或其群助手名）/ @all / 群公告，否则**不唤醒 LLM 本体**
  - `all`：恢复原行为（所有消息都进入 AI 决策）
- 语义：`mention_only` 下，**世界程序感知通道（通道 1）不受影响**——事件仍喂给世界程序，这正是"决策程序代替 AI"的基础；被禁的只是 LLM 本体唤醒。
- 备注：群 AI（居民）与群助手为同一实体时，@ 判断按 `_check_mention(content, agent.name)` 与 @all/公告穿透。

### 3.2 配置来源与打包

- 运行期读取：`worlds.config`（DB，JSONB 自由键，无需迁移）。
- **随世界打包**：`export_zip` 附加 `world_meta.json`（含 `config` 快照，仅白名单键）；`import_zip` 读回并合并进 `worlds.config`。分享/发布世界时，触发模式跟着走。
- 优先级：导入的 `world_meta.json` 只在目标世界 config 缺失该键时写入（不覆盖宿主已有设置，避免恶意覆盖）。

### 3.3 AI 可改

- 世界 AI 工具集（`WORLD_TOOLS`）新增 `update_trigger_mode`：`mode: "mention_only" | "all"`。
- 调用门槛：AI 可自主调用（如世界设定"本世界安静，非请勿扰"时主动设为 `mention_only`；活跃世界改回 `all`）；群主/世界主人在前端设置界面亦可改（后续）。

### 3.4 拦截实现（response_worker）

在 `_process_group_event` 组装候选 AI 后、逐个 `_maybe_trigger_ai_reply` 前：

1. 查该群是否绑定群视界（`WorldBinding(entity_type="group", entity_id=group_id)`，与通道 1 同源）；
2. 有绑定 → 读世界 `config.group_trigger_mode`（缺省 `mention_only`）；
3. `mention_only` 且消息非 @ 该 AI、非 @all、非群公告 → 跳过该 AI 的 LLM 触发（不 claim、不决策）；
4. 通道 1（世界程序感知）照常执行。

性能：绑定查询每消息最多一次（进程内小缓存，随 world_id 失效）；无绑定的普通群零开销。

## 4. 阶段二：决策技能（Decision Skill）机制

> 设计定稿，实现待排期。阶段一（触发模式）是它的第一个内置决策规则的特例。

### 4.1 预置情景列表（Event）

平台维护一组标准化情景，每个情景带**结构化事件上下文**（决策技能可引用）：

| 情景 | 触发源 | 事件上下文（示例） |
|---|---|---|
| `group_message` | 群消息 | `{group_id, sender_id, sender_name, sender_type, content, is_mention, is_at_all, group_type}` |
| `member_join` | 成员入群 | `{group_id, member_id, member_name, group_type}` |
| `member_leave` | 成员退群 | `{group_id, member_id, member_name}` |
| `friend_request` | 好友申请 | `{applicant_id, applicant_name, message}` |
| `scheduled` | 定时触发 | `{cron_expr, last_fire_at}` |
| `world_event` | 世界程序事件 | `{event_type, payload}` |
| `command` | 世界命令 | `{command, args}` |

### 4.2 决策技能结构（Decision Skill）

在世界侧技能（`worlds/{id}/skills/`）体系上扩展新类型 `type: "decision"`：

```jsonc
{
  "type": "decision",
  "name": "quiet_group_auto_reply",
  "when": {
    "event": "group_message",
    "conditions": {                 // 条件树（DSL，递归组合，见下）
      "and": [
        { "is_mention": false },
        { "content_contains": ["签到", "打卡"] },
        { "not": { "content_contains": "停止" } }
      ]
    }
  },
  "do": {
    "action": "run_script",         // run_script / call_tool / reply_template
    "script": "auto_reply.py",      // 世界沙箱内脚本（sandbox_isolate）
    "reply": "已记录你的签到 ✅"
  },
  "notify": false                   // false = 程序化处理完即结束；true = 命中后仍唤醒本体
}
```

- `when`：事件类型 + 条件 DSL。
- `conditions` **逻辑自由组装**：递归条件树，节点支持：
  - `and: [cond, ...]` / `or: [cond, ...]`（数组内全部/任一满足）
  - `not: cond`（取反）
  - 叶子条件 = 字段运算（引用事件上下文字段）：
    - `"字段": 值` 简写（等于）
    - `"字段_contains": "子串"` / `"字段_starts_with": "前缀"` / `"字段_matches": "正则"`
    - `"字段_gt/lt/gte/lte": 数值`（比较）
    - 保留字段：`is_mention` / `is_at_all` / `sender_type` / `group_type` / `content` / `sender_id` 等
  - 例：`or: [{content_contains: "天气"}, {and: [{content_contains: "签到"}, {not: {is_mention: true}}]}]`
  - 进阶（后续）：表达式字符串模式（`(content contains '天气' or group_type == '冒险团') and not is_mention`），白名单解析器，供高级场景；初期以条件树为准。
- `do`：三选一——`run_script`（沙箱 Python，能力最全）/ `call_tool`（平台工具，如 `world_data_put`）/ `reply_template`（固定回复，零成本）。
- `notify`：关键语义——**"什么情景才触发我"**。`notify: true` 的情景命中后仍唤醒 LLM 本体（AI 声明"这种时候必须我来"）；`false` 则程序处理完即止。
- AI 自写：提供 `write_decision_skill` / `update_decision_skills` 工具，AI 自己生成、迭代自己的决策技能（走 `capability_versioning` 版本化，前缀缓存稳定）。

### 4.3 决策引擎（Decision Engine）

事件 → 按序匹配该 AI 的决策技能：

1. 遍历 `type="decision"` 的技能（能力版本化 effective 快照）；
2. `when` 命中 → 执行 `do`：
   - `notify=false` → 结束（记决策日志 + 用量统计）；
   - `notify=true` → 执行 `do` 后**继续唤醒本体**（带 `do` 结果上下文）；
3. 全部未命中 → 走阶段一触发模式判定（`group_trigger_mode`）→ 决定是否唤醒本体。

### 4.4 安全与防循环

- `do` 脚本走世界沙箱（复用 `sandbox_isolate`，资源配额沿用 worlds.config）；
- 决策技能执行有独立日志与限额（防死循环：技能触发的动作产生的消息不再反喂同技能，参照通道 1 的 `source="world"` 防循环）；
- 条件 DSL 白名单化，不开放任意表达式求值（阶段二初期）；沙箱脚本受技能审核与版本化约束。

## 5. 落地清单

阶段一（本次）：
- [x] 设计文档（本文件）
- [ ] `response_worker` 触发拦截（群绑定世界 + `group_trigger_mode` 判定）
- [ ] `world_tools.update_trigger_mode` 工具（AI 可改）
- [ ] `export_zip` / `import_zip` 附 `world_meta.json`（config 白名单快照随包）
- [ ] CHANGELOG 补记

阶段二（决策技能）——核心闭环已落地（2026-08-13）：
- [x] 预置情景列表（group_message 已接入；member_join/leave、friend_request、scheduled 引擎通用、事件钩子陆续接）
- [x] 决策技能模型（type=decision：when 条件 DSL + do 三动作 + notify）——存 agent_skills / group_assistants.config
- [x] 决策引擎（事件→技能匹配→程序化处理 or 唤醒 LLM；优先于 mention_only 触发模式）
- [x] `write_decision_skill` / `list_decision_skills` / `delete_decision_skill` 工具（ToolRegistry 插件，AI 自配置；群助手独立入口）
- [x] do 执行（reply_template 零成本 / call_tool 平台工具 / run_script 沙箱复用 skill_sandbox）
- [ ] 决策执行限额/日志（目前依赖世界沙箱配额，独立限额与审计待补）
- [ ] 其余情景事件钩子（member_join/leave、friend_request、scheduled）
