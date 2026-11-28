"""
智能整理 —— 把一段别处的对话，整理成「建这个人设要用的材料」。

产出四样，各有去处（判据与创建助手完全同源，见 services/creator/agent_creator_service.py）：

    prompt    系统提示词：它是谁 / 怎么说话 / 有哪些重大的事（身份·关系·说话方式·边界四层）
    memories  这次聊出来的、值得长期记住的事实 → 记忆库
    knowledge 想起来才翻的大段资料 → 知识库（通常为空）
    summary   初摘要 → 导入时落进上下文账本（见 importer._inject_initial_summary）

**不是「这次聊了什么」的摘要**：议题（装修预算、某段代码）进记忆与初摘要，不进人设——
写进 system prompt 会把 AI 框死在一次对话里。用户原话：「提示词就是这个 AI 是谁怎么说话
有什么重大的事情，就是人设」；「这次聊的可以进记忆或者初摘要，当然比较大的情景要进提示词」。

模型从哪来：这件事发生在 AI 创建**之前**，没有「该 AI 的绑定配置」这一档，所以走
`resolve_user_llm`（池 → 用户默认），与创建助手同一条链。用不了时不自己找别的 Key、
不静默降级——返回 needs_config，由前端弹窗引导去配（配置入口全站一处）。
"""
from __future__ import annotations

import json
import logging
import re

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agent.api_config_resolver import resolve_user_llm
from app.services.chat_import.schema import Transcript
from app.utils.pure.llm_endpoint import chat_completions_url

logger = logging.getLogger(__name__)

TIMEOUT = 180.0
MAX_TOKENS = 4000
# 喂给模型的对话上限：太长就均匀采样，保住开头（人设线索）与结尾（最近的相处）
DIGEST_MAX_MESSAGES = 90
DIGEST_MAX_CHARS = 24000

SYSTEM = """你在帮用户把一段「他在别处与某个 AI 的聊天记录」整理成能在 Copree 上重新创建这个 AI 的材料。

只输出一个 JSON 对象，不要任何额外说明文字（可以用 Markdown 代码块包起来）：

{
  "prompt": "…",
  "memories": [{"content": "…", "importance": 3}],
  "knowledge": [{"title": "…", "summary": "…", "content": "…"}],
  "summary": "…"
}

各字段的含义与写法：

- prompt：这个 AI **是谁、怎么说话、有哪些重大的事情**——也就是人设。
  按四层写：身份 / 与用户的关系 / 说话方式（语气、句长、口头禅） / 边界（什么不做、怎么拒绝）。
  长期有效的重大情景（重要经历、关系的变化、它承担的角色）也要写进来——这些是它开口时的底气。
  **不要写这次聊的具体事务**（某个预算、某段代码、某次咨询的具体结论）：那些是内容不是人设，
  写进去会把它框死在一次对话里。
  配 2-3 组示例对话和几条错例（「不该这样说」）。不写动作描写，不写「要有温度」「保持人设不崩」这类空话。
  篇幅 300~800 字。

- memories：这次对话里**关于用户或这段关系**的、值得长期记住的事实。一条一句，陈述句。
  只管偏好、决定、重要事件、约定这类；寒暄、过程性的追问不要。importance 取 1~5（默认 3）。
  最多 20 条；没有就给空数组。

- knowledge：**想起来才去翻**的大段资料——详尽的世界观、年表、成批的示例对话、外部参考资料。
  多数情况下是空数组；只有这段对话里确实存在这类内容才写，别把记忆硬塞进来。

- summary：给这个 AI 的初摘要，交代「我们之前在别处聊过什么」，200 字以内，平实叙述。

判断某条内容该放哪，用这条：**不看着它就没法开口的，进 prompt；想起来再去翻的，进 knowledge；
一句话就能记住的事实，进 memories。**
"""


class OrganizeUnavailable(RuntimeError):
    """模型用不了。带上原因与决策轨迹，前端据此弹「去配置」。"""

    def __init__(self, reason: str, chain: list[dict] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.chain = chain or []


def _digest(transcript: Transcript) -> str:
    """对话 → 喂给模型的正文。太长就均匀采样，首尾都保住。"""
    msgs = transcript.messages
    if len(msgs) > DIGEST_MAX_MESSAGES:
        step = len(msgs) / DIGEST_MAX_MESSAGES
        picked = [msgs[min(int(i * step), len(msgs) - 1)] for i in range(DIGEST_MAX_MESSAGES)]
        picked[-1] = msgs[-1]
        msgs = picked

    who = transcript.participants or {}
    user_label = who.get("user") or "用户"
    ai_label = who.get("assistant") or "AI"
    lines: list[str] = []
    for m in msgs:
        speaker = user_label if m.role == "user" else ai_label
        text = (m.content or "").strip()
        if len(text) > 1200:
            text = text[:1200] + "…"
        for att in m.attachments:
            text += f"\n[附件：{att.name or '未命名'}{'' if att.available else '（无原文件）'}]"
        if not text:
            continue
        lines.append(f"{speaker}：{text}")
    body = "\n\n".join(lines)
    if len(body) > DIGEST_MAX_CHARS:
        body = body[:DIGEST_MAX_CHARS] + "\n\n…（以下省略）"
    return body


def _parse_json(text: str) -> dict:
    """模型偶尔会套代码块或加前后废话：只认第一个 JSON 对象。"""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


def _clean(result: dict) -> dict:
    """把模型给的东西收成确定的形状：类型不对的丢掉，不猜。"""
    prompt = str(result.get("prompt") or "").strip()
    summary = str(result.get("summary") or "").strip()

    memories = []
    for item in result.get("memories") or []:
        if isinstance(item, str):
            content, importance = item, 3
        elif isinstance(item, dict):
            content = str(item.get("content") or "").strip()
            try:
                importance = int(item.get("importance") or 3)
            except (TypeError, ValueError):
                importance = 3
        else:
            continue
        if content:
            memories.append({"content": content[:500], "importance": min(5, max(1, importance))})
    memories = memories[:20]

    knowledge = []
    for item in result.get("knowledge") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title or not content:
            continue
        knowledge.append({
            "title": title[:80],
            "summary": str(item.get("summary") or "").strip()[:200],
            "content": content,
        })

    return {"prompt": prompt, "memories": memories, "knowledge": knowledge, "summary": summary}


async def organize(
    db: AsyncSession,
    *,
    user_id: int,
    transcript: Transcript,
) -> dict:
    """跑一次智能整理。

    :raises OrganizeUnavailable: 没有可用模型（没配、额度用光、Key 坏了）
    """
    resolved = await resolve_user_llm(db, user_id, kind="llm", scenario="smart_organize")
    if not resolved.usable:
        raise OrganizeUnavailable(
            resolved.missing_reason,
            [s.as_dict() for s in resolved.chain],
        )

    model = resolved.chat_model or (resolved.models[0] if resolved.models else "")
    headers = {"Content-Type": "application/json"}
    if resolved.api_key:
        headers["Authorization"] = f"Bearer {resolved.api_key}"

    body = _digest(transcript)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "对话正文如下（平台：" + (transcript.origin.platform or "未知") + "）：\n\n" + body},
        ],
        "temperature": 0.3,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(chat_completions_url(resolved.base_url), json=payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"模型返回 HTTP {resp.status_code}：{resp.text[:200]}")

    data = resp.json()
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not text.strip():
        raise RuntimeError("模型没有返回内容")

    try:
        parsed = _parse_json(text)
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"模型输出不是合法 JSON：{e}")

    out = _clean(parsed)
    out["model"] = model
    out["source"] = resolved.source
    return out
