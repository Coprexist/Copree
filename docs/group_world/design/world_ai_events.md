# 世界事件与 AI 的通道（World Event → AI）

> 状态：**已实现**（2026-09-26）。产品要求：世界的事件由群视界提供并发送给 AI，群视界为此提供规范。
> 实现：`services/world/world_ai_events.py`（平台侧唯一入口）、世界工具 `emit_ai_event`、
> 受控 API `POST /world/{id}/api/ai_event`；用例 `backend/tests/test_world_ai_events.py`。

## 1. 定位

三条现有通道各管一段，缺的是"世界 → AI"这条：

| 通道 | 方向 | 现状 |
|------|------|------|
| 群消息 → 世界程序 | 群 → 世界 | 已落地（`world_event_hook`，首条立即 + 合并窗口） |
| 群消息 → AI 本体 | 群 → AI | 已落地（决策技能 + 唤醒链） |
| **世界事件 → AI** | 世界 → AI | **本文定义** |

用途：世界程序在自家逻辑里发生的事（补货、结算、天气变了、某个玩家触发了剧情）要能
主动通知某些 AI，让它们按自己写的决策技能处理（该说话说话、该记账记账），而不是靠 AI 轮询。

## 2. 事件契约（世界侧必须按此发）

```json
{
  "name": "shop_restock",
  "title": "商店补货了",
  "payload": {"item": "面包", "count": 12},
  "group_id": 5,
  "targets": {"kind": "group", "ids": [5]},
  "wake": false
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 世界自定的事件名，稳定标识（规则按它匹配），`[a-z0-9_]`，≤ 40 字符 |
| `title` | 是 | 人话标题（给 AI 看），≤ 60 字符 |
| `payload` | 否 | 世界自定字段（JSON 对象，≤ 4KB，扁平化后供规则条件引用：`payload_item` 等） |
| `group_id` | 否 | 兜底回复群：没指明群的收件人（直接点名的 AI）用它回话；只给这一个字段时理解为"发给这个群的 AI" |
| `targets` | 否 | 收件人，**由世界决定**：`{"groups": {"ids": [...], "types": ["类型slug"]}, "ais": {"ids": [...], "types": ["类型slug"]}}`；不传 = 发给本世界绑定的所有群里的 AI（一个群都没绑则退回世界居民 AI） |
| 唤醒 | — | 事件契约里没有 `wake` 字段：**未命中规则一律唤醒本体**（用户 2026-09-26 定，让 AI 能改自己的规则并保证有回复） |

事件名与 `title`/`payload` 的形状由世界自己负责；平台只校验上表的类型与上限。
`payload` 会**展平**成 `payload_字段名` 供规则条件引用（DSL 只认扁平字段），同时也以
`payload` 对象原样进上下文。

## 3. 投递语义

1. 事件到达 → 对该批收件人逐一过决策技能（情景 `world_event`，ctx = `name/title/world_id/group_id` + payload 展平）。
2. 命中且 `notify=false` → 执行 do（`reply_template` 代发到 `group_id`；`call_tool`/`run_script` 同现有分派），**不唤醒**。
3. 命中且 `notify=true` → 执行 do，唤醒本体并把结果作为提示注入（同群消息链路的 `note`）。
4. 未命中：**唤醒本体**（与好友申请、闹钟同一条独立唤醒链路），把事件标题、payload 与
   "要不要以后自动处理"的提示一起给它；它可以直接写一条 `world_event` 规则下次自动跑。
5. 事件不单独落库（审计日志与用量记账仍留痕）；`notify=true` 命中时的执行结果随唤醒提示一起给它。

## 4. 世界侧怎么发

- 受控 API：`POST /world/{id}/api/ai_event`，用世界自己的 `WORLD_API_TOKEN`（与现有受控 API 同一鉴权），
  body 即 §2 契约。属于写操作，只读运行（计划模式）下返回 403。
- 世界工具（给世界 AI 用）：`emit_ai_event(name, title, payload?, group_id?, targets?, wake?)`。
- 常驻世界与临时沙箱两条路径共用同一入口（`world_resident` 里的世界进程也走 HTTP，不另开协议）。

## 5. 限额与防循环

- 每世界每分钟事件条数上限（默认 60，`worlds.config.event_per_minute` 可配），超限丢弃并记日志；
- `payload` ≤ 4KB、`title` ≤ 60 字符、`name` ≤ 40 字符；
- 世界事件触发的回复一律 `source="world"`（与现有 '世界程序自己发的消息不触发' 同一套），
  不会因为"AI 回复了事件"而反过来再喂世界；
- 同一 `(world_id, name, payload)` 在 1 秒内重复投递只算一次（幂等，防世界程序循环里手滑）。

## 6. 与决策层的关系

- 情景表新增一行：`world_event`，字段 `name / title / world_id / group_id / payload_*`。
- AI 侧工具文案只多一句"世界可以给你发事件"，规则写法不变；
- 主体仍然是 **AI 个体**（居民 AI / 群助手），与 §1 的定位一致——世界只负责"发生什么"，
  怎么反应由每个 AI 自己的规则决定。

## 7. 决策记录（用户 2026-09-26 定）

1. **好友申请的规则可以带话**：`reply_template` 允许；话只在"通过申请"之后发得出去
   （那时两人已是好友），拒绝时发不出（AI 主动私信生人会被拒），日志如实记录。
2. **世界事件一律叫醒**：不设 `wake` 开关——叫醒才能让 AI 改自己的规则去适配，也保证它有回复。
3. **怎么发由视界决定**：`targets` 支持按 id 与按**群类型 / AI 类型**选收件人
   （类型定义复用 `group_types.json`：`world_bindings.group_type_slug` /
   `world_agents.group_type_slug`）；不传则默认本世界绑定群里的 AI。
4. **不单独落库**：事件投递完即结束，需要回看时以审计日志与用量记账为准（后续要界面回放再单独设计）。
