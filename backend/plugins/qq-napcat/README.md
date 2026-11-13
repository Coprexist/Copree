# QQ 通道（NapCat / OneBot v11）

把 AI 接进 QQ，走**非官方**协议端 NapCat / OneBot v11（正向 WebSocket 收事件，HTTP 出站）。

## 注意：风险自担

非官方协议端违反 QQ 用户协议，存在封号风险。建议只用备用号、自担风险；
对外提供服务请用官方「QQ 通道」插件。

## 怎么用

1. 自建 NapCat（或任意 OneBot v11 实现），开启正向 WS 与 HTTP 上报
2. 在「这个 AI 的通道页」填协议端地址与 token
3. 把机器人账号拉进群，@ 它或私聊它

一个部署接一个 NapCat（未开多实例）：多开请用官方通道。

## 需要的权限

- **网络出站**：连你填的协议端（WS + HTTP），地址由你提供，插件不自带任何外部地址
- **读**：Bot token（加密存储，不回显）
- **可写数据**：Copree 的消息表与成员表；插件自己的表 `plugin_qq_napcat__accounts`
  （每个实例上次挂过的 QQ 号，卸载时可选保留或清掉）

## 配置项

| 键 | 说明 |
| --- | --- |
| ws_url / http_url | 协议端的正向 WS 与 HTTP 地址 |
| access_token | 协议端 token（若有） |
| target_agent | 由平台自动填 |
| copree_group_id | 默认落点群 |
