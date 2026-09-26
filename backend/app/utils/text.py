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


# @ 令牌：<@!id> —— 与 QQ 的内联 @ 同形（<@!openid>），同一个壳换 id 就能发到 QQ
MENTION_TOKEN_RE = re.compile(r'<@!(\d+)>')


def mention_token(user_id: int) -> str:
    """@ 一个人的规范写法 <@!id>。

    为什么不用 @名字：名字会改、会重名，QQ 昵称还带空格（「@书爱 Shu Ai Neumann LOVE」切不干净）；
    id 唯一且稳定，通道出口只要把 id 换成 openid 就能发成真 @（QQ 已验证内联 @ 可用）。
    """
    return f"<@!{int(user_id)}>"


def iter_mention_ids(content: str) -> list[int]:
    """正文里被 @ 到的人（去重、保持出现顺序）"""
    found: list[int] = []
    for match in MENTION_TOKEN_RE.finditer(content or ""):
        uid = int(match.group(1))
        if uid not in found:
            found.append(uid)
    return found


def mentions_user(content: str, user_id: int) -> bool:
    """这条正文有没有 @ 到这个人（按 id 比，不认名字）"""
    return int(user_id) in iter_mention_ids(content)


# 上下文尾部的消息号标记：format_message 加在每条消息末尾，给 AI 认"要引用哪条"
_MSG_ID_MARK_RE = re.compile(r'\s*\[msg_id=(\d+)\]\s*$')


def take_trailing_msg_id(content: str) -> tuple[str, int | None]:
    """收掉正文末尾的 [msg_id=N]，返回 (清理后的正文, N)。

    那个标记是**给 AI 读的**（format_message 把它加在每条消息尾部，好让它知道回复哪条），
    但模型有时把它当成"回复语法"抄进自己的正文——用户就看到一串 [msg_id=2477] 的噪音
    （2026-09-25 私信里实测 4 条）。正确写法是 reply_to 参数：入口把标记收下来，
    正好当成它的本意。只认**末尾**那一个，正文中间提到的数字不动。
    """
    if not content:
        return content, None
    match = _MSG_ID_MARK_RE.search(content)
    if match is None:
        return content, None
    return content[:match.start()].rstrip(), int(match.group(1))


def _mention_keys(name_to_id: dict[str, int]) -> dict[str, int]:
    """能被 @ 出来的写法 → id：全名，以及「第一个词」（QQ 昵称带空格时 AI 通常只写第一个词）。

    同一个写法指向两个人时（重名，或某人的全名正好是另一个人的第一个词）**弃用**该写法：
    宁可留原文，也别把 @ 喊给另一个人。
    """
    keys: dict[str, int] = {}
    for name, uid in name_to_id.items():
        clean = (name or "").strip()
        if not clean:
            continue
        for key in {clean, clean.split()[0]}:
            if key in keys and keys[key] != uid:
                keys[key] = 0                      # 冲突：标掉，下面过滤
            else:
                keys.setdefault(key, uid)
    return {k: v for k, v in keys.items() if v}


def link_mentions(content: str, name_to_id: dict[str, int]) -> str:
    """入口用：把正文里的 @名字 归一成 <@!id>，之后全链路只认 id。

    只在入口做一次（人的手打、QQ 入站、工具调用、世界桥都经过那里）：
    下游的唤醒判定、AI 上下文、通道出口就不必再各写一套名字比对。

    边界与 extract_mentions / check_mention 共用同一份字符集：不然「@化学老师·少宇」
    会被短名「化学老师」切一半。认不出的名字原样留着（和以前一样是纯文本 @某人）。
    """
    if not content or not name_to_id:
        return content
    keys = _mention_keys(name_to_id)
    names = sorted(keys, key=len, reverse=True)
    if not names:
        return content
    # 一趟扫完：逐个 replace 会让先替换出来的令牌再被后面的短名命中
    pattern = re.compile(
        "@(" + "|".join(re.escape(n) for n in names) + ")"
        r"(?=[" + _MENTION_STOP + r"]|$)"
    )
    return pattern.sub(lambda m: mention_token(keys[m.group(1)]), content)


def render_mentions(content: str, resolve) -> str:
    """把正文里的 <@!id> 换成 resolve(id) 给出的写法（通道出口用：QQ 官方是 <@!openid>、
    NapCat 是 [CQ:at,qq=…]）。

    与 link_mentions 是一对：入口把名字收成 id，出口再把 id 摊成那条通道认得的样子。
    认不出的 id 由调用方决定退成什么——QQ 那条退名字，令牌原样发过去只会是乱码。
    """
    if not content:
        return content
    return MENTION_TOKEN_RE.sub(lambda m: resolve(int(m.group(1))), content)


def render_mention_names(content: str, id_to_name: dict[int, str]) -> str:
    """<@!id> → @名字（纯函数）。

    给人看的地方（会话列表预览、导出）用它：查不到名字就退成 @用户N——
    宁可看得见，也别把 <@!41> 这种令牌露到人眼前。
    """
    return render_mentions(content, lambda uid: "@" + (id_to_name.get(uid) or f"用户{uid}"))


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


def check_mention(content: str, target_name: str = "", target_id: int | None = None) -> bool:
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
    # 新写法：正文里存的是 <@!id>（入口归一之后就是它）
    if target_id is not None and mentions_user(content, target_id):
        return True
    # 旧写法：@名字（历史消息、人的手打）——兼容着，不能只认新的，否则叫不醒人
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
_MD_INLINE_CODE_RE = re.compile(r'`([^`\n]+)`')
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

    为什么需要：QQ 群的 markdown 是**内邀开通**的能力，且 MD 权限是**机器人账号维度**的
    （同一个平台里有的号有、有的没有）。出站已经改成"先按 msg_type=2 发 Markdown，
    接口说没权限再退回纯文本"（QqClient._send_rich）——所以这里只服务没权限的那些号：
    AI 写的 **加粗**、# 标题、`行内代码` 不能原样露在群里。
    站内不动（Copree 自己渲染 markdown）。
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
    text = _MD_INLINE_CODE_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_QUOTE_RE.sub("", text)
    return _MD_HR_RE.sub("", text)


def strip_leading_mention(content: str, name: str, user_id: int | None = None) -> str:
    """去掉正文开头**对这个人的** @提及，其余一字不动。

    用途：外部通道出站时清掉"回复对象"那个 @ —— 平台的被动回复自己就会显示 @对方，
    正文里再写一个就成了两个 @（用户 2026-09-25 在 QQ 群里看到的正是这个）。
    站内消息不动：Copree 界面要靠它显示 AI 在回复谁。

    边界规则与 check_mention 共用一份字符集（全字匹配、左括号不算边界）：
    否则「@化学老师·少宇」会被短名「化学老师」这个前缀匹配掉一半。
    """
    if not content:
        return content
    head = content.lstrip()
    # 新写法：<@!id>（入口归一之后正文开头就是它）
    if user_id:
        token = mention_token(user_id)
        if head.startswith(token):
            return head[len(token):].lstrip(" \t，,：:、")
    if not name:
        return content
    # 旧写法：@名字。昵称可能很长还带空格（QQ 昵称常态，如「书爱 Shu Ai Neumann」）：AI 常常只写第一个词。
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
