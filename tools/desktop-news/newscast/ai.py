"""AI 概括：任意 OpenAI 兼容接口，把候选事件写成新闻卡片。

三个刻意的设计：
- 只发聚类后的候选（不是原始几百条），并按 max_items_per_call 分批，成本可控；
- 结果按标题指纹缓存，白天多次刷新不会重复计费；
- 任何一步失败都返回可用性说明而不是抛异常，流水线据此回落到规则卡片。
"""

from __future__ import annotations

import json
import logging
import re

from . import netclient, textutil
from .classify import Cluster
from .config import resolve_api_key
from .models import NewsCard

_SYSTEM_PROMPT = """你是一名资深中文新闻编辑，负责为中学教室的电子屏编写「每日新闻速览」。
读者是中学生，用途是了解时事、积累作文素材。

硬性规则：
1. 只使用我提供的信息，绝不编造事实、数字、人名、时间或结果；信息不足时写得更保守。
2. candidates 中若有多条讲的是同一件事，必须合并成一张卡片，并在 ids 里列出全部编号。
3. 标题不超过 24 个汉字，概括 60~120 字，讲清「发生了什么 + 关键细节 + 为什么值得关注」。
4. 客观中立，不使用「震惊」「炸裂」「速看」这类标题党措辞，不对政治立场作评价。
5. 每条卡片都要给作文角度（15~30 字），指出这条新闻能支撑什么论点，例如「科技自立：核心技术买不来」。
6. 分类只能取我给的四类之一：top（大事件）、politics（时政社会）、tech（科技前沿）、novelty（新奇趣闻）。
7. 只输出严格 JSON，不要解释文字，不要 Markdown 代码块。"""

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


def parse_ai_json(text: str) -> dict:
    """从模型输出里抠出 JSON。模型时不时会加代码块或前后缀说明，这里都容忍。"""
    if not text:
        raise ValueError("AI 返回为空")
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败：{exc}") from exc
    raise ValueError("返回内容中找不到 JSON")


class AIClient:
    def __init__(self, cfg: dict, logger: logging.Logger) -> None:
        self.cfg = cfg.get("ai", {})
        self.logger = logger
        self.base_url = (self.cfg.get("base_url") or "").rstrip("/")
        self.model = self.cfg.get("model") or "deepseek-chat"
        self.api_key = resolve_api_key(self.cfg)
        self.json_mode = bool(self.cfg.get("json_mode", True))
        self.last_error = ""

    @property
    def available(self) -> bool:
        return bool(self.cfg.get("enabled", True) and self.api_key and self.base_url)

    def unavailable_reason(self) -> str:
        if not self.cfg.get("enabled", True):
            return "配置中已关闭 AI"
        if not self.api_key:
            return "未配置 API key（config.json 的 ai.api_key 或环境变量 NEWSCAST_API_KEY）"
        if not self.base_url:
            return "未配置 ai.base_url"
        return ""

    def _chat(self, messages: list[dict], max_tokens: int) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": float(self.cfg.get("temperature", 0.3)),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"
        try:
            data = netclient.post_json(url, payload, timeout=float(self.cfg.get("timeout", 120)),
                                       retries=1, headers=headers)
        except netclient.FetchError as exc:
            # 部分兼容实现不认识 response_format，去掉它再试一次
            if self.json_mode and exc.status in (400, 404, 422):
                self.logger.warning("接口不支持 response_format，降级重试：%s", exc)
                payload.pop("response_format", None)
                self.json_mode = False
                data = netclient.post_json(url, payload, timeout=float(self.cfg.get("timeout", 120)),
                                           retries=1, headers=headers)
            else:
                raise

        choices = (data or {}).get("choices") or []
        if not choices:
            raise ValueError(f"接口未返回 choices：{str(data)[:200]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            raise ValueError("接口返回的 message.content 为空")
        return content

    def _build_prompt(self, clusters: list[Cluster], window: tuple, categories: list[dict]) -> str:
        cat_desc = []
        for cat in categories:
            kws = "、".join(list(cat.get("keywords") or [])[:14])
            cat_desc.append({"key": cat["key"], "label": cat["label"], "关键词": kws})
        start, end = window
        news = []
        summary_limit = int(self.cfg.get("max_summary_chars", 90))
        for cluster in clusters:
            news.append({
                "id": cluster.cid,
                "分类倾向": cluster.category,
                "标题": cluster.title,
                "来源": cluster.sources[:4],
                "原文摘要": textutil.clean_text(cluster.summary, summary_limit),
                "时间": textutil.format_local(cluster.published),
            })
        payload = {
            "时间范围": f"{textutil.format_local(start, '%Y-%m-%d %H:%M')} 至 "
                       f"{textutil.format_local(end, '%Y-%m-%d %H:%M')}",
            "分类定义": cat_desc,
            "candidates": news,
            "输出格式": {"cards": [{"ids": [1], "category": "tech", "title": "...",
                                     "summary": "...", "angle": "..."}]},
        }
        return json.dumps(payload, ensure_ascii=False)

    def summarize(self, clusters: list[Cluster], window: tuple, categories: list[dict],
                  cache: dict) -> tuple[list[tuple[NewsCard, list[Cluster]]], bool, str]:
        """返回 (卡片及其来源候选, 是否用了 AI, 错误说明)。空候选直接返回，不浪费一次调用。

        卡片与候选的对应关系由这里精确给出（模型返回的 ids），下游据此写缓存、
        判断哪些候选还没被覆盖，避免用标题相似度去猜而写错缓存。
        """
        if not clusters:
            return [], False, ""
        if not self.available:
            return [], False, self.unavailable_reason()

        per_call = max(5, int(self.cfg.get("max_items_per_call", 22)))
        max_batches = max(1, int(self.cfg.get("max_batches", 3)))
        # 重要/新的候选优先进入 AI 处理范围，超出的部分走规则卡片
        ordered = sorted(clusters, key=lambda c: c.score, reverse=True)
        batches = [ordered[i:i + per_call] for i in range(0, len(ordered), per_call)][:max_batches]

        by_cid = {c.cid: c for c in clusters}
        pairs: list[tuple[NewsCard, list[Cluster]]] = []
        used_any = False
        errors: list[str] = []
        for index, batch in enumerate(batches, 1):
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": self._build_prompt(batch, window, categories)},
            ]
            try:
                content = self._chat(messages, self.cfg.get("max_tokens", 4000))
                data = parse_ai_json(content)
                got = self._cards_from_ai(data, by_cid, categories)
                if got:
                    pairs.extend(got)
                    used_any = True
                    self.logger.info("AI 第 %d/%d 批：%d 个候选 -> %d 张卡片",
                                     index, len(batches), len(batch), len(got))
                else:
                    errors.append(f"第 {index} 批未产出可用卡片")
            except Exception as exc:
                self.logger.warning("AI 第 %d 批失败：%s", index, exc)
                errors.append(f"第 {index} 批：{exc}")

        message = "；".join(errors[:3])
        return pairs, used_any, message

    def _cards_from_ai(self, data: dict, by_cid: dict[int, Cluster],
                       categories: list[dict]) -> list[tuple[NewsCard, list[Cluster]]]:
        """把模型返回的 ids 映射回候选，补全来源、链接与时间。

        模型偶尔会自造分类名，这里按配置的分类表校验，非法值退回候选自身的判定。
        """
        raw_cards = data.get("cards")
        if not isinstance(raw_cards, list):
            raise ValueError("返回 JSON 缺少 cards 数组")
        valid_keys = {c["key"] for c in categories}
        out: list[tuple[NewsCard, list[Cluster]]] = []
        for raw in raw_cards:
            if not isinstance(raw, dict):
                continue
            ids = raw.get("ids")
            if not isinstance(ids, list):
                ids = [raw.get("id")] if raw.get("id") is not None else []
            members: list[Cluster] = []
            for cid in ids:
                try:
                    cid_int = int(cid)
                except (TypeError, ValueError):
                    continue
                cluster = by_cid.get(cid_int)
                if cluster is not None and cluster not in members:
                    members.append(cluster)
            if not members:
                continue
            title = textutil.clean_text(raw.get("title") or "", 60)
            summary = textutil.clean_text(raw.get("summary") or "", 300)
            if not title or not summary:
                continue
            category = str(raw.get("category") or members[0].category).strip()
            if category not in valid_keys:
                category = members[0].category
            card = NewsCard(
                title=title,
                summary=summary,
                angle=textutil.clean_text(raw.get("angle") or "", 80),
                category=category,
                sources=[s for m in members for s in m.sources],
                urls=[u for m in members for u in m.urls],
                published=max((m.published for m in members if m.published), default=None),
                item_count=sum(m.item_count for m in members),
                score=max(m.score for m in members) + 1.0,  # AI 产出的卡片优先展示
                from_ai=True,
            )
            out.append((card, members))
        return out
