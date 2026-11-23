# 供应商预设（provider_presets）的形状契约

## 它在哪

`app/utils/pure/provider_presets.py` —— **pure 层**：只有数据与纯函数，不 import 任何
`app.services` / `app.models` / `app.routers`。`provider_config.py`（同样是 pure 层）
可以直接依赖它，不存在反向依赖。

数据演进（改模型、加厂商、下架档位）全在这一个文件里；库里的供应商配置是它的镜像，
启动时由 `provider_bootstrap` / `model_upgrade` 对齐。

## 唯一形状

对外只有两个访问器：

```python
get_preset(key) -> ProviderPreset | None      # 一个
get_all_presets(kind="llm") -> list[...]      # 一批（kind="*" 取全部）
```

两者都过 `_normalized()`，**返回同一份完整形状**：缺的键补空（`""`、`[]`、`False`），
因此「拿到预设就直接下标」是一条可以依赖的约定。

## 为什么要写这份契约

2026-11-23 插件商城保存 `llama.cpp` / `lmstudio` / `vllm` 预设必 500
（`KeyError: 'work_model'`）。根因不是"漏了两个键"，而是：

- `get_preset` 返回**原始** dict（这三家本地预设只写了 `chat_model`）
- `get_all_presets` 返回**归一化** dict
- 消费方（`admin.py save_provider`）按「字段齐全」直索引

同一个语义、两个入口、两种健壮性。归一化搬进访问器这一层之后，两个入口不会再分叉，
所有消费方（含未来新增的）自动安全。

## 规则

- 遍历预设一律 `get_all_presets(kind)`，**不要**直接迭代 `PRESETS.values()`（拿不到归一化）
- 新增预设字段时只改 `_normalized()` 一处，消费方自动拿到
- 新增厂商时字段可缺（`NotRequired`），归一化会补空——不需要为了不崩而把每个键都写一遍

## 取舍：为什么是一个「胖」形状，而不是两个 TypedDict

LLM 预设也会带 `voice_id` / `voices` / `style_hint`（恒为空），语音预设也会带
`chat_model` 系列（恒为空）。理论上可以拆成 `LlmPreset` / `VoicePreset` 两个 TypedDict，
按 `kind` 收窄。

**没有这么做**：Python 运行时不会执行类型收窄，拆开只会让每个消费方多一层
`if kind == ...`，而"一个形状、谁都能直索引"正是这次修复的收益。代价是几个恒空字段，
可接受。真要收紧，应该上静态类型检查（见 ROADMAP 里的 ruff/pyright 项），而不是拆形状。

## 回归测试

`tests/test_provider_presets_contract.py`：字段完整性（参数化覆盖每个预设）、
两访问器同形、三家本地预设可直索引（#4 的回归）、kind 过滤。

`tests/test_api_probe.py` 里还有一条间接保护：探针的模型名取自预设
（`_preset_chat_model`），预设与探针必须对得上。
