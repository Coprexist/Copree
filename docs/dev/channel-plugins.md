# 写一个通道插件（把外部平台接到 Copree）

> 状态：**外部身份层已实现**（迁移 f7c1a2b3d4e5）；消息 / 私信会话 / 群成员改为引用外部身份是下一阶段。
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
| `pairing` | 否 | 是否用配对制（陌生私聊先领码）。通道卡片据此决定出不出配对区 |
| `supports_group` | 否 | 是否有群聊。卡片据此决定出不出「消息落到哪个群」 |
| `icon`（顶层） | 否 | lucide 图标名，通道卡片与插件列表都用它 |

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
