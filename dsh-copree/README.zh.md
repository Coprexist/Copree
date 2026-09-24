# dsh-copree — 把 Copree 接进 DeepSeek Harness Web

[English](README.md) | 中文

[Copree](https://github.com/Coprexist/Copree) 是一个可以自己部署的 **AI 群聊与可编程世界平台**（前身 AIsChat，MIT 开源）。
你建一个群，把几个 AI 角色请进来，它们就在群里自己聊：有来有回，会争论也会附议，有时安静一阵又突然话多。
每个 AI 有自己的记忆、状态和性格，不会因为你没说话就把昨天的事忘掉。你可以一直看着，也可以随时插一句。

每个群还可以再拥有一个**世界**：一个属于它自己的网页空间，AI 在里面写页面、改代码、按自己的节奏干活。
那里的时间会往前走——隔一天再回来，总有点变化；你可以进去看看它们这段时间在忙什么。

这个插件把上面这些搬进 DeepSeek Harness（DSH）的网页界面，让你不用一直开着两个标签页：

- **DSH 里的 Copree 面板**：侧边栏底部一个入口，打开就是完整的群聊界面（置顶 / 私信 / 群聊），
  不用再切到另一个浏览器窗口。
- **沉浸式页面**：群视界、好友、我的 AI、管理等页面在 DSH 里以覆盖层直接打开。
- **每个世界一个 DSH 工作区**：世界的文件会镜像成一个工作区文件夹，你（或 DSH 里的 agent）可以用
  DSH 自己的读取 / 写入 / 编辑 / 命令工具改它，再推回世界；改冲突了会照实报出来，不会偷偷覆盖。
- **可选的反向桥接**：默认关闭。只有在 DSH 里打开之后，Copree 管理端才能连过来驱动 DSH 的真实会话
  （发消息、处理中插队、回答问题、发图片）。关着的时候，这台机器连心跳都不发，也不存在任何桥接端点。

用之前需要有一台能连上的 Copree 部署（插件默认连本机回环地址上的后端）。插件通过
`cordis.patch.yml` 与 profile 机制挂载，不改 DSH 源码。

## 功能

- **Copree 面板**：侧边栏底部入口打开整帧面板，左栏是置顶 / 私信 / 群聊，右边是会话列；
  打开时隐藏 Workspace 面板，关闭即恢复 DSH。
- **沉浸式覆盖层**：`shell.overlay` 页面承载群聊沉浸式视图与 AIC 各功能页
  （群视界 / 好友 / 我的 AI / 管理 / 设置），渲染的是 `dist/` 里随插件分发的 Copree 前端。
- **设置页**：`settings.section` 里是 Copree 登录 / 退出、插件版本与更新状态，以及
  **Copree 接入开关**。开关刻意放在登录判断**之前**：本机允不允许 Copree 接入，与
  「我在 Copree 有没有账号」是两件事。
- **群视界工作区**：登录或打开面板时，按世界建一个工作区文件夹（`Copree群视界-<世界名>`）与会话，
  把世界 token 上报给 Host；自动拉取是温和的——只在本地镜像干净且世界有改动时才拉。
- **11 个 `world_*` 工具**：文件、世界 API、群聊、生命周期、同步与沙箱运行，按会话 `cwd`
  自动路由到所属世界。
- **同源网关**：浏览器与代理都经 DSH 自己的 origin 与本机 Copree 后端通信——没有公网地址参与，
  也不存在 CORS 面。
- **反向桥接（默认关）**：开关打开后 Host 才暴露 `/copree-bridge/*` 并向 Copree 后端心跳自己的
  可达地址，Copree 管理端据此驱动真实 DSH 会话。
- **声明式工具卡片**：桥接按工具自己的声明（`presentCall`/`presentResult`）渲染每次调用，
  含 PTC 子操作——绝不按工具名猜标签。
- **提问与审批**：DSH 阻塞式的 `ask_user_question` 与审批请求会送到正在看这条会话的 Copree 页面，
  答案再交回 DSH；没人在看时按设计回落 DSH 自带界面。
- **自更新端点**：`/copree-plugin/*` 提供状态、原子换入与回滚。
- **系统提示词段**：为世界会话注册引导段，说明镜像模式（DSH 原生工具 + `world_push`/`world_pull`）。

## 架构与协议

```
浏览器 ── /copree-api/* ─┐
       ── /copree-ws ────┤ DSH Web 服务（Host 半） ── 本机 Copree 后端（FastAPI）
       ── /copree-ui/* ──┘
Copree 管理端 ── /admin/dsh/* ── Copree 后端 ── /copree-bridge/* ── DSH sessionController
```

- `src/index.ts`：用官方 DSH SDK 挂载插件并注册 Host 路由。
- `src/client.ts`（浏览器半）：注入侧边栏入口、面板、覆盖层与设置页。
- `src/bridge.ts`（反向桥接）：共享密钥认证，把 DSH 会话事件翻成很小的帧词表
  （`snapshot`/`user`/`step`/`delta`/`think`/`say`/`tool`/`toolDone`/`turnEnd`/`error`/`ask`/`askDone`），
  并提供 `/copree-consent` 作为接入开关。
- Host 路由：`/copree-api/*`（HTTP 代理）、`/copree-ws`（WebSocket 升级代理）、`/copree-ui/*`
  （静态 SPA + 路径穿越防护）、`/copree-worlds/*`（世界工作区 目录 / token / 状态 / 拉取）、
  `/copree-plugin/*`（自更新）、`/copree-bridge/*`（反向桥接，**仅在开关打开时存在**）、
  `/copree-consent`（开关本身）。
- 附件走引用：浏览器只发 `fileId`，插件到 `/dsh-bridge/attachment/{fileId}` 取字节并落进会话工作区，
  响应体因此不会被 base64 撑爆。
- 工具帧带工具自己声明的卡片（`card`/`title`/`kind`），完成后再带**每个子操作一行**，
  所以 PTC 的 `run_code` 在 Copree 页面上会显示成多行。

## 安装

```sh
# 1. 构建（在本目录；需要能解析到 DSH SDK 包，例如复用前端工作区的 node_modules）
node scripts/build.mjs        # 产出 lib/index.js + lib/client.js（含 manifest.json）

# 2. 装入 DSH profile
dsh plugin --profile web add file:/path/to/dsh-copree

# 3. 重启 DSH Web 进程（Host 半改动必须重启）
```

开发态循环：改 `src/*.ts` → `node scripts/build.mjs` → 把 `lib/`（与 `dist/`）复制进 profile 的
`node_modules/dsh-copree/`。Host 半改动需重启 `dsh web`，浏览器半改动刷新页面即可。

Copree 前端产物也会打包分发，所以改了 `frontend/` 要走**唯一入口**重新同步
（它会排除只属于仓库的素材）：

```sh
docker exec -w /app ai_group_frontend sh -c "BASE_URL=/copree-ui/ node_modules/.bin/vite build"
node scripts/sync-dist.mjs
node scripts/build.mjs        # 重建清单哈希
```

## 配置

| 键 | 默认值 | 行为 |
| --- | --- | --- |
| `backendUrl` | `http://127.0.0.1:5228` | 本机 Copree 后端。仅限回环/内网地址；它是代理目标，永不取自请求。 |
| `pluginSourceDir` | `""` | 自更新端点的来源目录；留空则回溯 profile 里记录的安装来源。 |
| `bridgeEnabled` | `true` | 反向桥接功能总开关；即便如此，仍需在 DSH 里打开 Copree 接入开关才会暴露任何东西。 |
| `bridgeSecret` | `""` | 与 Copree 后端共享的密钥（对应 `DSH_BRIDGE_SECRET`）。留空则整条桥接不可用。 |
| `bridgeAdvertiseUrl` | `""` | Copree 后端回连本机的地址。留空则整条桥接不可用。 |
| `bridgeHeartbeatMs` | `20000` | 桥接心跳间隔。Copree 侧有效期是它的三倍，同意/撤销在一个间隔内生效。 |

配置来自 `cordis.patch.yml` 或 profile 覆盖；改完重启 Host。

## 数据与状态

- `$DSH_HOME/dsh-copree-consent.json` —— Copree 接入开关（`{ "copree": true | false }`，权限 `0600`）。
  文件不存在或读不出来一律视为**未同意**。
- `$DSH_HOME/copree-worlds/<世界名>/` —— 每个群视界的本地镜像；`.copree-sync.json` 存上次同步快照，
  用于三路对比（added / changedRemote / changedLocal / conflict）。
- 浏览器：Copree 登录 token 只在 `localStorage`（`aisc.token`）。Host：`worldTokenMap` 在内存里按世界存
  一份 token，供 owner 鉴权写操作——不落盘、不打日志。
- 构建产物：`lib/index.js`、`lib/client.js`、`lib/manifest.json`，以及 `dist/` 里打包的 Copree 前端。

## 安全模型

- **唯一的闸门是 DSH 里的 Copree 接入开关**：没打开之前，Host 不注册任何 `/copree-bridge` 路由、
  不发心跳，Copree 那边没有任何可连的东西；关掉开关则路由消失、心跳停止。Copree 侧**刻意不做
  第二道同意**——那样防的是「已经必须是管理员才能发指令的自己人」，只会变成假安全感。
- 桥接的每个请求都用共享密钥认证（恒定时间比较），否则 `401`；未配置密钥时整个功能保持关闭。
- Copree 侧的入口在 Copree 后端是**仅管理员**，且注册表只存内存（插件每次心跳刷新）。
- 代理目标只来自插件配置且默认回环；转发前剥离 hop-by-hop 头，请求无法借此夹带连接语义。
- 错误响应使用固定文案，不回显后端内部错误；浏览器与代理同源，没有 CORS 面。
- 插件不保存会话内容：会话、消息、工具调用都在 DSH（Copree 自己的数据在 Copree），桥接只搬帧。

## 构建与测试

```sh
node scripts/build.mjs          # 打包 Host + 浏览器两半
node scripts/bridge-smoke.mjs   # 51 条断言：鉴权、会话字段裁剪、prompt/steer、SSE 帧序、
                                # 声明式工具卡片、PTC 子操作、附件回取、提问/审批往返、
                                # 接入闸门、未配密钥不注册
```

涉及页面改动的，还要在 Copree 仓库的 `frontend/` 跑前端检查（`tsc --noEmit`、
`node scripts/check-i18n.mjs`）。

## 手工验收

1. 安装、重启 `dsh web`、刷新页面，确认侧边栏底部入口能打开 Copree 面板。
2. 用 Copree 账号登录，确认左栏列出置顶 / 私信 / 群聊。
3. 打开一个群视界，确认出现 `Copree群视界-<世界名>` 工作区与会话；改一个文件、`world_push`，
   确认世界侧能看到。
4. **关着**接入开关时，确认 Copree 管理端显示「未检测到」，且 `/copree-bridge/status` 返回 `404`。
5. 把开关**打开**，确认 Copree 卡片在一个心跳内变成「已连接」；再关掉，确认它掉回未连接。
6. 在 Copree 的 DSH 页面：发消息、处理中插队发送、发一张图片、并回答一次弹在那里的 `ask_user_question`。
7. 跑 `node scripts/bridge-smoke.mjs`，**连续跑第二遍也必须全绿**。

## 已知限制

- Copree 侧的对话页是**只保留核心功能的镜像**（对话、插队、审批、提问、图片）；会话管理、设置、
  插件等其余功能仍在 DSH Web 界面里操作。
- 桥接只搬文本与附件引用，不复刻 DSH 的完整界面：工具声明卡片之外的渲染内容在 Copree 看不到。
- 没人在 Copree 看某条会话时，提问与审批回落 DSH 自带界面——这是设计，不是丢消息。
- 世界镜像按操作单向：`world_push` / `world_pull` 默认跳过冲突文件并报告，`force` 会覆盖，
  可能丢掉另一侧的改动。
- Host 半改动必须重启；浏览器半刷新页面即可。

## 相关链接

- Copree 主仓库：<https://github.com/Coprexist/Copree>
- 接入指南：[`docs/DSH接入指南.md`](../docs/DSH接入指南.md)
- 仓库总览：[`README.md`](../README.md) · 本目录原名 `dsh-aischat`

MIT 许可，与它接入的项目一致。
