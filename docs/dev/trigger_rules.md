# 触发组合规则（trigger rules）

一条规则 = **条件组合 + 动作 + 投几次**。三种来源共用一套语义与一个引擎：
插件在工具类上声明 `triggers`、平台内置规则、AI 自己写的决策技能。

## 规则形状

    {"id": "...",
     "when": {"event": "tool_result", "conditions": {...}},
     "do": {"action": "deliver", "text": "..."},
     "once": "context"}

## 条件：用现成的，或自己手搓

- 组合：{"and": [...]} / {"or": [...]} / {"not": {...}}，随便套
- 内置叶子：{"字段": 值}、{"字段_contains": "..."}，以及 _starts_with / _matches / _gt / _gte / _lt / _lte
- 工具事件的 ctx 字段：tool / ok / tool_calls（本会话第几次）/ first / agent_id
- 自己手搓：register_op("_in", fn) 加运算（叶子写 {"tool_in": [...]}）；
  register_predicate("is_admin", fn) 加判词（叶子写 {"$is_admin": true}，判词拿得到整个 ctx）
- 求值器只有一份（utils/pure/conditions.py），决策技能也用它——AI 写决策技能与插件写规则是同一套条件语义
- 自定义实现抛异常只算该叶子不命中，不炸调用方

## 动作：同样两头都行

- 内置：deliver（把 text 投进本次工具返回）、silent（吞掉结果）
- 自己手搓：register_action("count_it", fn)，fn(ctx, do, result) 返回要合并进工具结果的 dict
- reply_template / call_tool / run_script 由决策引擎执行，不在这里

## 投几次（once）

- **context（默认）**：投一次。状态挂当前状态帧（delivered），compact/clear 后帧重建 → 自动再投一次
- never：每次命中都投
- agent / version 两个粒度待状态层扩展，写了会被校验拒绝（不假装支持）

## 状态与热路径

- 状态两处，都在状态帧上：tool_uses（本会话每个工具调过几次 → tool_calls / first 的来源）、
  delivered（本会话投过哪些规则）。挂帧上是有意的：compact 后帧重建，"本会话第一次"自然归零
- 规则全在内存（内置 + 插件声明）；**没规则盯着的工具，调用时连状态都不读**（targets_of 先筛一眼）
- 入口只有一个：ToolRegistry.dispatch 在工具执行完过一遍，出错只记日志，不影响工具结果

## 第一个用例

web_search 的「先回复、再核实」不再常驻系统提示（不搜索时上下文保持干净），
改成规则在**本会话第一次搜完**投一次。规则声明就在 WebSearch.triggers 里。

## 迁移计划

| 现有机制 | 收敛方式 |
|---|---|
| 跨状态便签（40 次调用内投一次） | 条件 {calls_total_lte: 40} + once=context |
| 能力变更通知（按版本投一次） | once=version（待状态层） |
| 决策技能命中提示 | 决策引擎的动作之一，条件求值同源 |

收敛是渐进的：先把新东西接到这套规则上，旧机制按其节奏改，不做一次性大改。

## 验证

    docker exec ai_group_backend bash -c 'export TEST_DATABASE_URL=...; cd /app \
      && python tests/run_without_pytest.py test_trigger_rules'

覆盖：条件组合与自定义运算/判词、规则校验（未知动作、缺 text、未支持的 once）、
targets_of 筛空、首次投递一次且换会话再投、自定义动作执行、无规则工具零开销。
