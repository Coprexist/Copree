# 消息撤回

> 状态：已落地（v0.4.10，2026-09-26）｜迁移 `d0e1f2a3b4c5`（撤回标记）+ `e1f2a3b4c5d6`（通道引用索引）

## 一句话

**撤回不是把那条从历史上抹掉，而是"标一下 + 通知看过它的 AI + 界面变占位 + 顺手请外部通道一起撤"。**

## 为什么不能直接删/改

账本是 append-only 的（见 [conversation_history.md](./conversation_history.md)）：AI 的上下文里那句原文删不掉，
改中段还会让整段前缀缓存失效。所以撤回的语义是 **再补一条"这条已作废"的通知**，
而"撤回之后才进历史"的消息则天然只渲染占位。

## 语义

1. 站内标记：原文留在库里（审计/排障），但**任何渲染都不再显示**；
2. 给**已经看过这条消息**的 AI 补一条撤回通知（不重复被撤内容）；
3. 群里那条变成「XXX 撤回了一条消息」；
4. 外部通道同步撤回，**撤不掉时如实回报**（站内已撤、通道侧未撤）。

## 代码落点

**唯一入口 `app/chat/revoke.py`**（群聊与私信同形）：

- `check_revocable(message, actor_id, is_admin)` —— 幂等（撤过就拒）、权限（本人 / 群主管理员）、
  窗口 **2 分钟**（`REVOKE_WINDOW_SECONDS`，与 QQ 一致）；
- `revoke_group_message` / `revoke_dm_message` —— 落标记 + 补通知 + 调通道分发；
- `_append_revoke_notices` —— 反查账本里 `context_ref` 命中且 `ref = <msg_id>` 的条目，
  只给"看过它"的 AI 补通知（没看过的以后拿到的本来就是占位）；
- `utils/pure/history.revoked_notice()` / `revoked_text()` —— 文案的唯一来源。

**通道联动 `app/chat/outbound.py`**：出站注册表新增 **revoke 出口**
（与发消息相反：这里**等**每个通道回答，因为要把"撤没撤掉"如实告诉用户；
返回 `None` 表示这条不归它管）。QQ 侧调 `DELETE /v2/groups/{openid}/messages/{id}`。

## 数据模型

| 列 | 用途 |
|---|---|
| `revoked_at` / `revoked_by` | 撤回时间与操作者（users.id），渲染层据此只给占位 |
| `channel_msg_id` | 通道侧那条消息的 id：入站存事件 `d.id`，出站另开 session 回写发送响应里的 `id`（撤回要用） |
| `channel_ref_idx` | 通道侧的引用索引（QQ 的 `msg_idx` / `ext_info.ref_idx`），精准引用用 |

## 接口与前端

- `POST /gm/{group_id}/messages/{message_id}/revoke`、`POST /dm/{session_id}/messages/{message_id}/revoke`；
- WS 广播 `message_revoked` → 所有在线客户端把那条变占位；
- 消息操作菜单：悬停出现 → 回复 / 复制 / 撤回（撤回只在自己 2 分钟内的消息上出现，
  群主/管理员在别人的消息上也看得到，`my_role` 由 ChatArea 透传）；
- 通道侧没撤掉时，界面用一条提示条显示「站内已撤回，但外部通道没撤掉：…」，不假装全撤了。

## AI 侧

工具 `recall_message(group_id, message_id)`：只能撤自己发的（`is_admin` 恒 False，哪怕它是群主 AI），
返回里带通道侧结果。工具描述写清了"说错话时用、2 分钟内、撤不掉会写清楚"。

## 通道侧（QQ 官方）

`DELETE /v2/groups/{group_openid}/messages/{message_id}`：发送超过 2 分钟不可撤；
机器人是**群管理员**时还能撤普通成员的消息。常见错误码：`40061001` 参数无效、
`40062003` 无权限、`40064004` 超出撤回时限、`50065001` 撤回失败。这些都会原样进返回值。

**私信侧通道撤回未做**（官方那条接口只对群消息）。

## 已知限制

- **QQ 里别人撤回，我们收不到事件**（官方没有这个事件，实测也没有）→ 平台这边那条不会跟着变。
  这条通道限制已经写进 `channel.group_brief` 讲给 AI 听。
- **账本里已写下的旧条目不动**：若撤回发生在 AI 已经读过之后，它那一轮看到的仍是原文，
  下一轮会收到作废通知（这正是补通知存在的原因）。

## 测试

`backend/tests/test_message_revoke.py`：占位渲染 / 只通知看过的 AI / 窗口 / 权限 / 幂等 /
分发三态（成功、异常、不归我管）/ 路由 404 / 工具只能撤自己的。
