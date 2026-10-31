# B 站通道插件（bilibili）

> 状态：**已实现**（2026-10-31）
> 位置：`backend/plugins/bilibili/`（内置插件，同时以 verified 条目进社区索引）
> 参考实现：AstrBot `astrbot_plugin_bilibili`（动态订阅推送）、`bili-cli`/`bili-agent`（Agent 优先的 CLI）、DSH 的 bilibili 插件（链接解析）

## 1. 形态：为什么放在通道侧

B 站的登录态属于**某个人的 B 站账号**，而 Copree 里"谁在用什么外部账号"这件事由**通道**表达
（QQ 通道就是"一个机器人一个 AI"）。所以：

| | 普通插件（service 单实例） | 通道插件（本插件） |
|---|---|---|
| 配置归属 | 全局一份 | 每个 AI 一份（`instance = agent-<id>`） |
| 凭据 | 插件全局 Cookie | **这个 AI 的** Cookie，加密存在该实例配置里 |
| 谁登录 | 管理员 | **这个 AI 的创建者**自己扫码 |
| 卡片位置 | 能力与扩展 | 这个 AI 的通道页 |

工具按 `agent_id` 取该实例的登录态，所以同一个部署里多个 AI 可以接不同的 B 站账号。

## 2. 扫码登录（没有新增接口）

通道卡片的唯一约定是"插件报状态"（`get_status()` 的 detail）。登录因此做成**状态驱动**：

1. 没有 Cookie → 调 `passport.bilibili.com/x/passport-login/web/qrcode/generate`，把 `url` 放进
   `detail.login.qr_url`，同时带上 `expires_in`
2. 每次取状态顺手 poll 一次（`qrcode/poll`）：等待扫码 / 已扫码待确认 / 成功 / 失效
3. 成功时从响应的 `Set-Cookie` 取 `SESSDATA / bili_jct / DedeUserID`，用
   `plugin_config.set_config(instance=...)` 加密写回，然后调一次 nav 拿昵称
4. 有 Cookie 但 nav 说没登录 → 清空凭据、下次取状态重新出码

**为什么 `sessdata` 不是 required**：通道保存要求必填项齐全，若登录前就必填，
"没登录 → 不能保存 → 没有实例 → 出不了二维码"会死锁。

二维码会话按实例缓存（`_qr_sessions`）：反复刷新卡片不会换码——B 站会把旧码判失效。
登录态校验有 120 秒缓存（`_nav_cache`），卡片被反复拉取时不会把 nav 打爆。

## 3. 状态与工具

| 工具 | 登录 | 说明 |
|---|---|---|
| `bili_account` | — | 这个 AI 的登录态/昵称/UID |
| `bili_video` / `bili_hot` / `bili_user` / `bili_live` | 否 | 视频详情、热门、粉丝数、直播状态 |
| `bili_dynamics` | **是** | UP 主最新动态 |
| `bili_subscribe` | 否（推送要登录） | 订阅管理（按实例 + 群） |

未登录时 `bili_dynamics` 直接抛 `BiliError("这个 AI 还没有登录 B 站…")`，不静默降级。

## 4. 订阅推送

- 数据：`data/plugin_data/bilibili/subscriptions.json`（`instance / mid / group_id / name / last_ts`）
- 每实例一个轮询任务（`start()` 里起，`stop()` 里收），无订阅时空转不请求
- 推送：`send_gm_message(sender_type="ai", sender_id=群里某个 AI 的 user_id, source="world", via="bilibili")`
  - 群里没有 AI → 跳过（不留幽灵消息）
  - `source="world"`：不是"人发的"，不触发群消息钩子，避免自激
- 一个 UP 主一轮最多推 3 条，不补推

## 5. 接口依赖与实测口径（2026-10-31）

| 接口 | 登录 | 实测 |
|---|---|---|
| `x/web-interface/view` | 否 | 200 正常 |
| `x/web-interface/popular` | 否 | 200 正常 |
| `x/relation/stat` | 否 | 200 正常 |
| `live room/v1/Room/get_info` | 否 | 200 正常 |
| `x/polymer/web-dynamic/v1/feed/space` | 是 | 无 Cookie 时 `-412 request was banned` / HTML；wbi 签名也解不开 |
| `x/space/wbi/acc/info` | 是 | `-352 风控校验失败` |

结论：**动态必须有登录态**，所以插件把"扫码登录"做成前置能力，而不是可选装饰。
