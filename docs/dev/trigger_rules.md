# 触发组合规则（trigger rules）

一条规则 = **条件组合 + 动作 + 作用域**。三种来源共用一套语义与一个引擎：
插件在工具类上声明 `triggers`、平台内置规则、AI 自己写的决策技能（后者见"迁移"一节）。

## 规则形状

    {"id": "vendor.my_tool.first_use",
     "when": {"event": "tool_result", "conditions": <条件树>},
     "do": {"action": "deliver", "text": "..."},
     "scope": "frame"}

## 条件：一种规范写法

    {"field": "tool", "op": "in", "value": ["web_search", "web_fetch"]}
    {"field": "content", "op": "contains", "value": "签到"}
    {"op": "vendor.is_admin", "value": true}          # 判词不需要 field
    {"and": [...]} / {"or": [...]} / {"not": {...}}    # 任意嵌套

内置运算：eq / ne / contains / starts_with / matches / gt / gte / lt / lte / in。

**历史写法**（仅为兼容库里已有的决策技能规则，不再新增用法）：`{"字段": 值}`、
`{"字段_contains": 值}` 这类下划线后缀形式，只支持内置运算。

### 工具事件的 ctx 字段

| 字段 | 含义 |
|---|---|
| `tool` | 工具名 |
| `ok` | 这次成功没有 |
| `calls_in_frame` | **本帧内**第几次调用（含本次） |
| `first_in_frame` | 是不是本帧内第一次 |
| `agent_id` | 哪个 AI |

字段名带 `_in_frame` 是有意的：计数挂在状态帧上，compact/clear 后帧重建即归零——
"本会话第几次"现在做不到，别按那个语义写规则。

## 动作

- `deliver`：把 `text` 投进本次工具返回（模型看得到）
- `silent`：吞掉本次结果
- 想要别的？`register_action("vendor.name", fn)`，fn(ctx, do, result) 的返回值**只落在
  `result["_trigger"]["vendor.name"]` 下**——自定义动作覆盖不了工具自己的字段（success / url / 结果本体）

## 作用域（scope，投几次）

| 值 | 语义 |
|---|---|
| `frame`（默认） | 当前状态帧内投一次；compact/clear 后帧重建 → 会再投一次 |
| `always` | 每次命中都投 |

`session` / `agent` / `version` **没有实现**：状态目前只挂在状态帧上，写这几个值会被校验当场拒绝，
不假装支持。（要"这个会话只讲一次"，得先把投递进度挂到比帧更长的生命周期上。）

## 信任边界与命名空间

| 来源 | 能注册扩展吗 | 能用哪些动作 |
|---|---|---|
| 平台内置 | 能，名字可不必带命名空间 | 全部 |
| 插件 | 能，扩展名**必须带命名空间**（`vendor.xxx`） | 全部 |
| AI 自写的规则 | 不能注册 | 只有 `deliver`（`silent` 会吞结果，不给） |

平台已认领的名字，插件注册会被忽略并留 warning（不抛错、不覆盖）。注册表与求值器都在
`utils/pure/conditions.py`（求值）与 `utils/pure/trigger_rules.py`（规则/动作）。

## 校验与可观测

`validate_trigger(rule, source=...)` 当场拒绝并说清原因：事件名、条件形状、动作是否存在、
`deliver` 缺 text、scope 取值、AI 越权用动作。条件另有规模闸：

- 嵌套 ≤ 8 层、节点 ≤ 64 个（超了整棵树判为不命中，且**不会被 `not` 反转成命中**）
- `matches` 的正则长度 ≤ 200 字、被匹配文本先截断到 2000 字（`_matches` 是唯一能写出灾难性回溯的运算）

**不给自定义判词加超时**：Python 同步调用没法安全中断，硬做要上线程或信号，代价比收益大。
改为约定无副作用 + 传 ctx 快照（判词改不动调用方的 ctx）+ 异常只算不命中并记 debug。

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

## 迁移计划

| 现有机制 | 收敛方式 | 状态 |
|---|---|---|
| 跨状态便签（40 次调用内投一次） | 条件 `{"field": "calls_total", "op": "lte", "value": 40}` + `scope: frame` | 未开始（`calls_total` 这个字段还没提供，**暂不依赖**） |
| 能力变更通知（按版本投一次） | `scope: version` | 未实现（scope 里没有这个值），继续用原机制 |
| 决策技能命中提示 | 决策引擎的动作之一，条件求值同源（已共用 `match_conditions`） | 已完成一半：求值器已共用；动作仍在决策层 |
| AI 自写工具事件规则 | 决策技能情景表加入 `tool_call` / `tool_result` | 未开始 |

渐进收敛：新东西直接接这套规则，旧机制按各自节奏改，不做一次性大改。

## 验证

    docker exec ai_group_backend bash -c 'export TEST_DATABASE_URL=...; cd /app \
      && python tests/run_without_pytest.py test_trigger_rules'

覆盖：规范/历史两种条件写法与自定义运算、扩展命名空间与平台优先、规模与正则闸、
动作权限（AI 只能用 deliver）、scope 校验、targets_of 筛空、explain 逐条原因、
帧内只投一次且换帧再投、自定义动作落在 `_trigger` 里且覆盖不了结果字段、无规则工具零开销。
