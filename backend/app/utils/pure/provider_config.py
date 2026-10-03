"""
供应商配置纯函数 —— 零 IO 依赖。

包含：查找默认供应商、按名称/URL 匹配、模型列表收集、thinking 判定。
"""
import json
from typing import Any


def find_default_provider(providers: list[dict]) -> dict | None:
    """从列表中查找默认供应商（is_default=True 的第一个，否则第一个）"""
    if not providers:
        return None
    default = next((p for p in providers if p.get("is_default")), None)
    return default or providers[0]


def find_provider_by_name(providers: list[dict], name: str) -> dict | None:
    """按 name 精确匹配供应商"""
    for p in providers:
        if p.get("name") == name:
            return p
    return None


def find_provider_by_base_url(providers: list[dict], base_url: str) -> dict | None:
    """按 base_url（去除末尾 /）匹配供应商"""
    target = (base_url or "").rstrip("/")
    if not target:
        return None
    for p in providers:
        if (p.get("base_url", "") or "").rstrip("/") == target:
            return p
    return None


def find_provider_for_pool_key(providers: list[dict], provider_name: str | None, base_url: str | None) -> dict | None:
    """
    给定池 Key 的 provider_name 和 api_base_url，找到最佳匹配的供应商。

    优先级：provider_name 精确匹配 → base_url 匹配 → 默认供应商 → None
    """
    # 1. 按 provider_name 匹配
    if provider_name:
        found = find_provider_by_name(providers, provider_name)
        if found:
            return found

    # 2. 按 base_url 匹配
    if base_url:
        found = find_provider_by_base_url(providers, base_url)
        if found:
            return found

    # 3. 回退到默认
    return find_default_provider(providers)


def collect_all_models(providers: list[dict]) -> list[dict]:
    """
    收集所有供应商的全部模型，每个模型附加 provider_name / provider_key。
    用于 /agents/models 端点返回。
    """
    models: list[dict] = []
    for p in providers:
        p_models = p.get("model_options") or []
        for m in p_models:
            if isinstance(m, dict):
                models.append({
                    **m,
                    "provider_name": p.get("name", p.get("provider", "?")),
                    "provider_key": p.get("provider", "unknown"),
                })
    return models


def build_provider_summaries(providers: list[dict]) -> list[dict]:
    """构建前端可用的供应商摘要列表"""
    from app.services.agent.provider_presets import get_preset

    result: list[dict] = []
    for p in providers:
        provider_key = p.get("provider", "unknown")
        # 从预设中获取 api_key_url（优先用配置中手动指定的，否则用预设的）
        api_key_url = p.get("api_key_url", "")
        if not api_key_url:
            preset = get_preset(provider_key)
            if preset:
                api_key_url = preset.get("api_key_url", "")

        result.append({
            "name": p.get("name", p.get("provider", "?")),
            "provider": provider_key,
            "base_url": p.get("base_url", ""),
            "api_key_url": api_key_url,
            "chat_model": p.get("chat_model", ""),
            "work_model": p.get("work_model", ""),
            "thinking_supported": p.get("thinking_supported", False),
            "is_default": p.get("is_default", False),
            "models": p.get("model_options", []),
        })
    return result


def get_thinking_supported(providers: list[dict], api_base_url: str | None = None) -> bool:
    """
    判断当前供应商是否支持深度推理。

    优先从默认供应商取；若提供 base_url 则从匹配的供应商取；
    都无则回退到 URL 域名检测。
    """
    if api_base_url:
        provider = find_provider_by_base_url(providers, api_base_url)
        if provider:
            return bool(provider.get("thinking_supported", False))

    default = find_default_provider(providers)
    if default:
        return bool(default.get("thinking_supported", False))

    # 最后回退：域名检测
    if api_base_url:
        return "deepseek.com" in api_base_url or "dashscope" in api_base_url
    return False


def get_default_models(providers: list[dict]) -> tuple[str, str]:
    """获取默认的 chat_model 和 work_model"""
    default = find_default_provider(providers)
    if default:
        return default.get("chat_model", ""), default.get("work_model", "")
    return "", ""


def normalize_legacy_config(raw: Any) -> list[dict]:
    """
    将旧版 provider_config 规范化为数组格式。

    旧格式：dict（单对象）→ 包装为数组
    新格式：list → 直接返回
    NULL/空 → 返回空数组
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        # 历史数据可能被双重编码过（JSONB 里存了一个字符串）：解一层再判类型
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        raw.setdefault("name", raw.get("provider", "default"))
        raw.setdefault("is_default", True)
        return [raw]
    return []


def build_provider_config(
    name: str,
    provider_key: str,
    base_url: str = "",
    chat_model: str = "",
    work_model: str = "",
    embedding_model: str = "",
    model_options: list[dict] | None = None,
    thinking_supported: bool = False,
    is_default: bool = False,
) -> dict:
    """构建一个规范化的供应商配置字典（纯函数）"""
    return {
        "name": name,
        "provider": provider_key,
        "base_url": base_url,
        "chat_model": chat_model,
        "work_model": work_model,
        "embedding_model": embedding_model,
        "model_options": model_options or [],
        "thinking_supported": thinking_supported,
        "is_default": is_default,
    }


def upsert_provider(providers: list[dict], config: dict, index: int | None = None) -> list[dict]:
    """
    在供应商列表中新增或更新一个供应商配置（纯函数，返回新列表）。

    - index 指定时：更新对应位置
    - index 为 None 时：按 name 查找更新，未找到则追加
    - config["is_default"]=True 时：取消其他供应商的默认标记
    """
    result = [dict(p) for p in providers]  # 浅拷贝

    if config.get("is_default"):
        for p in result:
            p["is_default"] = False

    if index is not None and 0 <= index < len(result):
        result[index] = config
        return result

    existing_idx = next((i for i, p in enumerate(result) if p.get("name") == config.get("name")), None)
    if existing_idx is not None:
        result[existing_idx] = config
    else:
        if not result:
            config["is_default"] = True
        result.append(config)

    return result


def align_provider_models(
    providers: list[dict],
    *,
    presets: dict[str, dict],
    aliases: dict[str, str],
    labels: dict[str, str] | None = None,
) -> tuple[list[dict], int]:
    """把供应商配置的模型三项对齐到现役预设，返回 (新列表, 改动处数)

    为什么是"对齐"而不只是"改名"：库里这份配置是预设清单在库里的镜像（管理台上看到的就是它），
    只做别名平升的话，新加的档位与换过的标签都进不去——代码更新了，界面还是旧的。
    只动 chat_model / work_model / model_options 三项：base_url、名称、密钥属于使用者的配置，
    预设无权覆盖；provider 键不在预设里的（手工新增的）也只平升名字，不动它自己的清单。
    """
    result = [dict(p) for p in providers]
    labels = labels or {}
    changed = 0
    for item in result:
        preset = presets.get(str(item.get("provider") or ""))
        if preset:
            for key in ("chat_model", "work_model"):
                want = preset.get(key)
                if want and item.get(key) != want:
                    item[key] = want
                    changed += 1
            want_models = [
                {"value": m["value"], "label": m["label"]} for m in preset.get("models") or []
            ]
            if want_models and (item.get("model_options") or []) != want_models:
                item["model_options"] = want_models
                changed += 1
            continue
        for key in ("chat_model", "work_model"):
            new_name = aliases.get(str(item.get(key) or ""))
            if new_name:
                item[key] = new_name
                changed += 1
        options = []
        for opt in item.get("model_options") or []:
            entry = dict(opt) if isinstance(opt, dict) else opt
            if isinstance(entry, dict):
                value = str(entry.get("value") or "")
                if value in aliases:
                    value = aliases[value]
                    entry["value"] = value
                    changed += 1
                if value in labels and entry.get("label") != labels[value]:
                    entry["label"] = labels[value]
                    changed += 1
            options.append(entry)
        item["model_options"] = options
    return result, changed


def remove_provider(providers: list[dict], name: str) -> list[dict]:
    """
    从供应商列表中删除指定名称的供应商（纯函数，返回新列表）。
    若删除的是默认供应商，则将新列表的第一个设为默认。
    """
    result = [dict(p) for p in providers]
    was_default = any(p.get("name") == name and p.get("is_default") for p in result)
    new_list = [p for p in result if p.get("name") != name]

    if was_default and new_list:
        new_list[0]["is_default"] = True

    return new_list
