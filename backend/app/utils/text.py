"""
文本处理工具函数
"""

import re

# @提及 名称的终止字符（空白与各种标点）：提取与「名字后是否为边界」共用同一份，
# 免得两处字符集漂移 —— 一处改了另一处没改，@ 就会时灵时不灵
_MENTION_STOP = r'\s@，。！？、；：""''「」『』【】（）\(\)\[\]{}<>#+*&^%$!~`|\\/\n'

# @提及 提取正则：匹配 @ 后跟非空白/非标点字符的名称
_MENTION_RE = re.compile(r'@([^' + _MENTION_STOP + r']+)')
# 名字后面是不是真的边界（用于「全字匹配」判定）
_MENTION_STOP_RE = re.compile(r'[' + _MENTION_STOP + r']')
# 左括号是「名字还没写完」的信号：@浮生 撞上 @浮生（人物志1） 时不能算边界，
# 否则一个只叫「浮生」的 AI 会被「@浮生（人物志1）」唤醒（用户 2026-09-23：括号必须全字匹配）
_MENTION_OPEN_RE = re.compile(r'[（(【「『\[{<“]')

# CJK 字符范围（中日韩统一表意文字）
_CJK_RE = re.compile(r'[一-鿿㐀-䶿豈-﫿]')


def extract_mentions(content: str) -> set[str]:
    """
    从消息内容中提取所有 @提及 的名称。

    例如 "你好 @梦希 和 @涵吾珑 一起聊天" → {"梦希", "涵吾珑"}
    支持格式: @name（name 不含空格，直到遇到空格/标点/结尾）
    """
    mentions = set()
    for match in _MENTION_RE.finditer(content):
        name = match.group(1).rstrip('.,;:!?…')
        if name:
            mentions.add(name)
    return mentions


def check_mention(content: str, target_name: str) -> bool:
    """
    检查消息中是否 @ 提及了指定名称，或 @all/@ai。

    还支持 @human（泛指所有人类成员）。

    ⚠️ 名称里带括号/标点的（如「浮生（人物志1）」）必须按**整串**再找一次：提取规则遇到
    （）等分隔符就截断，只拿得到「浮生」，拿全名去比必然 False —— 而世界群的触发模式
    是 mention_only，@ 不上就等于这个 AI 永远不醒（用户 2026-09-23 报的正是这个）。

    但「整串」是**全字匹配**（用户 2026-09-23 明确要求）：名字后面必须是真的边界，
    且左括号不算边界。所以
      · 「@浮生（人物志1）」唤醒「浮生（人物志1）」✔
      · 「@浮生」**不**唤醒「浮生（人物志1）」✘（括号不是可选后缀）
      · 「@浮生（人物志1）」**不**唤醒只叫「浮生」的 AI ✘（否则一个称呼叫醒两个人）
    这里不再走 extract_mentions：它在括号处截断，正好会把后两种情况判成"命中"。
    """
    if not target_name:
        return False
    full = f"@{target_name}"
    idx = content.find(full)
    while idx != -1:
        end = idx + len(full)
        if end >= len(content) or (
            _MENTION_STOP_RE.match(content, end) and not _MENTION_OPEN_RE.match(content, end)
        ):
            return True
        idx = content.find(full, idx + 1)
    content_lower = content.lower()
    return "@all" in content_lower or "@ai" in content_lower


# Markdown 降级用的标记（QQ 群没开通原生 MD 时只能发纯文本，见 docs/develop 的 markdown 一节）
_MD_FENCE_RE = re.compile(r'^\s*```[^\n]*$', re.M)
_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_MD_BOLD_RE = re.compile(r'\*\*(.+?)\*\*|__(.+?)__', re.S)
_MD_STRIKE_RE = re.compile(r'~~(.+?)~~', re.S)
_MD_ITALIC_RE = re.compile(r'\*(.+?)\*|_(.+?)_', re.S)
_MD_HEADING_RE = re.compile(r'^\s{0,3}#{1,6}\s*', re.M)
_MD_QUOTE_RE = re.compile(r'^\s{0,3}>\s?', re.M)
_MD_HR_RE = re.compile(r'^\s{0,3}([-*_])\1{2,}\s*$', re.M)


def plainify_markdown(content: str) -> str:
    """Markdown → 纯文本（保留换行与列表，只去掉排版符号）。

    为什么需要：QQ 群的 markdown 是**内邀开通**的能力（官方：目前需要内邀开通，
    且"被动 MD 仍需单独申请开通"——我们发的恰好都是被动回复）。没开通时只能发
    msg_type=0 纯文本，AI 写的 **加粗**、# 标题就会原样露出来。
    站内不动（Copree 自己渲染 markdown），只在外部通道出站时降级；
    开通原生 MD 之后把出站换成 msg_type=2 + markdown.content 就能直接渲染。
    """
    if not content:
        return content
    text = _MD_FENCE_RE.sub("", content)
    text = _MD_IMAGE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(
        lambda m: m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)}（{m.group(2)}）", text
    )
    text = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_STRIKE_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_QUOTE_RE.sub("", text)
    return _MD_HR_RE.sub("", text)


def strip_leading_mention(content: str, name: str) -> str:
    """去掉正文开头**对这个人的** @提及，其余一字不动。

    用途：外部通道出站时清掉"回复对象"那个 @ —— 平台的被动回复自己就会显示 @对方，
    正文里再写一个就成了两个 @（用户 2026-09-25 在 QQ 群里看到的正是这个）。
    站内消息不动：Copree 界面要靠它显示 AI 在回复谁。

    边界规则与 check_mention 共用一份字符集（全字匹配、左括号不算边界）：
    否则「@化学老师·少宇」会被短名「化学老师」这个前缀匹配掉一半。
    """
    if not content or not name:
        return content
    head = content.lstrip()
    # 昵称可能很长还带空格（QQ 昵称常态，如「书爱 Shu Ai Neumann」）：AI 常常只写第一个词。
    # 两种写法都算"在回复这个人"：完整昵称、昵称的第一段。按长到短试，避免短名把长名切一半。
    for candidate in (name, name.split()[0] if name.split() else ""):
        if not candidate:
            continue
        full = f"@{candidate}"
        if not head.startswith(full):
            continue
        end = len(full)
        if len(head) > end and not (
            _MENTION_STOP_RE.match(head, end) and not _MENTION_OPEN_RE.match(head, end)
        ):
            continue
        return head[end:].lstrip(" \t，,：:、")
    return content


def validate_status_text(status_text: str | None) -> str | None:
    """
    校验状态文本长度。空值/空字符串直接放行（表示清空状态）。

    规则：中文为主（CJK 占比 > 50%）→ 最多 10 字；英文为主 → 最多 30 字符。
    不符合则抛出 ValueError。
    """
    if not status_text or not status_text.strip():
        return status_text

    text = status_text.strip()
    cjk_count = len(_CJK_RE.findall(text))
    total_chars = len(text)
    cjk_ratio = cjk_count / total_chars if total_chars > 0 else 0

    if cjk_ratio > 0.5:
        max_len = 10
        lang_name = "中文"
    else:
        max_len = 30
        lang_name = "英文/拉丁"

    if total_chars > max_len:
        raise ValueError(
            f"状态文本过长（{total_chars} 字符），{lang_name}状态最多 {max_len} 个字符"
        )

    return text
