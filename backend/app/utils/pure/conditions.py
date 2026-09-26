"""条件求值 —— 条件组合规则的唯一实现（纯函数，无 IO）。

决策技能与触发组合规则共用这一份：同一套语义、一处求值，AI 学一次就会写两种规则。

条件树结构：
- {"and": [cond...]} / {"or": [cond...]} 组合节点
- {"not": cond} 取反节点
- 叶子：{"字段": 值}（等于）、{"字段_运算": 值}（内置运算）、{"$判词": 值}（自定义判词）

两种扩展口，用现成的与自己手搓的都行：
- register_op(name, fn)：新增运算后缀，叶子写 {"字段_my_op": 值}
- register_predicate(name, fn)：新增判词，叶子写 {"$my_check": 值}；判词拿得到整个 ctx，
  适合"内置字段凑不出来"的判断（例如查库后的状态、插件自己的计数器）

约定：注册的东西优先于内置实现匹配，同名可覆盖（插件想改语义不必改平台代码）。
求值全程不抛异常——自定义实现出错只算该叶子不命中，不让一条规则炸掉整个工具调用。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

OPS = ("_starts_with", "_contains", "_matches", "_gte", "_gt", "_lte", "_lt")

# 自定义运算：{后缀: fn(value, expect) -> bool}
_CUSTOM_OPS: dict[str, object] = {}
# 自定义判词：{名字: fn(ctx, expect) -> bool}
_PREDICATES: dict[str, object] = {}


def register_op(name: str, fn) -> None:
    """登记一个运算后缀（带下划线，如 "_in"）。fn(value, expect) -> bool。"""
    key = str(name or "").strip()
    if not key:
        raise ValueError("运算名不能为空")
    _CUSTOM_OPS[key if key.startswith("_") else "_" + key] = fn


def register_predicate(name: str, fn) -> None:
    """登记一个判词。fn(ctx, expect) -> bool。"""
    key = str(name or "").strip()
    if not key:
        raise ValueError("判词名不能为空")
    _PREDICATES[key] = fn


def op_names() -> tuple[str, ...]:
    """全部可用运算（自定义优先）。校验报错时用来说清可选值。"""
    return tuple(_CUSTOM_OPS) + OPS


def predicate_names() -> tuple[str, ...]:
    return tuple(_PREDICATES)


def field_op(cond_key: str) -> tuple[str, str | None]:
    """叶子条件键拆成 (字段, 运算)。无运算后缀 = 等于。"""
    for op in op_names():
        if cond_key.endswith(op):
            return cond_key[: -len(op)], op
    return cond_key, None


def apply_op(value, op: str | None, expect) -> bool:
    try:
        if op is None:
            return value == expect
        custom = _CUSTOM_OPS.get(op)
        if custom is not None:
            return bool(custom(value, expect))
        if op == "_contains":
            return str(expect) in str(value)
        if op == "_starts_with":
            return str(value).startswith(str(expect))
        if op == "_matches":
            return re.search(str(expect), str(value)) is not None
        if op in ("_gt", "_gte", "_lt", "_lte"):
            v, e = float(value), float(expect)
            return {"_gt": v > e, "_gte": v >= e, "_lt": v < e, "_lte": v <= e}[op]
    except (TypeError, ValueError):
        return False
    except Exception as e:      # 自定义实现出错不该炸掉调用方
        logger.debug("条件运算失败 %s: %s", op, e)
        return False
    return False


def match_conditions(conditions, ctx: dict) -> bool:
    """递归求值条件树。空条件或不认识的形状都算不命中（宁可少投，不乱投）。"""
    if not isinstance(conditions, dict) or not conditions:
        return False
    if "and" in conditions:
        return all(match_conditions(c, ctx) for c in conditions["and"])
    if "or" in conditions:
        return any(match_conditions(c, ctx) for c in conditions["or"])
    if "not" in conditions:
        return not match_conditions(conditions["not"], ctx)
    # 叶子：单键（多键叶子按 and 处理）
    results = []
    for key, expect in conditions.items():
        name = str(key)
        if name.startswith("$"):
            fn = _PREDICATES.get(name[1:])
            if fn is None:
                logger.debug("未注册的判词 %s，按不命中处理", name)
                results.append(False)
                continue
            try:
                results.append(bool(fn(ctx, expect)))
            except Exception as e:
                logger.debug("判词 %s 执行失败: %s", name, e)
                results.append(False)
            continue
        field, op = field_op(name)
        results.append(apply_op(ctx.get(field), op, expect))
    return all(results)
