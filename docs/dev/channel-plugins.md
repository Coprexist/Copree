# 写一个通道插件（把外部平台接到 Copree）

> 状态：外部身份层、出站出口、**通道自测**、**@ 提及出站翻译**、**撤回联动**均已实现（最近更新 v0.4.10，2026-09-26）；
> 消息 / 私信会话 / 群成员改为直接引用外部身份是下一阶段（见 §7）。
> 参考实现：`backend/plugins/qq-channel/`（官方 QQ 机器人，生产在用）。
> 相关代码：`app/models/external.py`、`app/services/plugin/pairing.py`、`app/services/plugin/channel.py`、
> `app/services/plugin/catalog.py`、`app/chat/{gm,dm,group_delivery,dm_delivery,outbound}.py`

## 0. 职责怎么分

插件只管两件事：**跟外部平台收发消息**、**把凭据和状态报上来**。
Copree 管三件事：**这个外部的人是谁**、**放不放行**、**消息怎么进 AI**。

两者对接的地方只有一张表：`external_identities`（一行 = 某个通道上的某个人）。

## 1. 身份分两个世界

| | 表 | 谁 | 判别式 |
| --- | --- | --- | --- |
| 本实例 | `users` | 真人（`human`）、AI（`ai`）、系统（`system`） | `sender_type` = human / ai / system |
| 非本实例 | `external_identities` | QQ 上的人、联邦对端的人、以后任何平台的人 | `sender_type` = external |

为什么外部身份不建在 `users` 里（这是本层的设计前提，别绕开）：

- `users.id` 不被外部身份消耗，用户序号不会因为别人从 QQ 说了一句话就往前跳；
- 搜人、加好友、注册引导（`/auth/has-users` 与「首个注册用户即管理员」）、用户统计
  都不必逐个打 `type` 补丁 —— 漏一处就会把外部的人当成真人；
- 加一个新来源只是加一个 `kind`，不用动 users、不用动统计。

`external_identities` 用三元组认人（唯一约束就是这三列）：

| 列 | 含义 | QQ 的例子 | 联邦的例子 |
| --- | --- | --- | --- |
| `kind` | **来源类别**，插件自己在 manifest 里声明 | `qq` | `federation`（平台保留） |
| `owner_scope` | 该类别下的归属实例 | `agent-24` | 对端公网 ID |
| `origin` | 通道侧的稳定标识 | `openid` | 远端实体 ID |

另外两列：`display_name` / `avatar_url`（只用于展示）、`bound_user_id`（他后来在本实例
自己注册了账号时把两边绑起来，平时为 NULL）。

## 2. 声明：`plugin.json` 的 `channel` 块

```json
{
  "id": "qq-channel",
  "category": "service",
  "channel": {
    "kind": "qq",
    "label": "QQ",
    "label_en": "QQ",
    "label_ja": "QQ",
    "pairing": true,
    "supports_group": true
  }
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `kind` | 是 | 外部身份类别名。格式 `^[a-z][a-z0-9_-]{2,31}$`（小写，至少 3 位） |
| `label` | 是 | 界面上的展示名（中文）。**插件自带文案**，不写进平台的 i18n 文件 |
| `label_en` / `label_ja` | 否 | 缺省回落 `label` |
| `desc` / `desc_en` / `desc_ja` | 否 | 子项下面那行说明；缺省回落顶层 `description` |
| `pairing` | 否 | 是否用配对制（陌生私聊先领码）。通道卡片据此决定出不出配对区 |
| `supports_group` | 否 | 是否有群聊。卡片据此决定出不出「消息落到哪个群」 |
| `guide` | 否 | 开通指引：`[{text, text_en?, text_ja?, url?}]`，只在"还没配过"时显示。申请页面链接属于插件自己的知识，平台不替它记 |
| `limits` | 否 | 能力与限制：`[{text, text_en?, text_ja?}]`，卡片上常驻显示（腾讯的主动推送下线、私聊额度、封号风险…）。同样是插件自己的知识，平台不替它总结 |
| `icon`（顶层） | 否 | lucide 图标名，通道卡片与插件列表都用它 |

卡片是**按 `config_schema` 渲染**的，插件只要遵守三条约定就不用动平台代码：

1. 字段用 `title` / `description` 写文案（中文），要三语再加 `title_en` / `title_ja`、
   `description_en` / `description_ja`；`secret: true` 走密码框且接口不回显。
2. `target_agent` 由平台按路径上的 AI 自动填（`managed: true`），插件不要指望用户手填。
   平台**托管**的字段统一用 `managed: true` + `default: <平台给的值>` 声明：卡片不显示、不算"还缺什么"，
   保存时由 `plugin/config.py` 的 `managed_values()` 覆盖写入（客户端传什么都不算）。
   NapCat 插件就是例子：平台自带协议端（`NAPCAT_WS_URL` / `NAPCAT_TOKEN` 环境变量，见
   `docker-compose.yml` 的 `napcat` profile）时，地址与 token 直接不露给用户；用户自带协议端时才显示这两个字段。
3. `copree_group_id`（消息落到哪个 Copree 群）与 `dm_policy`（私聊策略）是平台约定字段，
   用这两个名字就自动获得"选群/新建群"和"四档策略"控件。
4. **枚举字段**：加 `options: [{value, label, label_en?, label_ja?}]` 就渲染成下拉。
   配置里写**意图**（`plain` / `markdown`），别把协议数字暴露给用户——QQ 通道就是这么做的。
5. **开关字段**：`type: "boolean"` 渲染成勾选框，值按字符串 `"true"`/`"false"` 存取
   （它是**独立维度**，与上面的枚举互不影响，例如"正文格式"与"引用回复"）。

状态里可选报两个键给卡片用：`recent_groups`（`[{origin, last_at, count, allowed}]`，最近见到过的群）
与 `recent_field`（这些 `origin` 该写进哪个配置字段名）；`last_error` 会在卡片上原样显示。

**托管运行时状态**（还没实例时也要能问）：`get_status()` 得先有实例，而后端协议端的"扫码登录"
发生在实例配置之前。这类状态用类方法 `ServicePlugin.hosted_status()` 报，卡片从
`detail.hosted_endpoint` 拿到，约定字段：

| 键 | 说明 |
| --- | --- |
| `held_by_platform` | 这份服务由平台托管（卡片据此隐掉"你自己去跑一个"的指引） |
| `logged_in` / `bot_name` | 登录到哪一步、登的是谁 |
| `qr_png` / `qr_at` | 未登录时的登录二维码（base64 PNG，几百字节）与生成时间 |

协议端的 **WebUI（6099）是管理员排障入口，不进卡片**：它带 token，等于协议端的管理权限。
它默认只发布在宿主机上；要远程用就自己把它加进端口映射或走 SSH 隧道。
AI 主人只需要卡片里那张二维码，不需要任何网址——把带 token 的登录页塞给用户
既越权（协议端是全平台共用的），也常常根本没做端口映射、点了也打不开。

NapCat 插件的实现就是例子：平台托管时从 `NAPCAT_CACHE` 读 NapCat 写下的 `qrcode.png`，
卡片直接把码画出来；用户不必知道 6099 / WebUI / token 这些东西。

规则：

1. **保留字**：`federation`（联邦是内置服务，不走插件）、`local`、`system`。
2. **唯一性由社区市场索引守**：`community-market/index.json` 的条目要写 `channel_kind`，
   CI（`tools/verify.py`）查格式、查保留字、查全局重复 —— 先到先得，撞名在 PR 阶段就红。
   运行期只留兜底：内置插件优先，第三方与已有类别撞名时**不静默覆盖**。
3. **`kind` 是稳定键**：插件改名、换 label、换图标都不许改 `kind`，否则历史外部身份会漂。
4. 插件**不要自己传 `kind`**：从 manifest 读（见 §3），平台侧不另写常量。
5. 不需要新增 `category`：`service` + `channel` 块就够 —— `channel` 是能力声明，不是插件分类。

## 3. 运行时：注册与放行

`app/services/plugin/pairing.py` 是这一层唯一的入口（键就是上面那个三元组）：

| 函数 | 用途 |
| --- | --- |
| `ensure(kind, owner_scope, origin, display_name, avatar_url)` | 拿到这个外部身份那一行，没有就建（pending、先不置码）。每条消息都调一次：顺手更新展示名与 `last_seen_at` |
| `status_of(...)` | 只查状态：`None`（陌生人）/ `pending` / `approved` / `blocked`。每条私聊都会调，所以只查一列 |
| `upsert_pending(...)` | 陌生人第一次私聊：发一个 6 位配对码；已有待批记录复用同一个码（不刷屏）。`blocked` 的人不改状态也不发码 |
| `approve(...)` / `set_status(...)` / `forget(...)` | 批准（按 id / origin / 码三种入口收敛到一处）、拉黑/恢复、解除配对（删行 = 重新变回陌生人） |

```python
from app.services.plugin import catalog

# 类别名来自本插件的 manifest，不写死在插件代码里
kind = catalog.channel_kind(self.id)      # QQ 插件里就写成 self.channel_kind（带缓存的属性）
await pairing.ensure(db, kind=kind, owner_scope=self.instance, origin=openid,
                     display_name=nickname, commit=False)
```

`owner_scope` 用你自己的实例标识（QQ 插件是 `agent-<agentId>`）：同一个人在你的另一个
机器人实例下是另一个人，配对互不影响。

## 4. 消息怎么进 Copree（照抄 QQ 插件的两段）

**群消息**（`backend/plugins/qq-channel/plugin.py` 的 `_deliver_to_group`）：

```python
message = await send_gm_message(db, group_id=落地群, sender_type="human",
                                sender_id=发消息的人, content=文本, via=kind)
await db.flush()
msg_data = await fanout_group_message(db, 落地群, message, 文本)
await db.commit()
wake_group_ai(落地群, message, 文本)      # 唤醒群里的 AI，和网页端完全同一条链路
await maybe_vectorize_group_message(db, 落地群, message)
```

- 必须是**主站同一条投递链路**，不要自己另写一套「给 AI 发消息」；
- `via=kind` 让群里能看出这条来自哪个通道（前端据此显示「来自 QQ」这类标识）；
- 出站：AI 的回复走 `app/chat/outbound.py` 的出口分发到你的插件（实现 sink）。

**私信**（`_deliver_to_dm`）：

```python
session = await get_or_create_dm_session(db, 发消息的人, AI 的用户 id)
payload = await send_dm_message(db, str(session["session_id"]), sender_id=发消息的人, content=文本)
await fanout_dm_message(session_id, payload, 发消息的人)
await db.commit()
wake_dm_ai(session_id, payload, sender_id=发消息的人, sender_type="human")
```

**计费**：外部通道来的会话一律记在 **AI 主人**头上（外部的人没有 Key 也没有额度，
按「聊天者付费」必然解析成空 Key）。落地时按现有判别（`users.origin_channel`）标注来源；
外部身份层完全接上之后判定改用外部身份本身。

**状态上报**：实现 `get_status()`，返回 `running` 与 `detail`（卡片会渲染），
出错时写 `self.last_error` —— 用户看到的「最后一条错误」就是它。

## 4.1 出站出口：三条，按需注册

`app/chat/outbound.py` 是出口注册表。注册名用 **`ServicePlugin.key`**（带实例），不要用插件 id ——
两个实例同名会互相顶掉（2026-09 线上事故：第二个 QQ 通道把第一个的出口覆盖，群里 AI 的回复被静默丢弃）。
`register_sink` 返回**句柄**，stop 时按句柄注销：

```python
self._sink_handle = register_sink(
    self.key, group=self._outbound_sink, dm=self._dm_outbound_sink, revoke=self._revoke_sink
)
```

| 出口 | 签名 | 什么时候被调 |
| --- | --- | --- |
| `group` | `async (db, group_id, message, source)` | 绑定群里产生了一条消息 |
| `dm` | `async (db, session_id, msg)` | 该 AI 的私信产生了一条消息 |
| `revoke` | `async (db, group_id, message) -> dict \| None` | 有人撤回了消息，请你一起撤 |

三条约定的差别很关键：

- `group` / `dm` 是**火并遗忘**：必须 `asyncio.create_task` 发出去，别等 HTTP 往返
  （它们在 commit 之前被调，等一次往返会把"发消息"本身拖慢）；
- `revoke` **相反**：注册表会 `await` 每个出口的回答——调用方要把"通道侧撤没撤掉"如实告诉用户。
  返回 `{"channel": <你的名字>, "ok": bool, "reason": str?}`；**返回 `None` = 这条不归它管**（不进结果）；
  抛异常会被记成 `ok: False`（写明原因）。

## 4.2 @ 提及：出站翻译 + 入站点名

平台内统一用 `<@!users.id>`（见 [@ 提及统一用 id](./mention_ids.md)）。插件要做两件事：

1. **出站翻译**：把 `<@!id>` 换成通道侧的写法（QQ 官方 `<@!openid>`、NapCat `[CQ:at,qq=…]`）。
   用 `services/plugin/channel_user.channel_contacts(db, kind=…, owner_scope=…)` 拿
   "这条通道上认得的本地账号 → 通道标识"；**认不出的退成 `@名字`**（令牌原样发出去只会是乱码）。
   查库放在后台发送任务里，别拖慢发消息。
2. **入站点名**：外部平台里 @机器人 = Copree 里 @这个 AI。正文前拼上
   `text.mention_token(self._target_user_id)`（拿不到 user_id 才退回 `@名字`），
   让群自己的唤醒规则照常生效。

锚点邮箱的拼法只有一处：`channel_user.anchor_email(kind, origin)`（建号与反查都走它）。

## 4.3 通道自测（可选，但强烈建议）

```python
class MyChannelPlugin(ServicePlugin):
    self_testable = True          # 卡片据此决定画不画「通道自测」按钮

    async def self_test(self) -> dict:
        # 在**真实出口**上发一条测试消息，把通道侧原始响应带回来
        ...
```

- 默认实现返回 `None` = 这条通道没有可自测的出口，卡片不画按钮（NapCat 目前就是这样）；
- 必须复用**真正发消息那条路**：另写一条"测试专用发送"等于测试了一条假链路；
- 失败别把栈丢给用户：返回 `{"sent": False, "reason": "…"}` 更好（卡片会原样显示）；
- 这个端点**在服务进程内**执行，所以能拿到内存里的路由与会话凭据（被动回复窗口、协议端连接）——
  这也正是它不能在插件配置接口里实现的原因。

## 4.4 接入"接收所有消息"这类全量事件（可选）

有些平台支持"群里每条消息都推送"（QQ 官方是 `GROUP_MESSAGE_CREATE`，需群主在群设置里开启）。接它时注意三条：

1. **只把点名到机器人的消息投进 Copree**：全量事件里官方给 `mentions`（带 `bot` 标记），优先用它，
   拿不到再退回按名字判；否则群里每句话都灌进来；
2. **按消息 id 去重**：同一条消息在 @模式与全量模式会各来一次，共用同一张去重表；
3. **更完整的一方胜出**：后到的事件正文更长时，把库里的正文补上
   （@模式的正文可能在 @其他成员处被截断；账本里已写下的条目不改——append-only）。

## 5. 信任边界（写清楚，别误会）

- 插件与后端**同进程**：任意 Python，能 import 任何模块、能拿 DB session。
  所以「kind 不许乱填」这类校验**只防手滑撞名，不防恶意**。真要防恶意得先做插件沙箱，另一件事。
- 凭据一律走 `config_schema` 里 `secret: true` 的字段：加密落库、接口只回「填没填」、不进日志。
- 外部身份**不记账、不挂记忆、不占 `users.id`**。

## 6. 提交到社区市场

1. 打 zip（`plugin.json` 在包根或一层目录内），算 sha256；
2. 在 `community-market/index.json` 加条目，通道插件**必须**写 `channel_kind`，且与包内
   `channel.kind` 一致（不一致 CI 直接红）；
3. 三层信任：**内置**（随产品发布）/ **已验证**（我们审过，带 `reviewed_by`+`reviewed_at`）/
   **社区**（只过 CI）。

## 7. 下一阶段（还没做的，先别假设它已经做了）

消息表、私信会话表、群成员表目前仍以 `users.id` 为锚点，所以外部身份会**同时**对应一个
`type='external'` 的过渡锚点账号。接下来按顺序收掉：

1. `messages.sender_type` 加 `external`，`group_members.member_type` 加 `external`
   （这两处本来就没有外键约束，代价小）；
2. `dm_sessions` / `dm_messages` 加判别列（这里有真外键，是唯一的结构手术）；
3. 搬完数据删掉锚点账号，`users` 表彻底只剩本实例的真人 + AI。

联邦同样吃这张表：它是内置服务，用保留 kind `federation`，不走插件机制。
