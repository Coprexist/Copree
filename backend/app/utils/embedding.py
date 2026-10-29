"""
嵌入向量工具模块
统一入口：get_embedding() 走 Embedding Provider 插件（embedding_providers 包）。

设计（对齐 dsh-mneme 向量哲学）：
  - 向量是"可选增强"：Provider embed 失败返回 None，调用方自动降级文本检索
  - 配置独立于 chat（EMBEDDING_*），不依赖 DeepSeek（其无 embedding API）
  - 未来转 JS：接口语义不变（OpenAI 兼容 /embeddings 端点）

兼容策略：
  - get_embedding() 签名保持不变（text, api_base_url, api_key, model），
    旧调用方（12 处）零改动
  - api_base_url 显式传入且非默认值时：按旧逻辑直连该端点
    （兼容 agent 级自定义 embedding 服务，如硅基流动/智谱）
  - 否则：走 get_embedding_provider() 插件
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 缓存的嵌入维度（首次成功后缓存；未启用向量时 None）
_embedding_dimension: int | None = None
_embedding_model: str | None = None


async def detect_embedding_dimension(
    api_base_url: str = "",
    api_key: str | None = None,
) -> tuple[int, str]:
    """
    自动检测嵌入维度（兼容旧调用，直接走当前 Provider）。

    返回 (维度, 模型名称)；未启用向量时返回 (1536, 默认模型)，
    与旧行为一致（调用方拿默认值兜底）。
    """
    global _embedding_dimension, _embedding_model

    if _embedding_dimension is not None:
        return _embedding_dimension, _embedding_model

    from app.embedding_providers import get_embedding_provider

    provider = get_embedding_provider()
    if provider.is_available():
        # 用 Provider 实际测一次，拿真实维度
        vec = await provider.embed("test dimension detection")
        if vec:
            _embedding_dimension = len(vec)
            _embedding_model = getattr(provider, "model", None) or provider.name
            logger.info(
                f"✅ 嵌入维度检测成功: 后端={provider.name}, "
                f"模型={_embedding_model}, 维度={_embedding_dimension}"
            )
            return _embedding_dimension, _embedding_model

    # 未启用/失败：默认值（与旧行为一致）
    logger.warning("⚠️  Embedding 未启用或不可用，使用默认维度 1536")
    _embedding_dimension = 1536
    _embedding_model = "text-embedding-3-small"
    return 1536, "text-embedding-3-small"


async def get_embedding(
    text: str,
    api_base_url: str = "",
    api_key: str | None = None,
    model: str | None = None,
) -> list[float]:
    """
    获取文本的嵌入向量。

    兼容旧签名。路由逻辑：
    1. 显式指定 api_base_url（非空且非默认 DeepSeek）→ 直连该 OpenAI 兼容端点
       （兼容 agent 级自定义 embedding 服务）
    2. 否则 → 走 embedding_providers 插件（ollama/api/local/disabled）

    返回向量；Provider 不可用时抛 EmbeddingUnavailableError
    （调用方已有 try/except 降级；routers/memories.py 显式处理）。
    """
    # 显式直连：仅当调用方明确传了非默认 base_url（兼容旧行为）
    if api_base_url and api_base_url != "https://api.deepseek.com":
        return await _embed_via_endpoint(
            text,
            api_base_url=api_base_url,
            api_key=api_key,
            # 模型优先级：显式指定 > EMBEDDING_MODEL > 旧默认
            model=model or settings.embedding_model or settings.default_embedding_model,
        )

    from app.embedding_providers import (
        get_embedding_provider,
        EmbeddingUnavailableError,
    )

    provider = get_embedding_provider()
    vec = await provider.embed(text)
    if vec:
        return vec

    raise EmbeddingUnavailableError(
        f"Embedding 后端 '{provider.name}' 不可用或返回空，"
        "已降级文本检索（请检查 EMBEDDING_BACKEND 配置）"
    )


async def _embed_via_endpoint(
    text: str,
    api_base_url: str,
    api_key: str | None,
    model: str,
) -> list[float]:
    """直连 OpenAI 兼容 /v1/embeddings 端点（旧逻辑，保留给显式自定义服务）。"""
    from app.utils.pure.llm_endpoint import embeddings_url
    url = embeddings_url(api_base_url)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            headers=headers,
            json={"model": model, "input": text},
        )
        if response.status_code != 200:
            raise Exception(
                f"Embedding API 错误 ({response.status_code}): {response.text[:500]}"
            )
        data = response.json()
        return data["data"][0]["embedding"]


def get_cached_dimension() -> int:
    """获取已缓存的嵌入维度（不会触发检测）"""
    return _embedding_dimension or 1536


def cosine_similarity(vec_a, vec_b) -> float:
    """纯 Python 余弦相似度（给没有 SQL 内向量运算能力的后端做检索降级）。

    pgvector 的 `<=>` 只有 PG 认，SQLite 安装包上必须把候选取回来在 Python 里算。
    维度不一致视为不可比（换过 embedding 模型的历史数据），返回 0。
    """
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = norm_a = norm_b = 0.0
    for x, y in zip(vec_a, vec_b):
        fx, fy = float(x), float(y)
        dot += fx * fy
        norm_a += fx * fx
        norm_b += fy * fy
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))
