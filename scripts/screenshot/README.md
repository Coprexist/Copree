# 截图脚本 / Screenshots

一条命令生成 README 与文档里用的界面截图。所有接口响应在浏览器侧被换成
`demo-data.mjs` 里的演示数据，头像统一由脚本注入——**不写数据库、不改业务代码、
不引入任何第三方图片素材**，所以随时可以重跑。

---

## 中文

### 前置条件

- 后端与前端已在运行（默认前端 `http://127.0.0.1:5227`）
- 本机有 Chrome / Chromium（或用 `CHROME_PATH` 指定）
- Node 20+，**不需要安装任何依赖**（只用标准库）

### 运行

```bash
# 全套（8 张，约 50 秒）
node scripts/screenshot/run.mjs

# 只重拍某几张（--only 认逗号分隔的多个名字）
node scripts/screenshot/run.mjs --only design
node scripts/screenshot/run.mjs --only chat

# 组件规范配图（配 docs/dev/ui_system.md，输出到 docs/assets/screenshots/ui/）
node scripts/screenshot/run.mjs --only ui-components,ui-convlog

# 自定义地址 / 输出目录
node scripts/screenshot/run.mjs --url http://127.0.0.1:5227 --out docs/assets/screenshots
```

JWT 密钥默认从 `docker exec ai_group_backend printenv JWT_SECRET_KEY` 读取，
也可以直接用环境变量给：`JWT_SECRET=... node scripts/screenshot/run.mjs`。

### 数据是怎么被改掉的

| 位置 | 做法 |
| --- | --- |
| 人名 / 昵称 / 好友名 | 按值哈希稳定映射到虚构人名池，仓库里没有真实用户名对照表 |
| 头像 | AI 用团队自绘头像，一个角色一张脸（`frontend/public/official-avatars/`，名字→文件见 `demo-data.mjs` 的 `AI_AVATARS`）；人类用户、群「自定义头像」用脚本现场生成的字母/方块图（SVG） |
| 群聊 / 私聊消息 | 整体换成 `demo-data.mjs` 里的演示对话，列表摘要也不保留原文 |
| 邮箱 / GitHub / token | 统一替换为演示值 |
| 世界对话与工具卡片 | 演示回合，含 `role=tool` 的工具卡片（列文件 / 读文件 / 编辑文件） |

### 加一张新截图

在 `shots.mjs` 的 `SHOTS` 里加一行即可：

```js
{ name: 'friends', path: '/friends', settle: 4500, prepare: scrollBottom }
```

`prepare` 是截图前在页面里跑的脚本（点开文件、滚到底、关弹窗）。

一条记录可用的字段：

| 字段 | 作用 |
| --- | --- |
| `path` / `settle` | 站内路由；等页面稳定的毫秒数 |
| `prepare` | 截图前在页面里跑的脚本，可以给数组 |
| `clip` | CSS 选择器：只截这一个元素（组件规范配图用）；找不到会告警并退回整页 |
| `out` | 输出子目录 —— 组件配图住 `ui/`，不跟 README 那 8 张混在一起 |
| `rawApi` | 这一张**不替换**演示数据。控制台这类后台页只认真实登录态（演示用户 `role=user` 会被路由挡回 /chat）；**用前先确认图里没有任何个人数据** |
| `format` / `quality` | `png`（默认，文字密集）/ `jpeg`（满屏插画） |

组件规范配图写在另一个清单 `UI_SHOTS` 里（同一个 `shots.mjs`），不点名不会拍 ——
其中 `ui-components` 不是业务页面，而是用真实 `index.css` 的语义类当场渲染一屏控件速览
（`prepare` 里叠一层浮层），CSS 一改这张图就跟着变。

---

## English

One command renders every UI screenshot used by the README and docs. API
responses are rewritten in the browser to the fixtures in `demo-data.mjs`;
AI avatars are the team's own drawings in `docs/assets/brand/avatars/`, and
human avatars are generated on the fly — **no database writes, no product
code changes, no third-party image assets**.

### Requirements

- Backend and frontend running (frontend defaults to `http://127.0.0.1:5227`)
- Chrome / Chromium installed (override with `CHROME_PATH`)
- Node 20+; **no dependencies to install** (standard library only)

### Run

```bash
# everything (8 shots, ~50s)
node scripts/screenshot/run.mjs

# re-shoot selected shots (--only takes a comma-separated list)
node scripts/screenshot/run.mjs --only design

# component spec images for docs/dev/ui_system.md (written to docs/assets/screenshots/ui/)
node scripts/screenshot/run.mjs --only ui-components,ui-convlog

# custom target / output directory
node scripts/screenshot/run.mjs --url http://127.0.0.1:5227 --out docs/assets/screenshots
```

The JWT secret is read from `docker exec ai_group_backend printenv JWT_SECRET_KEY`
unless `JWT_SECRET` is set in the environment.

### Add a shot

Append one entry to `SHOTS` in `shots.mjs`; `prepare` is page script that runs
right before the capture (open a file, scroll to bottom, dismiss a dialog).

Per-shot fields: `path` / `settle`, `prepare`, `clip` (CSS selector — capture just
that element), `out` (output subdirectory), `rawApi` (skip the demo-data rewrite — needed
by admin pages, which bounce a demo `role=user` back to /chat; **make sure the image carries
no personal data**), `format` / `quality`. Component spec images live in a separate
`UI_SHOTS` list in the same file and are only taken when named.
