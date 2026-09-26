"""条件求值 —— 条件组合规则的唯一实现（纯函数，无 IO）。

决策技能与触发组合规则共用这一份：同一套语义、一处求值。

**规范写法（只有一种）**：

    {"field": "tool", "op": "in", "value": ["web_search", "web_fetch"]}
    {"field": "content", "op": "contains", "value": "签到"}
    {"op": "vendor.is_admin", "value": true}          # 判词不需要 field
    {"and": [...]} / {"or": [...]} / {"not": {...}}    # 组合，任意嵌套

内置运算名：eq / ne / contains / starts_with / matches / gt / gte / lt / lte / in
自定义运算与判词必须带命名空间（vendor.xxx），见 register_op / register_predicate。

**历史写法（仅为兼容库里已有的决策技能规则，不再新增用法）**：
{"字段": 值} 与 {"字段_contains": 值} 这类下划线后缀形式，只支持内置运算。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

PLATFORM = "platform"

# 内置运算：规范名 → 实现
BUILTIN_OPS: dict[str, object] = {
    "eq": lambda value, expect: value == expect,
    "ne": lambda value, expect: value != expect,
    "contains": lambda value, expect: str(expect) in str(value),
    "starts_with": lambda value, expect: str(value).startswith(str(expect)),
    "matches": None,          # 单列，见下面 _match（带长度闸，防 ReDoS）
    "gt": lambda value, expect: float(value) > float(expect),
    "gte": lambda value, expect: float(value) >= float(expect),
    "lt": lambda value, expect: float(value) < float(expect),
    "lte": lambda value, expect: float(value) <= float(expect),
    "in": lambda value, expect: value in expect,
}

# 历史后缀 → 规范名
_LEGACY_SUFFIX = {
    "_starts_with": "starts_with",
    "_contains": "contains",
    "_matches": "matches",
    "_gte": "gte",
    "_gt": "gt",
    "_lte": "lte",
    "_lt": "lt",
}

# 规模闸：规则是人（或 AI）写的，不能让一条病态规则把一轮对话拖死
MAX_DEPTH = 8
MAX_NODES = 64
MAX_PATTERN = 200          # _matches 的模式长度上限
MAX_SUBJECT = 2000         # _matches 的被匹配文本上限（先截断再匹配）

_CUSTOM_OPS: dict[str, object] = {}
_CUSTOM_PREDICATES: dict[str, object] = {}
_OWNER: dict[str, str] = {}     # 名字 → 注册来源，用于"平台的不许插件覆盖"


def _ns_name(name: str, source: str, kind: str) -> str:
    """扩展名必须带命名空间；平台内置可以不带。"""
    key = str(name or "").strip()
    if not key:
        raise ValueError(f"{kind}名不能为空")
    if source != PLATFORM and "." not in key:
        raise ValueError(f"{kind}名「{key}」必须带命名空间（如 vendor.{key}），避免与平台内置撞名")
    return key


def _claim(name: str, source: str, kind: str) -> bool:
    """认领一个扩展名。已有更早的认领者且来源更权威时拒绝（返回 False）。"""
    owner = _OWNER.get(name)
    if owner is None:
        _OWNER[name] = source
        return True
    if owner == PLATFORM and source != PLATFORM:
        logger.warning("%s「%s」是平台内置，%s 的注册被忽略", kind, name, source)
        return False
    if owner != source:
        logger.warning("%s「%s」已被 %s 注册，%s 的注册被忽略", kind, name, owner, source)
        return False
    return True


def register_op(name: str, fn, *, source: str = "plugin") -> None:
    """登记一个运算。leaf 写成 {"field": ..., "op": name, "value": ...}。"""
    key = _ns_name(name, source, "运算")
    if key in BUILTIN_OPS:
        raise ValueError(f"运算名「{key}」是内置名，换个带命名空间的名字")
    if _claim(key, source, "运算"):
        _CUSTOM_OPS[key] = fn


def register_predicate(name: str, fn, *, source: str = "plugin") -> None:
    """登记一个判词（拿整个 ctx 快照做判断，必须无副作用）。leaf 写成 {"op": name, "value": ...}。"""
    key = _ns_name(name, source, "判词")
    if _claim(key, source, "判词"):
        _CUSTOM_PREDICATES[key] = fn


def op_names() -> tuple[str, ...]:
    return tuple(BUILTIN_OPS) + tuple(_CUSTOM_OPS)


def predicate_names() -> tuple[str, ...]:
    return tuple(_CUSTOM_PREDICATES)


def _match(value, expect) -> bool:
    """正则匹配：模式与被匹配文本都设上限——_matches 是唯一能被人写出灾难性回溯的运算。"""
    pattern = str(expect)
    if len(pattern) > MAX_PATTERN:
        logger.debug("正则过长（%d > %d），按不命中处理", len(pattern), MAX_PATTERN)
        return False
    return re.search(pattern, str(value)[:MAX_SUBJECT]) is not None


def apply_op(value, op: str | None, expect) -> bool:
    """求值一个运算。任何异常都只算不命中——一条规则不该炸掉整个工具调用。"""
    try:
        if not op or op == "eq":
            return value == expect
        if op in BUILTIN_OPS:
            fn = BUILTIN_OPS[op]
            return _match(value, expect) if fn is None else bool(fn(value, expect))
        custom = _CUSTOM_OPS.get(op)
        if custom is None:
            logger.debug("未注册的运算 %s，按不命中处理", op)
            return False
        return bool(custom(value, expect))
    except (TypeError, ValueError):
        return False
    except Exception as e:  # noqa: BLE001 —— 第三方实现出错不该外溢
        logger.debug("运算 %s 执行失败: %s", op, e)
        return False


def _leaf(key: str, expect, ctx: dict) -> bool:
    """历史写法的一个叶子：{"字段_运算": 值}。"""
    field = key
    op = "eq"
    for suffix, canonical in _LEGACY_SUFFIX.items():
        if key.endswith(suffix):
            field, op = key[: -len(suffix)], canonical
            break
    return apply_op(ctx.get(field), op, expect)


def _canonical_leaf(node: dict, ctx: dict) -> bool:
    """规范写法：{"field": ..., "op": ..., "value": ...}；op 带 $ 视为判词。"""
    op = str(node.get("op") or "").strip()
    if op.startswith("$"):
        fn = _CUSTOM_PREDICATES.get(op[1:])
        if fn is None:
            logger.debug("未注册的判词 %s，按不命中处理", op)
            return False
        try:
            return bool(fn(dict(ctx), node.get("value")))    # 传快照：判词改不动调用方的 ctx
        except Exception as e:  # noqa: BLE001
            logger.debug("判词 %s 执行失败: %s", op, e)
            return False
    return apply_op(ctx.get(node.get("field")), op or "eq", node.get("value"))


def validate_conditions(conditions, *, check_refs: bool = True,
                        _depth: int = 0, _count: list | None = None) -> tuple[bool, str]:
    """校验条件树：形状、规模、引用到的运算与判词是否已注册。

    规模闸与"引用存在性"都放在这里，写错当场说清楚，不留给运行期安静地不命中。
    """
    count = _count if _count is not None else [0]
    if not isinstance(conditions, dict) or not conditions:
        return False, "条件必须是对象"
    if _depth > MAX_DEPTH:
        return False, f"条件嵌套超过 {MAX_DEPTH} 层"
    count[0] += 1
    if count[0] > MAX_NODES:
        return False, f"条件节点超过 {MAX_NODES} 个"
    if "op" in conditions and isinstance(conditions.get("op"), str):
        op = conditions["op"].strip()
        if op.startswith("$"):
            if check_refs and op[1:] not in _CUSTOM_PREDICATES:
                return False, f"判词 {op} 未注册（可用：{list(predicate_names()) or '无'}）"
            return True, ""
        if check_refs and op not in BUILTIN_OPS and op not in _CUSTOM_OPS:
            return False, f"运算 {op} 未注册（可用：{list(op_names())}）"
        if op == "matches" and len(str(conditions.get("value"))) > MAX_PATTERN:
            return False, f"正则超过 {MAX_PATTERN} 字"
        return True, ""
    for key, value in conditions.items():
        if key in ("and", "or"):
            if not isinstance(value, list) or not value:
                return False, f"{key} 需要非空数组"
            for child in value:
                ok, err = validate_conditions(child, check_refs=check_refs,
                                              _depth=_depth + 1, _count=count)
                if not ok:
                    return False, err
            continue
        if key == "not":
            ok, err = validate_conditions(value, check_refs=check_refs,
                                          _depth=_depth + 1, _count=count)
            if not ok:
                return False, err
            continue
        op = "eq"
        for suffix, canonical in _LEGACY_SUFFIX.items():
            if str(key).endswith(suffix):
                op = canonical
                break
        if op == "matches" and len(str(value)) > MAX_PATTERN:
            return False, f"正则超过 {MAX_PATTERN} 字"
    return True, ""


class _TooBig(Exception):
    """条件树超规模。整棵树判为不命中——不能让它被 not 反转成"命中"。"""


def _eval(conditions, ctx: dict, depth: int, count: list) -> bool:
    if not isinstance(conditions, dict) or not conditions:
        return False
    if depth > MAX_DEPTH:
        raise _TooBig(f"嵌套超过 {MAX_DEPTH} 层")
    count[0] += 1
    if count[0] > MAX_NODES:
        raise _TooBig(f"节点超过 {MAX_NODES} 个")
    if "op" in conditions and isinstance(conditions.get("op"), str):
        return _canonical_leaf(conditions, ctx)
    if "and" in conditions:
        return all(_eval(c, ctx, depth + 1, count) for c in conditions["and"])
    if "or" in conditions:
        return any(_eval(c, ctx, depth + 1, count) for c in conditions["or"])
    if "not" in conditions:
        return not _eval(conditions["not"], ctx, depth + 1, count)
    return all(_leaf(str(key), expect, ctx) for key, expect in conditions.items())


def match_conditions(conditions, ctx: dict) -> bool:
    """递归求值条件树。形状不认识、规模超限、引用未注册一律算不命中（宁可少投，不乱投）。"""
    try:
        return _eval(conditions, ctx, 0, [0])
    except _TooBig as e:
        logger.debug("条件树超规模（%s），按不命中处理", e)
        return False
