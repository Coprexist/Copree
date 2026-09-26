# 免打扰（DND）语义地图

> 状态：已落地（浮窗 / 桌面通知 / 侧栏三处口径已对齐）｜**改 DND 之前先读这一份**

## 一句话

**免打扰挡的是"常规消息"，不是"别叫我"：点名到你个人照样进来，@all 不破例。**
"dnd" 在本仓是四个互不相干的东西——动手前先确认你改的是哪一个，它们分别管
人的提醒、AI 的唤醒、以及好友优先级，改错一处会连带影响另外三处。

## 四义表

| # | 是什么 | 存在哪 | 谁写 | 谁读 | 管什么 |
|---|---|---|---|---|---|
| 1 | **人的群免打扰** | `group_members.dnd_until`（`member_type='human'` 那行） | `POST /groups/{id}/dnd` → `chat/delivery.py` | 前端三处（见下） | 别拿这个群的常规消息打扰我 |
| 2 | 人的私信免打扰 | `dm_sessions.user1/2_dnd_until` | `POST /dm/{sid}/dnd` | 浮窗与桌面通知（按会话） | 同上，按会话 |
| 3 | **AI 的三档静音** | `group_members.dnd_until`（AI 行）、`group_members.muted_until`、`member_silences`、`agents.state='dnd'` | AI 自己的按钮 / 自动降级 | `chat/delivery.py`、`chat/group_delivery.py`、`ai/decider.py` | 要不要把这个 AI 叫醒（与人的提醒无关） |
| 4 | 特别关心 | `friendship.is_priority` | 好友设置 | `ai/decider.py` | 这个人的消息优先，可穿透 AI 的 DND |

### 存储约定（别按注释猜）

`group_members.dnd_until`：**NULL / 过去 = 没设免打扰**；将来时间 = 免打扰中；
"永久"存 `2099-12-31`，取消写 `2000-01-01`（`app/chat/delivery.py` 的 set/cancel）。
判断一律走 `is_member_in_dnd()` 或 `dnd_until > now`——历史上这里被写反过
（注释写过 "NULL=永久免打扰"，而代码把空当成没设；`group_delivery.py` 里还留着那次的记录）。

## 人的 DND 拦在哪：前端拦，后端照推

`connection_manager.broadcast_to_group` **不查 DND**：push 照发，过滤全在客户端。
所以要让"@"穿透，不需要后端放行，改判定条件就够。

| 面 | 文件 | 规则 |
|---|---|---|
| 站内浮窗 | `frontend/src/hooks/useNotificationSocket.ts` | 免打扰群里的常规消息不弹；`mentioned_me`（点名到你个人）照弹 |
| 桌面通知（标题未读 / 闪烁） | `frontend/src/hooks/useDesktopNotification.ts` | 免打扰群不再整段跳过：被点名到个人时**记 1**（语义是"有一处找你"，不是未读数） |
| 侧栏「@你」标记 | `frontend/src/components/ChatSidebar.tsx` | 免打扰不再压掉这个标记 |
| 侧栏未读数字 | 同上 | 免打扰群照显原始未读数（不动） |

后端只负责把判据送给前端：`_push_to_scopes` 送 `mentioned_me`（个人点名）与
`mentions_all`（@all）两个标记；正文由 `notification_service.message_toast` 整理。

## AI 的口径与人的口径不同（有意，别"对齐"掉）

- **AI 侧**：`ai/decider.py` 的 `dnd_penetrate = is_mentioned or is_at_all or is_announcement
  or is_priority_friend`——**@all 也穿透**。对 AI，@all 是"所有 AI 都该被叫"，它被叫是应该的。
- **人侧**：只认点名到个人；**@all 不破例**——对人，@all 是"喊所有人"，
  人人被喊一遍不该打扰已经关掉免打扰的人（否则谁都能一键叫醒全体免打扰的人）。
- `@ai` 是叫 AI：对人的通知两种都不算。
- 判词只有一份：`app/utils/text.py` 的 `mentions_all` / `mentions_all_ai`（`check_mention` 也调它们）。

## 改动清单（改 DND 语义时逐项过一遍）

一处语义改动会同时落在四个地方，只改一处会让用户看到自相矛盾的行为——
"浮窗弹了、标题不闪、侧栏没标记"比"三处都不破例"更像 bug：

1. AI 唤醒（`ai/decider.py`、`chat/group_delivery.py` 的在线/DND 暂存判定）
2. 站内浮窗（`useNotificationSocket.ts` 的 `toItem`）
3. 桌面通知与未读口径（`useDesktopNotification.ts`）
4. 侧栏标记与未读数字（`ChatSidebar.tsx`）

改完把这份文档的表格一起更新——它是这几处的唯一地图。

## 相关

- [@ 提及统一用 id](./mention_ids.md)：`<@!id>` 的写法与四层落点（判词就在这里）
- [决策层](./decision_layer.md)：AI 的触发与 notify 语义
- [前端界面统一规范](./ui_system.md)：浮窗等组件的样式与自检
