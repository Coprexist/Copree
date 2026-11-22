"""按 chat 模板适配 system 位置（app/utils/pure/chat_template.py）。纯函数。

对应事故：llama.cpp --jinja + Qwen3 接 Copree，每轮 500
`Jinja Exception: System message must be at the beginning.`

两条铁律（改测试前先读）：
1. `anywhere` 通道必须原对象返回 —— DeepSeek 的前缀缓存逐字节不变。
2. 动态块**不挪位置**，只改 role —— 挪了就把每轮变化的内容送到最前面，
   本地 ollama 实测缓存命中 0.21s / 不命中 39.45s。
"""
from app.utils.pure.chat_template import (
    ANYWHERE,
    GO,
    INJECTION_MARK,
    JINJA,
    LEADING,
    NONE,
    fit_system_layout,
    layout_from_template,
    normalize_layout,
)

# ---- 判定：取自真实 tokenizer_config.json 的形状 ---- #

QWEN_STRICT = (
    "{%- if messages[0].role == 'system' %}"
    "{{- '<|im_start|>system\\n' + messages[0].content + '<|im_end|>\\n' }}"
    "{%- endif %}"
    "{%- for message in messages %}"
    "{%- if message.role == 'system' and not loop.first %}"
    "{{- raise_exception('System message must be at the beginning.') }}"
    "{%- endif %}"
    "{%- if message.role == 'user' %}...{%- endif %}"
    "{%- endfor %}"
)

QWEN_SILENT = (
    "{%- if messages[0].role == 'system' %}"
    "{{- '<|im_start|>system\\n' + messages[0].content + '<|im_end|>\\n' }}"
    "{%- endif %}"
    "{%- for message in messages %}"
    "{%- if message.role == 'user' %}...{%- elif message.role == 'assistant' %}...{%- endif %}"
    "{%- endfor %}"
)

GEMMA2 = (
    "{{ bos_token }}"
    "{%- if messages[0]['role'] == 'system' %}"
    "{{- raise_exception('System role not supported') }}"
    "{%- endif %}"
    "{%- for message in messages %}{{- message['content'] }}{%- endfor %}"
)

GENERIC = (
    "{%- for message in messages %}"
    "{%- if message['role'] == 'system' %}<|im_start|>system\\n{{ message['content'] }}"
    "{%- elif message['role'] == 'user' %}<|im_start|>user\\n{{ message['content'] }}"
    "{%- endif %}{%- endfor %}"
)


def test_strict_template_requires_leading():
    """报错型：非首条 system 直接 raise（辰天遇到的 500）。"""
    assert layout_from_template(QWEN_STRICT) == LEADING


def test_template_without_system_support_is_none():
    """Gemma 2：根本不接受 system 角色。"""
    assert layout_from_template(GEMMA2) == NONE


def test_silently_truncating_template_is_leading():
    """只渲染 messages[0] 的 system、循环里没有 system 分支 —— 后续 system 会静默丢失。"""
    assert layout_from_template(QWEN_SILENT) == LEADING


def test_generic_template_stays_anywhere():
    """循环里能通用处理 system 的模板不受影响，必须保持 anywhere。"""
    assert layout_from_template(GENERIC) == ANYWHERE


def test_unknown_shapes_fall_back_to_anywhere():
    """宁可宽松：认不出、空串、None 都不许判成严格档。"""
    for template in (None, "", "   ", 123, b"bytes"):
        assert layout_from_template(template) == ANYWHERE, template


def test_template_without_any_system_role_is_none():
    """模板里连 system 这个角色都没有：system 无处可放，只能并进 user（不能弄丢）。"""
    for template in (
        "{{ messages }}",
        "<|endoftext|>{{ messages | join }}",
        "{% for m in messages %}{{ m.content }}{% endfor %}",
    ):
        assert layout_from_template(template) == NONE, template


# ---- ollama：Go 模板（不是 Jinja）---- #

OLLAMA_GO = (
    "{{- if .System }}{{ .System }}{{ end }}"
    "{{- range $i, $_ := .Messages }}"
    "{{- $last := eq (len (slice $.Messages $i)) 1}}"
    '{{- if eq .Role "user" }}<|User|>{{ .Content }}'
    '{{- else if eq .Role "assistant" }}<|Assistant|>{{ .Content }}'
    "{{- end }}{{- end }}"
)
OLLAMA_PROMPT_ONLY = "{{ .Prompt }}"
OLLAMA_WITH_SYSTEM_BRANCH = (
    "{{- if .System }}{{ .System }}{{ end }}"
    "{{- range .Messages }}"
    '{{- if eq .Role "system" }}[S]{{ .Content }}'
    '{{- else if eq .Role "user" }}[U]{{ .Content }}'
    "{{- end }}{{- end }}"
)


def test_ollama_template_is_leading():
    """ollama 只能渲染一个 .System（循环里不认 system），多的会被它自己合并到开头。

    我们判 leading 主动合并，它就没有可合并的东西，缓存断点留在原位。
    """
    assert layout_from_template(OLLAMA_GO, GO) == LEADING


def test_ollama_prompt_only_template_is_anywhere():
    """gemma4 那类 {{ .Prompt }}：没有角色概念，看不出限制，宁可宽松。"""
    assert layout_from_template(OLLAMA_PROMPT_ONLY, GO) == ANYWHERE


def test_ollama_template_with_system_branch_stays_anywhere():
    """循环里自己认 system 的模板能保留位置，不动它。"""
    assert layout_from_template(OLLAMA_WITH_SYSTEM_BRANCH, GO) == ANYWHERE


def test_dialect_defaults_to_jinja():
    """不传方言时按 llama.cpp 的 Jinja 判（默认路径不能悄悄变）。"""
    assert layout_from_template(QWEN_STRICT) == LEADING
    assert layout_from_template(QWEN_STRICT, JINJA) == LEADING


def test_layout_normalization():
    assert normalize_layout("leading") == LEADING
    assert normalize_layout(" LEADING ") == LEADING
    assert normalize_layout(NONE) == NONE
    assert normalize_layout("strict") == ANYWHERE
    assert normalize_layout(None) == ANYWHERE


# ---- 归一化：稳定块合并 / 动态块就地降级 ---- #

HEAD = {"role": "system", "content": "你是助手"}          # 稳定块（历史前）
ROSTER = {"role": "system", "content": "## 状态帧"}        # 稳定块（历史前）
HISTORY = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好，有什么可以帮你的"},
]
MEMORY = {"role": "system", "content": "【想起】记忆"}      # 动态块（历史后）
TAIL = {"role": "system", "content": "当前时间 10:00"}     # 动态块（历史后）


def test_anywhere_returns_same_object():
    """anywhere 必须原对象返回：DeepSeek 前缀缓存布局逐字节不变。"""
    messages = [HEAD, ROSTER, *HISTORY, MEMORY, TAIL]
    assert fit_system_layout(messages, ANYWHERE) is messages
    assert fit_system_layout(messages, "unknown-value") is messages


def test_leading_merges_stable_and_keeps_dynamic_in_place():
    """稳定块合并成一条 system；动态块**原地**变 user，位置不动。"""
    messages = [HEAD, ROSTER, *HISTORY, MEMORY, TAIL]
    result = fit_system_layout(messages, LEADING)

    assert result[0] == {"role": "system", "content": "你是助手\n\n## 状态帧"}
    assert result[1:3] == HISTORY, "历史段必须逐字节不变"
    assert result[3] == {"role": "user", "content": f"{INJECTION_MARK}【想起】记忆"}
    assert result[4] == {"role": "user", "content": f"{INJECTION_MARK}当前时间 10:00"}
    assert sum(1 for m in result if m["role"] == "system") == 1


def test_leading_is_idempotent_when_already_canonical():
    """已经是「一条稳定 system、没有动态块」时原对象返回，不重建。"""
    messages = [HEAD, *HISTORY]
    assert fit_system_layout(messages, LEADING) is messages


def test_none_folds_stable_into_first_user_and_demotes_dynamic():
    """Gemma 那类：稳定块并入第一条 user；动态块同样就地降级。"""
    messages = [HEAD, ROSTER, *HISTORY, MEMORY, TAIL]
    result = fit_system_layout(messages, NONE)

    assert result[0] == {"role": "user", "content": "你是助手\n\n## 状态帧\n\n你好"}
    assert result[1] == HISTORY[1]
    assert result[2] == {"role": "user", "content": f"{INJECTION_MARK}【想起】记忆"}
    assert not any(m["role"] == "system" for m in result)


def test_none_without_user_creates_one():
    """边界：一条 user 都没有时新建一条，不能把稳定块弄丢。"""
    result = fit_system_layout([HEAD, {"role": "assistant", "content": "在"}], NONE)
    assert result == [
        {"role": "user", "content": "你是助手"},
        {"role": "assistant", "content": "在"},
    ]


def test_no_system_messages_is_a_no_op():
    messages = list(HISTORY)
    assert fit_system_layout(messages, LEADING) is messages
    assert fit_system_layout([], NONE) == []


def test_no_stable_prefix_is_a_no_op():
    """开头就是对话（没有稳定块）：不动，避免把动态块往前提。"""
    messages = [*HISTORY, MEMORY]
    assert fit_system_layout(messages, LEADING) is messages


def test_input_messages_are_not_mutated():
    messages = [HEAD, ROSTER, *HISTORY, MEMORY, TAIL]
    snapshot = [dict(m) for m in messages]
    fit_system_layout(messages, NONE)
    assert messages == snapshot


def test_multimodal_content_gets_a_text_part():
    """content 是分片列表（多模态）时，标记变成最前面的 text 分片。"""
    result = fit_system_layout(
        [{"role": "system", "content": "看这张图"}, {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:x"}},
        ]}],
        NONE,
    )
    assert result[0]["content"][0] == {"type": "text", "text": "看这张图"}
    assert result[0]["content"][1]["type"] == "image_url"
