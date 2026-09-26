# @ 提及统一用 id（<@!id>）

> 状态：已落地（v0.4.10，2026-09-26）｜涉及平台内群聊/私信与外部通道出站

## 一句话

**存储与 AI 上下文里，@ 一律是 id（`<@!id>`）；名字只在给人看的地方现算。**
与 QQ 的内联 @ 同形，所以同一条正文在通道出口只是"换个壳里的 id"。

## 为什么（旧的病根）

平台内的 @ 以前是纯文本 `@名字`：前端补全插名字、后端按名字全字匹配。
于是名字会改、会重名、QQ 昵称还带空格，`strip_leading_mention` / `check_mention` 上堆了一串
"全字匹配 / 左括号不算边界 / 只认第一个词"的补丁——每一条都是名字匹配的代价。
id 唯一且稳定，这些问题一次性消失；通道出口拿到 id 也能直接翻成通道侧的真 @。

## 统一写法

| 位置 | 形态 | 例子 |
|---|---|---|
| 平台内 | `<@!users.id>` | `<@!41>` |
| QQ 官方 | `<@!openid>` | 正文里内联 @，QQ 渲染成可点的 @对方 |
| NapCat | `[CQ:at,qq=<QQ号>]` | 协议端 CQ 码 |
| 通配 | `@all` / `@ai` | 保持原样，不是 id |

## 四层落点（各管一段，不互相抄）

1. **编解码唯一入口** —— `app/utils/text.py`
   `mention_token` / `iter_mention_ids` / `mentions_user` / `link_mentions`（入口）/
   `render_mentions`（出口）/ `render_mention_names`（给人看）/ `take_trailing_msg_id`。
   边界字符集 `_MENTION_STOP` 与 `extract_mentions`/`check_mention` 共用同一份。
2. **入口归一** —— `app/chat/gm.py:send_gm_message` 落库前调 `link_group_mentions`：
   人的手打、QQ 入站、工具调用、世界桥一次覆盖。只做群聊（私信没有 @ 这回事）。
   名字带空格时按"全名 → 第一个词"两档认；**同一个写法指向两个人时弃用**（宁可留原文也不 @ 错人）；
   认不出的名字原样保留。
3. **识别兼容** —— `check_mention(content, name, id)` 同时认新令牌与旧 `@名字`
   （历史消息、人的手打还在用名字；世界群是 mention_only，认不出就等于叫不醒）。
   判定点：`response_worker`（主唤醒 + 群助手按名字）、`group_delivery`（分发/离线暂存）、
   `agent_service`（回复场景）；未读小红点 SQL 也加了令牌分支。
4. **展示** —— 前端 `frontend/src/utils/mentions.ts:renderMentions`（ChatView 消息正文与引用预览）；
   后端"顺手给人看"的地方（会话列表预览、聊天记录导出）用
   `message_serializer.mention_names`（一次查全用到的 id）+ `text.render_mention_names`。

## 通道出口

`app/services/plugin/channel_user.py`：

- `anchor_email(kind, origin)` —— 通道身份锚点邮箱的唯一拼法（建号与反查都走它）；
- `channel_contacts(db, kind, owner_scope)` —— "这条通道上认得的本地账号 → openid"。

QQ 插件把 `<@!id>` 翻成 `<@!openid>`，NapCat 翻成 `[CQ:at,qq=…]`；
**认不出的退成 `@名字`**（令牌原样发到通道侧只会是乱码）。查库放在后台发送任务里，不拖慢发消息。

## 全量模式（开了「接收所有消息」）

`GROUP_MESSAGE_CREATE` 里官方给 `mentions`：被 @ 的人先建成 Copree 群成员，
正文里缺提及就补 `<@!id>`（QQ 有时把 @成员的提及从正文摘掉）。@机器人模式收不到这个字段。

## 兼容与不做的事

- **旧消息不迁移**：历史正文里的 `@名字` 原样显示、识别仍然认它。
- 群助手（`group_assistants`）没有 users 行 → 只能按名字认，入口也不会把它的名字归一（代码里写明）。
- AI 侧的写法由工具描述统一：「要 @ 谁就写 `<@!对方的id>`，id 见每条消息说话人后面的 `（id=N）`」。

## 测试

- `backend/tests/test_mentions.py`：令牌往返、入口归一（含空格昵称 / 边界 / 歧义弃用 / 幂等）、出口渲染、摘开头 @、双写法识别。
- `backend/tests/test_echoed_context_markers.py`：AI 把 `[msg_id=…]` 抄进正文时的收口。
- `test_qq_channel_plugin.py` / `test_qq_napcat_plugin.py`：出站真 @（`<@!openid>` / CQ 码）、
  认不出的令牌不发出、全量事件补全正文、被 @ 的人建成群成员。
