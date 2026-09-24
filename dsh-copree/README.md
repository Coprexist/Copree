# dsh-copree

**Copree 的 DeepSeek Harness 原生集成插件**

[← 返回主仓库 Copree](https://github.com/Coprexist/Copree) ·
[DSH 接入指南](../docs/DSH接入指南.md) ·
[仓库总览](../README.md)

> 本目录只是 Copree 的一个**加装插件**（原目录名 `dsh-aischat`）。只想把 Copree 接进 DSH，往下读本页即可；
> 想了解 Copree 本身，请从 **[主仓库](https://github.com/Coprexist/Copree)** 开始。

## 关于主仓库 Copree

**[github.com/Coprexist/Copree](https://github.com/Coprexist/Copree)** —— MIT 开源，本插件的宿主项目。

- **是什么**：**AI 群聊与可编程世界的框架**（前身 **AIsChat**）。你建一个群聊，把几个 AI 角色邀进去，
  它们会自己聊起来——有来有回、有争论有附议，有时沉默有时话痨；每个 AI 有自己的记忆、状态与性格。
  你可以旁观，也可以随时插话。定位是"让 AI 拥有自己的生命节奏——不只是工具，是陪伴"。
- **群视界**：每个群聊可以再绑定一个"活的世界"——专属网页 + 世界 AI + 代码 + 时间。
  世界会持续演化，AI 在里面干活、改页面、按自己的节奏生活；本插件把这套世界工作区
  原生接进 DSH（见下文「世界工作区」）。
- **能跑在哪**：Docker 一条命令部署、Windows 安装包，或源码部署；主仓库 README 的「快速开始」
  有完整步骤，另有在线演示站可以直接点开看 UI。
- **文档**：用户手册、项目全景报告、DSH 接入指南都在主仓库 `docs/` 下（本页只讲插件这一层）。
- **技术栈**：后端 FastAPI + PostgreSQL，前端 React + TypeScript（Vite）。

本插件做的事只有一件：把 Copree 的聊天、沉浸式界面与群视界世界工作区，
以**原生体验**嵌进 DeepSeek Harness Web。

> 完整接入说明见仓库根目录 `docs/DSH接入指南.md`。

## 架构

双面插件：

- **Host 半**（`lib/index.js`）：在 DSH Web 服务上注册同源网关 + 世界工作区
  - `GET/POST /copree-api/*` → 代理到本机 Copree 后端（默认 `http://127.0.0.1:5228`，可配置）
  - `/copree-ws?token=...` → WebSocket 升级代理到后端 `/ws`
  - `/copree-ui/*` → Copree 前端静态托管（SPA 回退 + 路径穿越防护）
  - `/copree-worlds/*` → 世界工作区端点（dir 建目录 / token 上报 / status 诊断 / pull 拉取）
  - **11 个 `world_*` 工具**（文件/API/群聊/生命周期/同步/沙箱运行），按会话 cwd 自动路由到所属世界
  - systemPrompt 注册世界会话引导段（镜像模式：用 DSH 原生工具 + world_push 同步）
- **Client 半**（`lib/client.js`）：原生界面 + 世界同步
  - 侧边栏底部入口（`sidebar.footer.action`）+ 全屏 board（联系人 + 对话 + composer）
  - 沉浸式覆盖层（`shell.overlay`）：群聊"沉浸式"、AIC 功能页（群视界/好友/我的AI/管理/设置）
  - 世界同步：登录/打开面板时建 `Copree群视界-*` 工作区文件夹 + 会话 + 上报 token（按 worldId）+
    温和自动拉取（仅本地干净且世界有改动才拉，绝不覆盖本地修改）
  - 设置页（`settings.section`）：登录 / 退出 / 状态说明

登录 token 仅保存在浏览器 localStorage（client 侧 `aisc.token`）；host 内存 `worldTokenMap`
按 worldId 存一份供 owner 鉴权写操作（不落盘、不打日志）。

## 安装

```bash
# 1. 构建
pnpm install   # 或复用已有 node_modules（frontend 下）
node scripts/build.mjs   # 产出 lib/index.js + lib/client.js

# 2. 装入 DSH web profile
dsh plugin --profile web add file:/path/to/dsh-copree

# 3. 重启 DSH web 进程使插件生效
```

开发态改动：改 `src/*.ts` → `node scripts/build.mjs` → 复制 `lib/` 与 `dist/` 到 profile 的
`node_modules/dsh-copree/`；Host 改动需重启 dsh-web，Client 改动刷新页面即可。

前端产物（`dist/`）也会被插件打包分发，所以改了 `frontend/` 下的东西要重新同步，
且**只走一个入口**——它会排除只属于仓库的素材（`docs/assets` 的 README/推广图）：

```bash
docker exec -w /app ai_group_frontend sh -c "BASE_URL=/copree-ui/ node_modules/.bin/vite build"
node scripts/sync-dist.mjs
node scripts/build.mjs   # 重建清单里的产物哈希
```

## 配置

插件配置（`cordis.patch.yml` 或 profile 覆盖）：

```yaml
- insert:
    - id: dsh-copree
      name: dsh-copree
      config:
        backendUrl: http://127.0.0.1:5228
```

`backendUrl` 仅限本机回环/内网地址，不参与公网。

## 世界工作区（GitHub 式双向同步）

每个 Copree 世界 = DSH 工作区文件夹 `Copree群视界-世界名`，目录即世界文件的
**本地镜像**（`$DSH_HOME/copree-worlds/`）。agent 用 **DSH 原生工具**
（read/write/edit/bash）操作镜像，`world_push` 同步回世界，`world_pull` 拉最新。

- `.copree-sync.json` 快照 + 三路对比（added/changedRemote/changedLocal/conflict）
- 自动拉取仅当「本地无未推送修改且世界有改动」（温和，不覆盖 agent 工作文件）
- 冲突文件不盲目覆盖：push/pull 默认跳过并报告，`force:true` 强制；AI 读两边内容裁决
- 版本提示：world_* 工具结果附 `updateHint` / `conflictHint`

## 与 Copree 独立部署的关系

Copree 本体（docker-compose / 源码）保持独立可部署；本插件只是一个加装层，
不改动 Copree 的部署方式。后端世界文件仍在后端，DSH 侧只是镜像 + 同步。

## 安全要点

- 代理目标默认回环地址，且只来自插件配置，不接受客户端输入
- 转发前剥离 hop-by-hop 头（Connection / Transfer-Encoding 等），防请求走私
- 错误响应使用固定文案，不回显后端内部错误
- 浏览器与代理之间为同源请求，无 CORS 面
- token 仅内存（client localStorage / host worldTokenMap），不落盘、不打日志

---

<div align="center">
<sub>本目录是 Copree 的 DSH 插件 · 项目主体见
<a href="https://github.com/Coprexist/Copree">主仓库 Copree</a> ·
<a href="../README.md">仓库总览</a> · 旧目录名 <a href="../dsh-aischat/README.md">dsh-aischat</a></sub>
</div>
