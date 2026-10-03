"""
LLM 厂商预设 — 常用 API 提供商的默认配置
每个预设含：base_url、聊天/工作/embedding 模型、是否支持深度推理、可用模型列表
"""

from typing import NotRequired, TypedDict


class ProviderPreset(TypedDict):
    key: str
    label: str
    base_url: str
    api_key_url: str  # 获取 API Key 的官网链接
    chat_model: str
    work_model: str
    embedding_model: str
    thinking_supported: bool
    models: list[dict]  # {value, label}
    # 标了它的预设会在启动时直接变成一条"配置项"（见 services/agent/provider_bootstrap）：
    # 主流厂商升级后本该就位，不该等使用者一个个去点。管理员删过的配置项不会再补。
    auto_config: NotRequired[bool]


PRESETS: dict[str, ProviderPreset] = {
    # ── DeepSeek ──
    "deepseek": {
        "key": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key_url": "https://platform.deepseek.com/api_keys",
        "chat_model": "deepseek-flash",
        "work_model": "deepseek-v4-pro",
        "embedding_model": "deepseek-embed",
        "thinking_supported": True,
        "models": [
            {"value": "deepseek-flash", "label": "DeepSeek V4.1 Flash（快速）"},
            {"value": "deepseek-v4-pro", "label": "DeepSeek V4 Pro（高质量）"},
        ],
    },

    # ── OpenAI ──
    "openai": {
        "key": "openai",
        "label": "OpenAI / ChatGPT",
        "base_url": "https://api.openai.com",
        "api_key_url": "https://platform.openai.com/api-keys",
        # 默认给次新一代：代号越新、越可能只走 Responses API，而这条链路只发 /chat/completions
        "chat_model": "gpt-5.5",
        "work_model": "gpt-5.5-pro",
        "embedding_model": "text-embedding-3-small",
        "thinking_supported": False,
        # 清单只收"OpenAI 兼容 chat 上大概率能用"的：gpt-6-astra、*-codex、realtime 这些
        # 只在 Responses API 目录里出现，选了会报错，不列（要用手填模型名即可）
        "models": [
            {"value": "gpt-5.6-sol", "label": "GPT-5.6 Sol（最新旗舰）"},
            {"value": "gpt-5.6-sol-pro", "label": "GPT-5.6 Sol Pro（最强）"},
            {"value": "gpt-5.6-luna", "label": "GPT-5.6 Luna（均衡）"},
            {"value": "gpt-5.6-terra", "label": "GPT-5.6 Terra"},
            {"value": "gpt-5.5", "label": "GPT-5.5"},
            {"value": "gpt-5.5-pro", "label": "GPT-5.5 Pro"},
            {"value": "gpt-5.4", "label": "GPT-5.4"},
            {"value": "gpt-5.4-mini", "label": "GPT-5.4 Mini（快速）"},
            {"value": "gpt-5", "label": "GPT-5（上一代旗舰）"},
            {"value": "gpt-5-mini", "label": "GPT-5 Mini"},
            {"value": "gpt-5-nano", "label": "GPT-5 Nano（最省）"},
            {"value": "gpt-4.1", "label": "GPT-4.1"},
            {"value": "gpt-4o", "label": "GPT-4o"},
            {"value": "o4-mini", "label": "o4 Mini（推理）"},
        ],
    },

    # ── Ollama 本地 ──
    "ollama": {
        "key": "ollama",
        "label": "Ollama（本地）",
        "base_url": "http://localhost:11434",
        "api_key_url": "",
        "chat_model": "qwen3",
        "work_model": "qwen3:14b",
        "embedding_model": "nomic-embed-text",
        "thinking_supported": False,
        "models": [
            {"value": "qwen3", "label": "Qwen3"},
            {"value": "qwen3:14b", "label": "Qwen3 14B"},
            {"value": "llama4", "label": "Llama 4"},
            {"value": "mistral", "label": "Mistral"},
            {"value": "deepseek-r1:8b", "label": "DeepSeek R1 8B"},
        ],
    },

    # ── 通义千问 / DashScope ──
    "qwen": {
        "key": "qwen",
        "label": "通义千问（阿里云 DashScope）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_url": "https://dashscope.console.aliyun.com/apiKey",
        "chat_model": "qwen-plus",
        "work_model": "qwen-max",
        "embedding_model": "text-embedding-v3",
        "thinking_supported": True,
        "models": [
            {"value": "qwen-plus", "label": "Qwen Plus（均衡）"},
            {"value": "qwen-max", "label": "Qwen Max（最强）"},
            {"value": "qwen-turbo", "label": "Qwen Turbo（快速）"},
            {"value": "qwq-plus", "label": "QwQ Plus（深度推理）"},
            {"value": "qwen3.6-plus", "label": "Qwen3.6 Plus（新一代均衡）"},
            {"value": "qwen3.6-flash", "label": "Qwen3.6 Flash（新一代快速）"},
        ],
    },

    # ── Kimi / Moonshot ──
    "kimi": {
        "key": "kimi",
        "label": "Kimi（月之暗面 Moonshot）",
        "base_url": "https://api.moonshot.cn",
        "api_key_url": "https://platform.moonshot.cn/console/api-keys",
        "chat_model": "kimi-k2.5",
        "work_model": "kimi-k3",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "kimi-k3", "label": "Kimi K3（旗舰）"},
            {"value": "kimi-k2.7-code", "label": "Kimi K2.7 Code（代码）"},
            {"value": "kimi-k2.7-code-highspeed", "label": "Kimi K2.7 Code HighSpeed（极速）"},
            {"value": "kimi-k2.6", "label": "Kimi K2.6（均衡）"},
            {"value": "kimi-k2.5", "label": "Kimi K2.5（快速）"},
            {"value": "kimi-k2-thinking", "label": "Kimi K2 Thinking（深度推理）"},
            {"value": "kimi-k2-thinking-turbo", "label": "Kimi K2 Thinking Turbo（推理加速）"},
        ],
    },

    # ── 智谱 GLM ──
    "zhipu": {
        "key": "zhipu",
        "label": "智谱 AI（GLM）",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "chat_model": "glm-5.3-flash",
        "work_model": "glm-5.3",
        "embedding_model": "embedding-2",
        "thinking_supported": False,
        "models": [
            {"value": "glm-5.3", "label": "GLM-5.3（旗舰）"},
            {"value": "glm-5.3-highspeed", "label": "GLM-5.3-Highspeed（极速）"},
            {"value": "glm-5.3-flash", "label": "GLM-5.3-Flash（轻量免费档）"},
            {"value": "glm-5.2", "label": "GLM-5.2（1M 上下文）"},
            {"value": "glm-5.2-highspeed", "label": "GLM-5.2-Highspeed（极速）"},
            {"value": "glm-5.1", "label": "GLM-5.1（长程任务优化）"},
            {"value": "glm-5", "label": "GLM-5（基座模型）"},
            {"value": "glm-5-turbo", "label": "GLM-5-Turbo（轻量任务优化）"},
            {"value": "glm-4.7", "label": "GLM-4.7（上一代旗舰）"},
            {"value": "glm-4.7-flashx", "label": "GLM-4.7-FlashX（轻量高速）"},
            {"value": "glm-4.7-flash", "label": "GLM-4.7-Flash（免费模型）"},
            {"value": "glm-4.6", "label": "GLM-4.6（200K 上下文）"},
            {"value": "glm-4.5-air", "label": "GLM-4.5-Air（高性价比）"},
            {"value": "glm-4.5-airx", "label": "GLM-4.5-AirX（极速版本）"},
            {"value": "glm-4-long", "label": "GLM-4-Long（1M 上下文）"},
            {"value": "glm-4-flashx-250414", "label": "GLM-4-FlashX-250414（免费增强版）"},
            {"value": "glm-4-flash-250414", "label": "GLM-4-Flash-250414（免费版）"},
            {"value": "glm-4-plus", "label": "GLM-4-Plus（高性能）"},
            {"value": "glm-4-air-250414", "label": "GLM-4-Air-250414（基座模型）"},
            {"value": "glm-4-airx", "label": "GLM-4-AirX（极速推理）"},
        ],
    },

    # ── 硅基流动 SiliconFlow ──
    "siliconflow": {
        "key": "siliconflow",
        "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn",
        "api_key_url": "https://cloud.siliconflow.cn/account/ak",
        "chat_model": "Qwen/Qwen3-8B",
        "work_model": "deepseek-ai/DeepSeek-V3",
        "embedding_model": "BAAI/bge-large-zh-v1.5",
        "thinking_supported": False,
        "models": [
            {"value": "Qwen/Qwen3-8B", "label": "Qwen3 8B"},
            {"value": "deepseek-ai/DeepSeek-V3", "label": "DeepSeek V3"},
            {"value": "deepseek-ai/DeepSeek-R1", "label": "DeepSeek R1"},
            {"value": "meta-llama/Llama-4-Maverick-17B-128E-Instruct", "label": "Llama 4 Maverick"},
        ],
    },

    # ── Xiaomi MiMo（按量付费）──
    "xiaomi-mimo": {
        "key": "xiaomi-mimo",
        "label": "Xiaomi MiMo",
        "base_url": "https://api.xiaomimimo.com",
        "api_key_url": "https://platform.xiaomimimo.com/#/console/api-keys",
        "chat_model": "mimo-v2.6-flash",
        "work_model": "mimo-v2.6-pro",
        "embedding_model": "",
        "thinking_supported": True,
        "models": [
            {"value": "mimo-v2.6-pro", "label": "MiMo V2.6 Pro（旗舰）"},
            {"value": "mimo-v2.6-pro-ultraspeed", "label": "MiMo V2.6 Pro UltraSpeed（极速）"},
            {"value": "mimo-v2.6-flash", "label": "MiMo V2.6 Flash（快速）"},
            {"value": "mimo-v2.5-pro", "label": "MiMo V2.5 Pro（多模态，高质量）"},
            {"value": "mimo-v2.5", "label": "MiMo V2.5（多模态）"},
        ],
    },

    # ── Xiaomi MiMo Token Plan（订阅制）──
    "xiaomi-mimo-tp": {
        "key": "xiaomi-mimo-tp",
        "label": "Xiaomi MiMo（Token Plan 订阅）",
        "base_url": "https://token-plan-cn.xiaomimimo.com",
        "api_key_url": "https://platform.xiaomimimo.com/#/console/api-keys",
        "chat_model": "mimo-v2.6-flash",
        "work_model": "mimo-v2.6-pro",
        "embedding_model": "",
        "thinking_supported": True,
        "models": [
            {"value": "mimo-v2.6-pro", "label": "MiMo V2.6 Pro（旗舰）"},
            {"value": "mimo-v2.6-pro-ultraspeed", "label": "MiMo V2.6 Pro UltraSpeed（极速）"},
            {"value": "mimo-v2.6-flash", "label": "MiMo V2.6 Flash（快速）"},
            {"value": "mimo-v2.5-pro", "label": "MiMo V2.5 Pro（多模态，高质量）"},
            {"value": "mimo-v2.5", "label": "MiMo V2.5（多模态）"},
        ],
    },

    # ── Google Gemini ──
    "google": {
        "key": "google",
        "label": "Google Gemini",
        "auto_config": True,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_url": "https://aistudio.google.com/apikey",
        "chat_model": "gemini-3.5-flash",
        "work_model": "gemini-3.1-pro-preview",
        "embedding_model": "text-embedding-004",
        "thinking_supported": False,
        "models": [
            {"value": "gemini-3.8-flash", "label": "Gemini 3.8 Flash（最新）"},
            {"value": "gemini-3.7-flash", "label": "Gemini 3.7 Flash"},
            {"value": "gemini-3.5-flash", "label": "Gemini 3.5 Flash（均衡）"},
            {"value": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash-Lite（轻量）"},
            {"value": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro（旗舰预览）"},
            {"value": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
            {"value": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
        ],
    },

    # ── xAI Grok ──
    "xai": {
        "key": "xai",
        "label": "xAI Grok",
        "auto_config": True,
        "base_url": "https://api.x.ai/v1",
        "api_key_url": "https://console.x.ai",
        "chat_model": "grok-4.5",
        "work_model": "grok-4.6",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "grok-4.6", "label": "Grok 4.6（旗舰）"},
            {"value": "grok-4.5", "label": "Grok 4.5"},
            {"value": "grok-4.3", "label": "Grok 4.3"},
        ],
    },

    # ── Groq（超快推理）──
    "groq": {
        "key": "groq",
        "label": "Groq",
        "auto_config": True,
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_url": "https://console.groq.com/keys",
        "chat_model": "openai/gpt-oss-120b",
        "work_model": "qwen/qwen3.8-27b",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "openai/gpt-oss-120b", "label": "GPT-OSS 120B（推荐）"},
            {"value": "openai/gpt-oss-20b", "label": "GPT-OSS 20B（轻量）"},
            {"value": "qwen/qwen3.8-27b", "label": "Qwen3.8 27B"},
            {"value": "qwen/qwen3.6-27b", "label": "Qwen3.6 27B"},
            {"value": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B"},
            {"value": "llama-3.1-8b-instant", "label": "Llama 3.1 8B（极速）"},
        ],
    },

    # ── OpenRouter（一个 Key 聚各家）──
    "openrouter": {
        "key": "openrouter",
        "label": "OpenRouter（聚合）",
        "auto_config": True,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_url": "https://openrouter.ai/keys",
        "chat_model": "openai/gpt-5.6-luna",
        "work_model": "anthropic/claude-opus-5",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "anthropic/claude-opus-5", "label": "Claude Opus 5"},
            # Responses-only 的模型（GPT-6 Astra 这类）原生 API 用不了，OpenRouter 已经替我们
            # 转成 chat/completions，这条路是零改动用上它们的方式
            {"value": "openai/gpt-6-astra", "label": "GPT-6 Astra（经网关）"},
            {"value": "openai/gpt-5.6-sol", "label": "GPT-5.6 Sol"},
            {"value": "openai/gpt-5.6-luna", "label": "GPT-5.6 Luna"},
            {"value": "google/gemini-3.8-flash", "label": "Gemini 3.8 Flash"},
            {"value": "deepseek/deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
            {"value": "moonshotai/kimi-k3", "label": "Kimi K3"},
        ],
    },

    # ── Mistral ──
    "mistral": {
        "key": "mistral",
        "label": "Mistral",
        "auto_config": True,
        "base_url": "https://api.mistral.ai/v1",
        "api_key_url": "https://console.mistral.ai/api-keys",
        "chat_model": "mistral-medium-latest",
        "work_model": "mistral-large-latest",
        "embedding_model": "mistral-embed",
        "thinking_supported": False,
        "models": [
            {"value": "mistral-large-latest", "label": "Mistral Large（旗舰）"},
            {"value": "mistral-medium-latest", "label": "Mistral Medium（均衡）"},
            {"value": "mistral-small-latest", "label": "Mistral Small（快速）"},
            {"value": "codestral-latest", "label": "Codestral（代码）"},
            {"value": "devstral-latest", "label": "Devstral（编码 agent）"},
        ],
    },

    # ── Together（聚合，按量）──
    "together": {
        "key": "together",
        "label": "Together AI",
        "base_url": "https://api.together.ai/v1",
        "api_key_url": "https://api.together.ai/settings/api-keys",
        "chat_model": "deepseek-ai/DeepSeek-V4-Pro",
        "work_model": "Qwen/Qwen3.7-Max",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "deepseek-ai/DeepSeek-V4-Pro", "label": "DeepSeek V4 Pro"},
            {"value": "deepseek-ai/DeepSeek-V4-Flash-0731", "label": "DeepSeek V4 Flash 0731"},
            {"value": "Qwen/Qwen3.7-Max", "label": "Qwen3.7 Max"},
            {"value": "Qwen/Qwen3.6-Plus", "label": "Qwen3.6 Plus"},
            {"value": "MiniMaxAI/MiniMax-M3", "label": "MiniMax M3"},
            {"value": "google/gemma-4-31B-it", "label": "Gemma 4 31B"},
        ],
    },

    # ── Fireworks（聚合，按量）──
    "fireworks": {
        "key": "fireworks",
        "label": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_url": "https://fireworks.ai/account/api-keys",
        "chat_model": "accounts/fireworks/models/kimi-k3",
        "work_model": "accounts/fireworks/models/glm-5p3",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "accounts/fireworks/models/kimi-k3", "label": "Kimi K3"},
            {"value": "accounts/fireworks/routers/kimi-k3-fast", "label": "Kimi K3 Fast"},
            {"value": "accounts/fireworks/models/glm-5p3", "label": "GLM 5.3"},
            {"value": "accounts/fireworks/models/glm-5p3-flash", "label": "GLM 5.3 Flash"},
            {"value": "accounts/fireworks/models/glm-5p2", "label": "GLM 5.2"},
        ],
    },

    # ── Cerebras（超快推理）──
    "cerebras": {
        "key": "cerebras",
        "label": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_url": "https://cloud.cerebras.ai",
        "chat_model": "gpt-oss-120b",
        "work_model": "gemma-4-31b",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "gpt-oss-120b", "label": "GPT-OSS 120B"},
            {"value": "gemma-4-31b", "label": "Gemma 4 31B"},
        ],
    },

    # ── NVIDIA NIM ──
    "nvidia": {
        "key": "nvidia",
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_url": "https://build.nvidia.com",
        "chat_model": "deepseek-ai/deepseek-v4-pro-0813",
        "work_model": "minimaxai/minimax-m3",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "deepseek-ai/deepseek-v4-pro-0813", "label": "DeepSeek V4 Pro 0813"},
            {"value": "deepseek-ai/deepseek-v4-flash-0731", "label": "DeepSeek V4 Flash 0731"},
            {"value": "minimaxai/minimax-m3", "label": "MiniMax M3"},
            {"value": "moonshotai/kimi-k2.6", "label": "Kimi K2.6"},
            {"value": "meta/muse-glimmer-30b", "label": "Muse Glimmer 30B"},
        ],
    },

    # ── HuggingFace Router ──
    "huggingface": {
        "key": "huggingface",
        "label": "HuggingFace Router",
        "base_url": "https://router.huggingface.co/v1",
        "api_key_url": "https://huggingface.co/settings/tokens",
        "chat_model": "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "work_model": "MiniMaxAI/MiniMax-M3",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "MiniMaxAI/MiniMax-M3", "label": "MiniMax M3"},
            {"value": "Qwen/Qwen3-235B-A22B-Instruct-2507", "label": "Qwen3 235B A22B"},
            {"value": "Qwen/Qwen3-235B-A22B-Thinking-2507", "label": "Qwen3 235B Thinking"},
            {"value": "Qwen/Qwen3-30B-A3B", "label": "Qwen3 30B A3B"},
            {"value": "Qwen/Qwen2.5-Coder-32B-Instruct", "label": "Qwen2.5 Coder 32B"},
        ],
    },

    # ── Baseten ──
    "baseten": {
        "key": "baseten",
        "label": "Baseten",
        "base_url": "https://inference.baseten.co/v1",
        "api_key_url": "https://app.baseten.co/settings/api_keys",
        "chat_model": "deepseek-ai/DeepSeek-V4-Pro",
        "work_model": "moonshotai/Kimi-K3",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "deepseek-ai/DeepSeek-V4-Pro", "label": "DeepSeek V4 Pro"},
            {"value": "deepseek-ai/DeepSeek-V4-Flash-0731", "label": "DeepSeek V4 Flash 0731"},
            {"value": "moonshotai/Kimi-K3", "label": "Kimi K3"},
            {"value": "moonshotai/Kimi-K2.7-Code", "label": "Kimi K2.7 Code"},
            {"value": "openai/gpt-oss-120b", "label": "GPT-OSS 120B"},
        ],
    },

    # ── 蚂蚁 Ling ──
    "ant-ling": {
        "key": "ant-ling",
        "label": "蚂蚁 Ling",
        "base_url": "https://api.ant-ling.com/v1",
        "api_key_url": "https://api.ant-ling.com",
        "chat_model": "Ling-2.6-flash",
        "work_model": "Ling-2.6-1T",
        "embedding_model": "",
        "thinking_supported": False,
        "models": [
            {"value": "Ling-2.6-flash", "label": "Ling 2.6 Flash（快速）"},
            {"value": "Ling-2.6-1T", "label": "Ling 2.6 1T（旗舰）"},
            {"value": "Ring-2.6-1T", "label": "Ring 2.6 1T（推理）"},
        ],
    },
}


def get_preset(key: str) -> ProviderPreset | None:
    """获取指定厂商预设"""
    return PRESETS.get(key)


def get_all_presets() -> list[ProviderPreset]:
    """获取所有厂商预设（简要，不含 models 详情）"""
    return [
        {
            "key": p["key"],
            "label": p["label"],
            "base_url": p["base_url"],
            "api_key_url": p["api_key_url"],
            "chat_model": p["chat_model"],
            "work_model": p["work_model"],
            "embedding_model": p["embedding_model"],
            "thinking_supported": p["thinking_supported"],
            "models": p["models"],
            "global_default_chat_model": p.get("global_default_chat_model"),
            "global_default_work_model": p.get("global_default_work_model"),
            "auto_config": p.get("auto_config", False),
        }
        for p in PRESETS.values()
    ]


# ── 旧模型名 → 现名 ──────────────────────────────────────────────
# 厂商换代会连 model id 一起换（deepseek-v4-flash → deepseek-flash、mimo-v2-* → mimo-v2.6-*），
# 而库里 agents / users / world_ais 存的是当时那个名字。只在这里登记一行，启动时按它把旧名平升：
# 别的部署者拉到新代码就跟着迁移，不必为每次改名再写一份 Alembic 迁移。
# 只登记「同一个模型换名」或「旧档位已被现役档位取代」，不记"升档"——那会悄悄改掉花费。
MODEL_ALIASES: dict[str, str] = {
    "deepseek-v4-flash": "deepseek-flash",
    # 小米 v2 系列已不下发（实测 /v1/models 只剩 v2.5 / v2.6）
    "mimo-v2-pro": "mimo-v2.6-pro",
    "mimo-v2-omni": "mimo-v2.6-pro",
    "mimo-v2-flash": "mimo-v2.6-flash",
}


def canonical_model(model: str | None) -> str | None:
    """把历史名收敛成现名；认不出（或为空）就原样返回。"""
    if not model:
        return model
    return MODEL_ALIASES.get(model.strip(), model)
