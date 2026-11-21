# NapCat 能给 AI 什么：分层接法

> 2026-11-16 调研结论。codebase 事实基于 `backend/plugins/qq-napcat/plugin.py`，
> 协议面核对 NapCat 官方扩展文档与 OneBot v11 规范，对照 AstrBot 的接法。

---

## 一、现状：一条通道的边界只有「收两条消息、发两种文本」

| 面 | 现在做到哪 |
|---|---|
| 入站 | 只认 `post_type=message` 且 `message_type ∈ {group, private}`；`notice` / `request` / `message_sent` 全丢 |
| 消息段 | 只解 `text` / `at` / `file`；`reply` 段**直接跳过**（引用内容与 message_id 都丢）；其余落占位符 |
| 图片 | 只从 `image` 段取 url 下载成附件 |
| 出站 | 只有 `send_group_msg` / `send_private_msg` + 纯文本；**丢弃返回的 message_id**；不引用、不富媒体、不主动发起 |
| 传输 | 出站优先复用已连的正向 WS，HTTP 只兜底 → **新增任何 action 的基建成本≈0** |

一句话：这条通道能对话，但**没有 id、没有感知、没有表达力**。而 id、感知、表达力是后面所有动作的前置条件。

---

## 二、四层接法

### 第一层：纯读（零外部副作用，先做）

| 能力 | action / 事件 | 为什么 |
|---|---|---|
| 入站 notice | `group_recall` / `friend_recall` / `notify.poke` / `group_increase` / `group_decrease` / `group_upload` / `group_msg_emoji_like` | AI 对「被撤回、被戳、有人进群、有人传文件、自己被踢」完全无感，这是「活着」和「照着台词念」的差别 |
| 引用还原 | `reply` 段 + `get_msg` | 现在引用内容直接丢，多 AI 群里不知道在回哪条 |
| 只读查询 | `get_group_member_info` / `get_msg` / `get_forward_msg` | 认人、判管理员、看懂合并转发；比任何群管理动作都更基础 |

**硬约束**：
- notice 落库但**不唤醒** AI（`send_gm_message` + `wake=False`），否则群成员进进出出就把额度烧完。
- `message_id` 是哈希 + LRU（约 5000 条）+ **重启即失**，不能指望靠 `get_msg` 反查历史。

### 第二层：表达力（只改「自己说的话」）

| 能力 | action / 段 | 注意 |
|---|---|---|
| 引用回复 | `reply` 段 | 需要先拿到并保存 message_id |
| 图片 / 表情 | `image` / `face` / `mface` 段 | 文件路径跨容器不通，走 `base64://` 或 URL |
| 表情回应 | `set_msg_emoji_like` | **AstrBot 核心与生态都没有**，接了就是差异点；emoji_id 需内置词表 |
| 戳回去 | `send_poke`（`friend_poke` / `group_poke`） | **只做「回戳」不做「主动戳」**；有冷却；群里要确认同群 |
| 撤回自己的话 | `delete_msg` | 只允许撤自己、2 分钟窗口内；先把 `_send_reply` 丢掉的 message_id 接住，存本地 LRU |

**安全前置（见第五节）**：出站字符串必须先做 CQ 转义，否则模型输出会变成指令。

### 第三层：群管理（改外部世界状态，一律押后）

`set_group_ban` / `set_group_whole_ban` / `set_group_kick` / `set_group_card` /
`set_group_admin` / `set_group_special_title` / `_send_group_notice` / `set_essence_msg` /
`set_friend_add_request` / `set_group_add_request` / `set_group_leave` / 群文件上传删除

**为什么不现在做**：平台现成的 `request_approval()` 绑定 `world_id + 前端广播`，**只在 world 作用域可用**；
QQ 群聊回合没有确认通道。没有确认闸门就放行，AI 迟早把别人的群搞乱。

权限位（真要做的前置知识）：禁言/踢人/全员禁言/公告/设精华 → 机器人须是管理员；
设头衔/解散群 → 须是群主；撤回他人消息 → 须管理员。否则 `retcode != 0`。

### 第四层：不接

| 能力 | 理由 |
|---|---|
| `send_like` 点赞 | 刷赞是腾讯最容易识别的高危行为，收益低、风控代价高 |
| `@全体` | 打扰全群，且有剩余次数限制 |
| 伪造 `node` | 能指定 user_id/nickname，等于可冒充他人发言 |
| `set_qq_avatar` 等自我改造 | 难回滚 |
| `mark_*_as_read` | 用户不可见的自嗨 |
| `set_group_leave(is_dismiss)` | 解散群，不可逆 |

---

## 三、实施顺序与前置依赖

1. **CQ 转义**（安全修复，与能力无关，应先做）——见第五节。
2. **入站 notice + reply 还原 + 只读查询**（纯读，零副作用）。
3. **富媒体出站 + 表情回应 + 回戳 + 撤回自己**。
4. **群管理**：等「群里问一句 + 仅主人私聊确认」的闸门设计出来再谈。

### 公认的坑（实现时逐条对）

- 唤醒预算：notice 全量唤醒会烧额额度。
- CQ 码注入：字符串出站不转义 = 模型输出直接变成指令。
- 多群共用一个落点：`self._route` 只记「最近来消息的群」，带群号的动作必须**显式传 group_id**。
- 群临时会话（`private` + `sub_type=group`）绕过「好友」假设，配对与额度策略要覆盖。
- 无数据库：历史消息、文件 URL 都会过期，「回头再看」要设计成一次性。
- Markdown 幻觉：不降级 ≠ 客户端真渲染；真 MD 要走合并转发双层包装。

---

## 四、NapCat 的能力面（备查）

- **OneBot 11 标准 38 个 action**：收发消息、`delete_msg`、`get_msg`、`get_forward_msg`、`send_like`、
  群管理全套（踢/禁/名片/管理员/头衔/退群）、加好友/加群审批、`get_*` 查询系列。
  注意：**禁言、踢人、改名片、设管理员、退群、头衔都是标准，不是扩展。**
- **NapCat 扩展**：`send_poke`、`set_msg_emoji_like`、`set_online_status`、`_send_group_notice`、
  `set_essence_msg`、`send_forward_msg`、`upload_group_file`、群/好友历史、`ocr_image`、
  `set_qq_avatar`、AI 语音三件套等。
- **事件**：`message` / `message_sent` / `request` 全量；`notice` 覆盖撤回、戳一戳、表情回应（仅自己）、
  入退群、禁言、管理员、名片、群文件、精华。

---

## 五、出站 CQ 转义（已实施）

出站 `message` 是一个普通字符串，协议端会把 `[CQ:…]` 当**指令**执行。
所以模型只要输出 `[CQ:at,qq=all]` 就能 @ 全体、输出 `[CQ:image,file=…]` 就能发图——
这是提示词注入直达外部动作的通道。

处置（`plugins/qq-napcat/plugin.py`）：

- 在 `_send_reply` 里，`strip_color_markup` 之后、`_translate_mentions` **之前**，把原文中所有
  `[CQ:…]` 转义成 `&#91;CQ:…&#93;`（CQ 标准转义，客户端显示为字面量）。
- 平台自己生成的 `[CQ:at,qq=…]` 由 `_translate_mentions` 在转义**之后**拼上，不受影响。

---

## 六、对照：AstrBot 是怎么接的

- 核心适配器只做「消息 ↔ 段」翻译，**不把 action 注册成 LLM 工具**；
- 工具化靠生态插件（`astrbot_plugin_qq_tools` 有 22 个工具模块），并且靠两个约定让模型能用：
  **上下文注入 `[MSG_ID:xxx]`**、用 **`[REPLY:message_id]`** 表示引用；
- 群管理工具默认关闭 + 权限控制，撤回内置角色与时间窗判断。

可抄的两点：**把 message_id 注入上下文**、**用标记而不是函数调用表达引用**。
表情回应（`set_msg_emoji_like`）AstrBot 核心与生态都缺，这是我们可以超车的一格。
