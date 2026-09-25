"""
通俗模式（Plain language）的唯一来源：偏好键、判定、注入段文本。

为什么单独成模块：这段提示词要同时进入「普通 AI 对话」（ai/llm.py 的六段之外）
和「群视界世界 AI」（services/world/world_chat_service.py）两条互不相干的装配路径，
各写一份必然走形——"名词要解释"这种事只要有一处漏了，用户就会碰上裸术语。

为什么偏好放在 users.ui_prefs 而不是新加一列：ui_prefs 本就是「我这个人的偏好」
（PUT /user/settings 按 key 合并，/auth/me 原样回给前端），加列要动模型 + 迁移 +
两处响应组装，换来的只是一个更好听的名字。键名以本常量为准，别处不许再写字面量。

纯模块：不认识数据库，取人这件事由调用方用自己的会话做（各调用点都已持有会话）。
"""

# users.ui_prefs 里的键
PLAIN_LANGUAGE_PREF_KEY = "plain_language"

# 注入段：以【】开头与站内其它段（【平台信息】【名字】）保持同一读法
PLAIN_LANGUAGE_SEGMENT = (
    "\n【表达方式｜通俗模式】正在读你回复的人不懂编程。按下面的顺序要求自己：\n"
    "1. 先替换，再解释：能用日常说法讲清楚的事就用日常说法，别搬术语——"
    "能说「把它放到网上那台电脑上」就别写「部署到服务器」。\n"
    "2. 换不掉的专有名词（工具、框架、协议、文件、命令、英文缩写）第一次出现时，"
    "紧跟一句不超过 12 个字的大白话解释，说清「它是什么 / 干什么用」，"
    "例如：Flyway（管理数据库版本的工具）、webhook（别的系统主动来通知你）。\n"
    "3. 一段话里需要解释的名词不超过两个；要说的更多，就拆成几段，"
    "或者先用大白话说一遍再补名词。\n"
    "4. 短句，一次说一个点；步骤用「先…再…最后…」。不假设对方知道背景，"
    "也不要省掉「为什么这么做」。\n"
    "5. 不确定就说不确定，别把猜测讲成结论。\n"
    "6. 例外：代码块、报错原文、命令、文件名原样保留——它们是事实不是表达，"
    "前后各用一句大白话说明「这是什么 / 该怎么办」。"
)


def user_wants_plain_language(user) -> bool:
    """用户是否开了通俗模式。取不到人就当没开（缺省不改变任何人的说话方式）。"""
    prefs = getattr(user, "ui_prefs", None) or {}
    if not isinstance(prefs, dict):
        return False
    return bool(prefs.get(PLAIN_LANGUAGE_PREF_KEY))


def build_expression_segment(user) -> str:
    """返回要追加到系统提示词尾部的段；没开就返回空串（追加空串不改变前缀，缓存不受影响）。"""
    return PLAIN_LANGUAGE_SEGMENT if user_wants_plain_language(user) else ""
