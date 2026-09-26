# 触发组合规则（trigger rules）

一条规则 = **条件组合 + 动作 + 作用域**。三种来源（插件声明、平台内置、AI 自写的决策技能）
共用同一套**条件与动作语义**、同一个求值器与执行入口。

说清楚边界：共用的是语义与引擎，**投递状态目前只支持 frame / always 两种**——
能力变更通知（按版本投递）仍是独立路径，收敛见"迁移计划"。

## 规则形状

    {"id": "vendor.my_tool.first_use",
     "when": {"event": "tool_result", "conditions": <条件树>},
     "do": {"action": "deliver", "text": "..."},
     "scope": "frame"}

## 条件：一种规范写法

    {"field": "tool", "op": "in", "value": ["web_search", "web_fetch"]}
    {"field": "result_text", "op": "contains", "value": "0 条"}
    {"op": "vendor.is_admin", "value": true}          # 判词不需要 field
    {"and": [...]} / {"or": [...]} / {"not": {...}}    # 任意嵌套

内置运算：eq / ne / contains / starts_with / matches / gt / gte / lt / lte / in。

**判词的读法（唯一一处容易写错的地方，这里定死）**：判词是**取值器**，只拿 ctx、返回一个值；
`value` 是**断言值**，引擎比较"判词算出来的值 == value"。

    register_predicate("vendor.level", lambda ctx: 3)      # 只收 ctx，不收入参
    {"op": "$vendor.level", "value": 3}                   # 合法：断言它等于 3
    {"op": "$vendor.is_admin", "value": false}            # 合法：断言它不是管理员

与字段叶子（`{"field": ..., "value": ...}`）读法一致——value 永远是"期望值"，不是"入参"。

### 工具事件的 ctx 字段

| 字段 | 含义 |
|---|---|
| `tool` | 工具名 |
| `ok` | 这次成功没有 |
| `calls_in_frame` | **本帧内**第几次调用（含本次） |
| `first_in_frame` | 是不是本帧内第一次 |
| `result_text` | 本次结果的 JSON 文本，截断到 1000 字（按"这次搜出来什么"分支用） |
| `agent_id` | 哪个 AI |

字段名带 `_in_frame` 是有意的：计数挂状态帧，compact/clear 后帧重建即归零——
"本会话第几次"现在做不到，别按那个语义写规则。

## 动作

- `deliver`：把 `text` 投进本次工具返回（模型看得到）
- `silent`：吞掉本次结果
- 自定义：`register_action("vendor.name", fn)`，fn(ctx, do, result) 的返回值**只落在
  `result["_trigger"]["vendor.name"]`** 下——覆盖不了工具自己的字段（success / url / 结果本体）

`_trigger` **对模型可见**（它就是工具结果的一部分），所以里面只放"给模型看的补充信息"，
别塞机器内部状态。

## 作用域（scope，投几次）

| 值 | 语义 |
|---|---|
| `frame`（默认） | 当前状态帧内投一次；compact/clear 后帧重建 → 会再投一次 |
| `always` | 每次命中都投 |

`session` / `agent` / `version` **没有实现**：状态只挂在状态帧上，写这几个值会被校验当场拒绝。
（要"这个会话只讲一次"，得先把投递进度挂到比帧更长的生命周期上。）

## 信任边界与命名空间

| 来源 | 能注册扩展吗 | 能用哪些动作 |
|---|---|---|
| 平台内置 | 能，名字可不必带命名空间 | 全部 |
| 插件 | 能，扩展名**必须带命名空间**（`vendor.xxx`） | 全部（`silent` 也在内） |
| AI 自写的规则 | 不能注册 | `deliver`，加上注册方显式 `ai_allowed=True` 的动作 |

**为什么 `silent` 只给代码、不给 AI**：它会吞掉工具结果，等于让规则改写"AI 看到的世界"。
代码随发布走、要过评审；AI 写的规则若能用它，就能把自己的工具结果藏起来逃避纠错。

平台已认领的名字，插件注册会被忽略并留 warning（不抛错、不覆盖）。

**注册表生命周期**：进程级全局，目前后端不做热重载（uvicorn 无 --reload，插件随进程启动加载一次），
所以不存在"幽灵动作"。将来要热重载，用 `unregister_op / unregister_predicate / unregister_action`
按**来源**卸载——来源对不上不会动别人注册的东西。

## 校验与可观测

`validate_trigger(rule, source=...)` 当场拒绝并说清原因：事件名、条件形状、动作是否存在、
`deliver` 缺 text、scope 取值、AI 越权用动作。条件另有规模闸：

- 嵌套 ≤ 8 层、节点 ≤ 64 个（超了整棵树判为不命中，且**不会被 `not` 反转成命中**）
- `matches`：模式 ≤ 200 字、被匹配文本先截断到 1000 字，并且**拒绝已知的灾难形状**
  （嵌套量词 `(a+)+b`、反向引用 `\1`）

**残余风险与取舍（说清楚比假装安全好）**：形状闸只挡已知套路，不证明安全；Python 的 `re` 没有超时，
`re2`/`regex` 要引依赖（本机还装不了）。所以 `matches` 只适合写简单模式，复杂解析请用自定义运算
（`register_op` 里跑自己的代码，出错只算不命中）。

**不给自定义判词/运算加超时**：同步 Python 调用没法安全中断，硬做要上线程或信号，代价大于收益。
改为：约定无副作用 + 传 ctx 快照（改不动调用方的 ctx）+ 异常只算不命中并记 debug。

排查"规则怎么没生效"用 `trigger_service.explain_tool_result(db, agent_id, tool)`（dry-run，
不写状态不改结果），每条规则给出 `matched` 与原因：`event_mismatch` / `conditions_false` /
`already_delivered` / `matched`。

## 状态与热路径

- 状态两处，都在状态帧上：`tool_uses`（本帧每个工具调过几次）、`delivered`（本帧投过哪些规则）
- 规则全在内存；**没规则盯着的工具，调用时连状态都不读**（`targets_of` 先筛一眼）
- 入口只有一个：`ToolRegistry.dispatch` 在工具执行完过一遍，出错只记日志，不影响工具结果

## 第一个用例

web_search 的「先回复、再核实」不再常驻系统提示（不搜索时上下文保持干净），
改成规则在**本帧第一次搜完**投一次。规则声明就在 `WebSearch.triggers` 里。

## 历史写法什么时候删

条件还认 `{"字段_contains": 值}` 这类下划线后缀写法，只为兼容库里已有的决策技能规则
（AI 是通过那套学的，改语义会打脸它自己的旧规则）。

**删除判定条件**（不给日期，给标准）：库里 `agent_skills(skill_type='decision')` 与
`group_assistants.config['decision_rules']` 里再也读不到下划线后缀写法，且工具描述
（`rule_schema_desc`）已只教规范写法 → 就可以删掉 `conditions._LEGACY_SUFFIX` 那一段。
迁移本身还没开始（见下表）。

## 迁移计划

| 现有机制 | 收敛方式 | 状态 |
|---|---|---|
| 跨状态便签（40 次调用内投一次） | 需要 `calls_total`（跨帧计数）+ `scope: frame` | 未开始；`calls_total` 这个字段**还没提供**，暂不依赖 |
| 能力变更通知（按版本投一次） | `scope: version` | **未实现**（scope 里没有这个值），继续用原机制 |
| 决策技能命中提示 | 条件求值已同源（共用 `match_conditions`）；动作仍在决策层 | 一半 |
| AI 自写工具事件规则 | 决策技能情景表加入 `tool_call` / `tool_result` | 未开始 |

渐进收敛：新东西直接接这套规则，旧机制按各自节奏改，不做一次性大改。

## 验证

    docker exec ai_group_backend bash -c 'export TEST_DATABASE_URL=...; cd /app \
      && python tests/run_without_pytest.py test_trigger_rules'

覆盖：规范/历史两种条件写法、判词取值语义、自定义运算、扩展命名空间与平台优先、注册与卸载、
AI 动作闸（含 ai_allowed）、规模与灾难形状闸、scope 校验、targets_of 筛空、explain 逐条原因、
帧内只投一次且换帧再投、result_text 分支、自定义动作落在 `_trigger` 且覆盖不了结果字段、
无规则工具零开销。
