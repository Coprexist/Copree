# 工具记录与“我搜过了”

模型会在正文里描述自己"做了什么"：搜过了、查过了、没搜到。这些话是**文本**，不是事实记录。
本文只记两件事：判别"搜过"该看哪些证据，以及关键词搜聊天记录的入口。

## 1. 搜过没有，看这三处

| 证据 | 写在哪 | 会不会漏 |
|------|--------|----------|
| 工具派发日志 | stdout `调用工具: web_search(...)`，在调用那一刻打印 | 不漏（与轮次是否落库无关） |
| `tool_usage_daily` | 按日聚合的调用计数（`app/tools/base.py` 派发处记账） | 按日合计，不分到具体 AI |
| 当轮 `messages[].tool_calls` | `ai_conversation_logs.messages` | 受对话日志保留策略影响（老轮次会被清理） |

判"没搜"要三处一起看；只凭账本的"本轮工具"不够：

- 账本**有** `web_search` → 一定搜过；
- 账本**没有** → 先确认是不是同一轮。"搜索一轮、回答另一轮"很常见：回答那轮的账本只会有
  `send_gm/end_turn`，正文里的事实来自 `【想起】`（记忆召回注入）。
  2026-10-26 真机就是这种：23:50 那条只有 `send_gm/end_turn`，而 23:53 / 23:57 / 23:58 三轮都记着
  `web_search(ok)；web_fetch(ok)`——歌词来自记忆，不是 23:50 现搜的。

## 2. 检索工具集

能算"真去检索了"的工具：`web_search`、`web_fetch`、`read_conversation`、`recall_memory`、`expand_message`。
写文件、发消息都不算。这个集合目前只用于文档与排查口径，平台不据此拦截任何消息。

## 3. 关键词搜聊天记录：`search_messages`

此前平台只有"按会话读最近 N 条"（`read_conversation`）和"按 id 展开"（`expand_message`），
没有关键词检索——用户说"你搜一下"时，AI 没有可调的工具。`search_messages`
（`app/tools/chat_social/search_messages.py`）补这一步：

- 只搜**它自己在的会话**（已加入的群、参与的私信），越界不给；
- 参数：`query`（必填）、`group_id`、`sender_id`、`days`（默认 30，最多 3650）、`limit`（1–30，默认 10）；
- 返回 `{conversation, message_id, sender_type, sender_id, sender_name, created_at, snippet}`，
  片段取命中词前后各 24 字（最多 120 字），避免把长文灌回上下文；
- 没命中时回执提示换更短的词；外部资料明确指向 `web_search`。
