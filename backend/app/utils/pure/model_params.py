"""采样参数的常识 —— 唯一来源。

为什么单独成模块：同一个「温度」有四个读者——创建助手按字段说明决定填什么、用户拖滑块、
AI 通过 update_self_config 自改、文档里给人看。四处各写一份数字必然走形，于是出现 2026-10-18 那次：
创建助手把 temperature / topP / presencePenalty / frequencyPenalty 全拉到上限（2 / 1 / 2 / 2），
建出来的 AI 第一句话就是「打这么长的东西是真撑着问……」，用户以为它变傻了。

事实依据（DeepSeek API 文档「Temperature 设置」）：默认 1.0；代码/数学 0.0；数据抽取 1.0；
通用对话 1.3；翻译 1.3；创意写作 1.5。2.0 是分布尾部，长上下文里会明显跑偏。
top_p=1 表示不裁剪（原样用完整分布），叠上高温度等于双重放开。
presence/frequency penalty 到 2.0 时，模型被逼着回避刚出现过的词，只能挑越来越不相干的字，
输出退化成无意义字串——那次「变傻」的直接原因。

所以：API 允许的区间不是该填的区间。表单契约按安全区间收敛，字段说明直接写清后果。
"""

# key 用**表单字段名**（camelCase）：创建助手看的 FIELD_CATALOG 就是这个命名。
# update_self_config 那边是 snake_case，另有一份映射（见 SELF_CONFIG_NAMES）。
PARAM_SPECS: dict[str, dict] = {
    "temperature": {
        "what": "随机性：越高越发散，也越容易跑题、用怪词",
        "api": (0.0, 2.0),
        "safe": (0.2, 1.3),
        "default": 0.9,
        "why": "超过 1.3 在长上下文里明显跑偏，2.0 基本不可读（官方通用对话建议 1.3、创意写作才到 1.5）",
    },
    "topP": {
        "what": "核采样：只从概率最高的那一小撮候选里挑",
        "api": (0.0, 1.0),
        "safe": (0.5, 1.0),
        "default": 0.9,
        "why": "1.0 = 不裁剪；再叠高 temperature 等于把两个口子一起放大，通常留 0.9 起步",
    },
    "presencePenalty": {
        "what": "话题新鲜度：越高越倾向于说没说过的话题",
        "api": (-2.0, 2.0),
        "safe": (0.0, 1.0),
        "default": 0.5,
        "why": "1.5 以上开始为了躲重复而换生僻说法，2.0 会退化成不成句的字串",
    },
    "frequencyPenalty": {
        "what": "重复抑制：越高越不重复用同一个词",
        "api": (-2.0, 2.0),
        "safe": (0.0, 1.0),
        "default": 0.5,
        "why": "同 presence：拉到 2.0 会逼它不断换词，最后换出的词彼此不相干",
    },
}

# 表单字段名 → update_self_config 的参数名（AI 自改时用的是 snake_case）
SELF_CONFIG_NAMES = {
    "temperature": "temperature",
    "topP": "top_p",
    "presencePenalty": "presence_penalty",
    "frequencyPenalty": "frequency_penalty",
}


def safe_range(field: str) -> tuple[float, float]:
    return PARAM_SPECS[field]["safe"]


def describe(field: str) -> str:
    """字段说明（创建助手的 field_manual 与工具 schema 共用这一份）。"""
    spec = PARAM_SPECS[field]
    lo, hi = spec["safe"]
    return f"{spec['what']}；建议 {lo}~{hi}（默认 {spec['default']}）：{spec['why']}"


def clamp(field: str, value: float) -> float:
    """表单契约的收敛：落到安全区间内（API 允许更宽，但宽出来的部分只会让它说胡话）。"""
    lo, hi = safe_range(field)
    return max(lo, min(hi, float(value)))


def out_of_safe_range(field: str, value: float) -> bool:
    lo, hi = safe_range(field)
    return not (lo <= float(value) <= hi)
