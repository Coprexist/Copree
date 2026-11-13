# QQ 通道（官方机器人 API）

把 Copree 的 AI 接进 QQ：群里被 @ 或私聊机器人时唤醒 AI，AI 的回复发回 QQ。

## 怎么用

1. 在 QQ 开放平台创建机器人，拿到 AppID / ClientSecret
2. 到「这个 AI 的通道页」填进去并保存，通道自动启动、开始长连（WebSocket）
3. 把机器人拉进 QQ 群，群里 @ 它说话，或直接私聊它

一个实例 = 一个机器人 = 一个 AI。多个 AI 就建多个实例（每个机器人一份配置）。

## 需要的权限

- **网络出站**：api.sgroup.qq.com（REST 发消息）、WebSocket 长连（收事件）
- **读写**：插件配置（plugin_configs，AppSecret 加密存储，接口只回「已填」）
- **可写数据**：Copree 的消息表与成员表（通道的职责就是让外部消息落进群）
  插件自己的目录里只有代码与 manifest（入口 `plugin.py`，装配；收发与身份在 `inbound.py` /
  `outbound.py` / `identity.py`，常量在 `common.py`）

不读写插件目录以外的文件，不落盘明文凭据。

## 平台限制（写在这里，免得当成 bug）

- 群里只能被动回复：有人 @ 或回复机器人才发得出去（腾讯 2025-04-21 起下线主动推送）
- 被动回复窗口：群 5 分钟、私聊 60 分钟，同一条消息最多回 5 次
- 私聊可主动发，但每人每天 2 条、每机器人每天 200 条
- Markdown：私聊可用；群聊看机器人权限，没开通时自动降级纯文本

## 配置项

| 键 | 说明 |
| --- | --- |
| app_id / client_secret | QQ 开放平台的机器人凭据 |
| target_agent | 由平台自动填（这个机器人是哪个 AI），用户不用填 |
| copree_group_id / group_map | QQ 群落到哪个 Copree 群 |
| auto_create_group | QQ 群首次说话时自动接上落点群（默认开） |
| qq_group_allowlist | 允许接入的 QQ 群，留空不限制 |
