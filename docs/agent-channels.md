# 给自己的 AI 接一个外部通道（QQ / NapCat）

> 状态：已实现。入口在「我的 AI → 设置 → 通道」，页面里按通道分子项（有几条由插件声明）。
> 相关代码：app/routers/channels.py、app/services/plugin/{channel,pairing,runtime_control}.py、
> backend/plugins/qq-channel/（官方）、backend/plugins/qq-napcat/（协议端）、
> frontend/src/components/channels/{ChannelCard,ChannelModal}.tsx

## 1. 定位

给一个 AI 多接一个聊天软件。目前两条通道，可以**同时开**（各是一份配置、一份外部身份）：

| 通道 | 怎么接 | 拿得到什么 |
| --- | --- | --- |
| QQ（官方） | 去 QQ 开放平台自建机器人，填 AppID / AppSecret | 群里被 @ 与私聊；腾讯只给 openid |
| QQ（NapCat） | 自己跑一个 NapCat / OneBot v11 协议端接管真 QQ 号 | 群/私聊全量消息、支持富文本；违反 QQ 用户协议，封号风险自担 |

不管哪条，接进来之后：

- QQ 群里被 @ → 消息进 Copree 群 → 走主站同一套投递与唤醒
- QQ 私聊机器人 → 进该 AI 的私信会话 → 同样走主站那套

**认证与触发都不是新的一套**：入口只做"翻译"，收进来的消息走
app/chat/group_delivery.py 与 dm_delivery.py，和网页端完全同路。

## 2. 三种身份，别混

| 谁 | 靠什么识别 | 说明 |
| --- | --- | --- |
| AI 主人 | platform 账号 | 配置这条通道的人 = AI 的 owner_id |
| 通道上的对话者 | **origin**（通道侧标识） | 官方 QQ 给的是按机器人加密的 openid，**拿不到 QQ 号**；NapCat 给的是真 QQ 号。身份存在 external_identities，不占 users.id |
| 机器人自己 | AppID / AppSecret | 主人去 QQ 开放平台扫码自建，凭据 Fernet 加密落库、接口不回显 |

所以"认人"只能认 openid：QQ 号填了也校验不了，界面只把它当显示名。昵称来自官方事件的
username 字段，存在 external_identities.display_name 上。

外部身份**不建在主站的 users 表里**：不占用户号、不进用户统计、搜不到也加不了好友。
他以后自己注册了 Copree 账号，才用 `bound_user_id` 把两边绑起来。
想自己写一个通道插件（接别的平台）：[写一个通道插件](./dev/channel-plugins.md)。

## 3. 配对（默认策略）

默认 dm_policy = pairing，对齐 OpenClaw 的 DM pairing（docs/channels/pairing.md）：

1. 陌生人私聊机器人 → **消息不进 AI**，只收到一个 6 位配对码（60 秒内最多提醒一次）
2. Copree 里这张卡片出现"待批准"，显示昵称、配对码与 openid 尾号
3. 主人点「批准」，或把码抄回来「按码批准」→ 该 openid 记入 external_identities（这个外部身份那一行，approved）
4. 之后他的私聊才进 AI。可随时「拉黑」（静默忽略）或「解除」（回到陌生人，重新领码）

策略四档：pairing（默认）/ owner（只认已配对，陌生人静默）/ open（谁都能聊）/ off（私聊关闭）。
群里被 @ 不受私聊策略影响，另有 QQ 群白名单。

## 4. 归属与实例

- 实例 id = agent-<agentId>：一个 AI 每条通道一个实例，天然不冲突
- 归属直接读 agents.owner_id，**没有给 plugin_configs 加归属列**（少一列就少一处能对不上的地方）
- 非主人访问一律 403；**管理员也被挡在外面**（通道里存的是用户的机器人凭据，
  管理员排障走控制台的插件配置接口，不需要从这条路径碰别人的凭据）
- 管理台那份「列表即真相」有护栏：agent- 前缀的实例归 AI 主人管，
  管理员重扫 / 保存插件配置不会把它们删掉（见 plugin/config.py 的 is_owner_scoped）

## 5. 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | /me/agents/{id}/channels | 这个 AI 的全部通道视图：插件 id / 类别 / 三语名称 / schema / 已填值 / 机密"填没填" / 运行态 / 待批已批名单 |
| PUT | /me/agents/{id}/channels/{plugin_id} | 保存配置（target_agent 由路径上的 AI 决定，用户传什么都不算）并启动 |
| POST | /me/agents/{id}/channels/{plugin_id}/landing-group | 不接回现有群，改在 Copree 新建一个落点群 |
| POST | /me/agents/{id}/channels/{plugin_id}/start \| stop | 启停（与管理台共用 runtime_control） |
| POST | /me/agents/{id}/channels/{plugin_id}/pairings/approve | 按 pairing_id 或 code 批准 |
| POST | /me/agents/{id}/channels/{plugin_id}/pairings/block | 拉黑一个人（body 里是 origin） |
| POST | /me/agents/{id}/channels/{plugin_id}/pairings/forget | 解除配对 |

`{plugin_id}` 不是平台常量：哪个插件算通道由它 manifest 的 `channel` 块决定（见
[写一个通道插件](./dev/channel-plugins.md)），未知 plugin_id 一律 404。

## 6. 接口能给什么、不能给什么（官方 QQ 机器人）

腾讯的规则（2026-09 实测 + 官方文档 bot.q.qq.com/wiki 的消息收发一节）：

| 场景 | 能不能 | 规则 |
| --- | --- | --- |
| 群里发言 | **只能被动回复** | 只有别人 @ 或回复机器人时才发得出去；**主动推送能力腾讯自 2025-04-21 起不再提供**（报 40034105 主动消息失败, 无权限） |
| 被动回复窗口 | 能 | 群 **5 分钟**、私聊 **60 分钟**；同一条消息最多回 **5 次**，超时或超频失败 |
| 私聊主动消息 | 能 | 每天每用户最多 **2 条**，每机器人每天累计 **200 条** |
| Markdown | 私聊已验证可用 | 群聊取决于该机器人是否开通；平台的做法是**先按 MD 发，接口说没权限就在同一次发送里降级纯文本** |

这些规矩会**同时告诉 AI**：平台在 AI 的上下文里注入一段「这个群接了外部通道」的说明
（app/ai/llm.py 注入，文案在 app/services/plugin/channel.py 的 group_brief），
免得它答应「我待会儿在群里发」。

## 7. 已知限制

- 富媒体不回传：QQ 发来的图片/语音/文件只转成文字占位
- 多个 QQ 群共用一个 Copree 群时，群回复回到"最近一次来消息"的那个群
- **AI 重名**：实例只靠名字绑定目标 AI，重名时启动会报错并要求填 Copree 群 ID（不会静默绑错）
- 一个 AI 每条通道一个实例（同一插件要接两个机器人 = 两条实例，界面上还没开）；官方 QQ 与 NapCat 可以同时开
- 群接入（让 AI 自己加群）还没做，见 TODO：AI 不在线时写脚本那条线
