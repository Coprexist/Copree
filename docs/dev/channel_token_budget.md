# 通道对话额度（QQ / NapCat）

**单一来源**：每个外部聊天对话能用多少 token、怎么算、用完说什么——各自定在哪一层。

## 一句话

额度按**对话**算（一个 QQ 群一个、一个 QQ 私聊一个），默认 100 万**交涉 token**；
设置声明在**通道插件**里，账记在**平台**里，拦在**消息落库之前**。

## 分层：谁拥有什么

| 事项 | 归谁 | 位置 |
| --- | --- | --- |
| 口径、默认额度、超限自动回复开关与文案 | 通道插件 | 插件 `config_schema`（通道卡片上的表单） |
| 生效中的策略（供记账与拦截读取） | 平台 | `channel_token_policy`（插件 `start()` 时同步） |
| 每个对话的已用量、单独覆盖、重置 | 平台 | `channel_conversation_usage` |
| 拦截与自动回复 | 通道插件 | 落库前调 `token_quota.decide()`，按返回的 notice 发 |
| 记账 | 平台 | `conversation_log_service.save_conversation_log()` 统一落账 |

为什么策略要有一份平台副本：记账发生在模型调用那一侧，那里加载不到插件实例
（插件可能没启动、也可能被换掉），而额度判定必须与记账用同一份数。

## 计费口径

- `billable`（默认）：`max(0, prompt - cached) + completion`
- `total`：`total_tokens`（缺字段时按 `prompt + completion` 兜底）

两个计数器都累加（`used_billable` / `used_total`），所以换口径不丢历史，随时能换回去继续看。
换算与剩余量只有一处实现：`app/utils/pure/channel_token.py`。

## 记账：只算通道对话

`charge()` 认"这个对话是不是从外部通道来的"，不需要额外的绑定表：

- 群：`groups.origin_channel` 非空（落点群是通道建的或认领的才会带）；
- 私聊：对端用户 `users.origin_channel` 非空（通道来的人建号时就带着）。

站内群、站内私聊既不会出现在对话列表里，也不消耗任何额度。

## 拦截：落库之前，不是记账之后

插件在 `_deliver_to_group` / `_deliver_to_dm` 的第一步调 `decide()`：
这一条一旦落库就会唤醒 AI、跑完一轮模型调用，账已经花出去了。拒绝时消息不进 Copree。

自动回复默认开，同一个对话**每 10 分钟最多一句**（`BLOCKED_NOTICE_INTERVAL_SECONDS`）：
被刷屏的群否则会每来一条就回一句"额度用完"。文案留空 = 平台默认的官方口吻那一句。

## 页面

通道卡片里的「对话额度」：

- 顶部是**默认值（只读）**——它来自插件设置，在卡片上方的表单里改，改完重载通道生效；
- 下面每个对话一行：已用 / 额度、剩余、进度条、是否已用完；可单独设额度（覆盖默认）或回到默认；
- 可单个重置或全部重置。重置只清已用量，不删这条对话。

## 合作者权限

通道配置里是机器人凭据（AppID / ClientSecret / access_token），默认只有主人能看。
主人在 AI 的「合作者」区域可打开「允许合作者管理通道配置」（`agents.collaborators_manage_channels`，
迁移 0097）：打开后，**有编辑权**的合作者能看能改；新建的落点群仍落在 AI 主人名下。

## 迁移

- `0096`：`channel_token_policy` + `channel_conversation_usage`
- `0097`：`agents.collaborators_manage_channels`

## 已知边界

- 额度按 Copree 的对话算：一个落点群里的站内发言也计入该群额度（同一个对话，同一份账）。
- 额度用完后只拦入站；AI 已经发出但还没送达的回复不受影响。
- NapCat 是自建协议端，超限说明走主动发送（没有被动窗口）；官方 QQ 走被动回复，窗口过期就静默不发。
