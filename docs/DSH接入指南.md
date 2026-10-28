# Copree 接入 DeepSeek Harness（DSH）指南

> 面向：想用 DSH 直接操作 Copree 的开发者/用户
> 适用范围：dsh-copree 插件（v0.3.10+），Copree 作为插件嵌入 DSH Web

---

## 1. 这是什么

**dsh-copree** 把 Copree 以「一等公民」嵌入 DeepSeek Harness Web。接入后你可以：

| 能力 | 说明 |
|---|---|
| 💬 **聊天嵌入** | DSH 侧边栏点 Copree 直接聊（置顶私信 / 群聊 / 功能页），不必切换系统 |
| 🖼️ **沉浸式界面** | 群聊"沉浸式"按钮、AIC 功能页（群视界/好友/我的AI/管理/设置）以 iframe 全屏打开 |
| 🗂️ **世界工作区** | 每个 Copree 世界 = DSH 工作区一个文件夹 `Copree群视界-世界名`，**用 DSH 原生的 agent 和工具直接操作世界**（改代码、跑逻辑、读写群聊） |

核心设计一句话：**对话/工具/沙箱用 DSH 的，操作对象是 Copree 的（世界文件、数据、群聊）**。

---

## 2. 架构总览

插件是**双面 Cordis 插件**，同一 npm 包分 Host / Client 两半：

```
┌─ DSH Web 进程（Host 半边）──────────────────────────────────────┐
│  /copree-api/*   HTTP 代理 → Copree 后端（默认 127.0.0.1:5228）│
│  /copree-ws       WebSocket 升级代理 → 后端 /ws                  │
│  /copree-ui/*     前端静态托管（SPA 回退 + 防穿越）               │
│  /copree-worlds/* 世界工作区：dir / token / status / pull 端点   │
│  world_* 工具（11 个） 按会话所在世界目录自动路由                 │
│  systemPrompt 段    世界会话提示词（泛化引导）                    │
└──────────────────────────────────────────────────────────────────┘
        ▲ 同源（浏览器永不接触后端地址）
┌─ 浏览器（Client 半边）───────────────────────────────────────────┐
│  侧边栏 Copree 入口 → 全屏 board（rail + 对话列 + composer）      │
│  沉浸式覆盖层（iframe /copree-ui/...）                           │
│  世界同步：登录/打开面板时建文件夹+会话+上报 token+温和拉取         │
└──────────────────────────────────────────────────────────────────┘
```

**安全边界**：所有流量同源（`/copree-api` 等前缀由 DSH Web 服务），浏览器不持有后端地址；`backendUrl` 仅限本机回环，来自插件配置而非客户端输入。

---

## 3. 安装与部署

### 3.1 前置

- DSH Web 已运行（`dsh web`，如 `127.0.0.1:3080`）
- Copree 后端在**同一台机器**运行（默认 `127.0.0.1:5228`），其前端已按 `/copree-ui/` base 构建

### 3.2 构建插件

```bash
cd dsh-copree
pnpm install        # 或复用 node_modules
node scripts/build.mjs   # 产出 lib/index.js（Host）+ lib/client.js（Client）
```

插件自包含 `dist/`（Copree 前端 `BASE_URL=/copree-ui/` 构建产物），无需单独部署前端。

> `dist` **必须在 `package.json` 的 `files` 里**。打包管理器严格按该字段装包：
> 漏了 `dist`，`dsh plugin add` / 插件市场一键装出来的副本就没有 UI，`/copree-ui/`
> 直接 404。开发态手工拷 `lib/` + `dist/` 会掩盖这个问题。

### 3.3 装入 DSH

```bash
dsh plugin --profile web add file:/path/to/dsh-copree
systemctl restart dsh-web   # 或重启 dsh web 进程
```

### 3.4 配置（可选）

`cordis.patch.yml` / profile 覆盖：

```yaml
- insert:
    - id: dsh-copree
      name: dsh-copree
      config:
        backendUrl: http://127.0.0.1:5228   # 仅回环/内网
```

> 开发态改动同步：改 `src/*.ts` 后重跑 build，把 `lib/` 与 `dist/` 复制到
> profile 的 `node_modules/dsh-copree/`，Host 改动需重启 dsh-web，Client 改动刷新页面即可。

### 3.5 插件自更新

DSH 设置页的 **Copree** 分区底部新增一行插件版本信息，检测到不一致时出现 **更新** 按钮；
侧边栏底部的 Copree 入口在有更新时显示角标。

**身份用内容摘要，不用版本号。** `node scripts/build.mjs` 会产出 `lib/manifest.json`：
清单里每个产物带 sha256，对清单取摘要即该次构建的身份（同镜像 digest 的用法）。
因此不存在"忘了 bump 版本号导致检测不到"的情形——内容变了身份就变。

**更新源零配置。** 插件回溯安装来源：profile 的 `package.json` 里
`dependencies["dsh-copree"]` 的 `file:` 规格（pnpm/npm 记录的就是它）。
需要指向别处时再配 `pluginSourceDir`：

```yaml
- insert:
    - id: dsh-copree
      name: dsh-copree
      config:
        pluginSourceDir: /path/to/dsh-copree
```

**换入过程不会留半新半旧的状态**：先暂存并逐文件校验 sha256（源码改过但没重新构建会被
拒绝），通过后备份旧文件、逐文件原子改名，构建清单最后落盘作为提交点。上一次的状态保留在
`.copree-plugin-previous/`，可回滚。

**安装完整性单独检一次**：清单只记录"装了什么"，文件被包管理器裁掉后清单仍会声称一切正常
——所以 `status` 会逐个确认产物是否真的在（只做存在性检查，开销可忽略），缺失即报
`reason: incomplete`，而不是拿两份清单比对说"已是最新"。内容哈希留给换入阶段，那条路径
本来就要读全量文件。

**谁负责更新：按安装来源分三条通道**

本地构建与分发出去的副本不是一回事，硬用一套版本语义去管会互相覆盖。插件读
profile 的 `package.json` 判断自己是怎么装的，据此决定该由谁更新：

| 安装来源 | `installKind` | 通道 | 界面行为 |
|---|---|---|---|
| 本地目录（`file:` / `link:`） | `local-file` | `self` | 走本文这套内容寻址换入，出「更新」按钮 |
| npm 包，**且装了插件市场** | `npm` | `market` | 显示「推荐在设置 → 插件市场更新」，**不出手** |
| git 源，**且装了插件市场** | `git` | `market` | 同上 |
| npm / git 源，**没装市场** | `npm` / `git` | `package-manager` | 出「检查更新」，由 `dsh plugin update` 从原来源更新 |

第三、四行的判据是「市场（`dshmarket`）是否在 profile 的 dependencies 里」。本地安装
**永远优先走 `self`**——开发机上装了市场，也不该让市场去接管本地源码。

`package-manager` 通道**不重复实现版本比对**：npm 的 semver、git 的 ref 解析都归 pnpm，
插件只负责调用并如实回传输出。这和插件市场自己遵循的同一条原则一致——它也曾专门修过
「用 npm 包名去比对私有 git 源，结果把 git 安装覆盖掉」的问题。

**两种生效方式，界面会如实告知**：

| 变更范围 | applyMode | 生效方式 |
|---|---|---|
| 只动了 `lib/client.js` / `dist/` | `hot` | 自动刷新页面即生效 |
| 动了 `lib/index.js`（Host 半） | `restart` | 必须重启 dsh-web——它正被进程加载，覆盖文件不会热替换 |

插件不会自行重启宿主进程（那会掐断当前会话）。

**接口**（同为同源，无需认证）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/copree-plugin/status` | 已装/可用的身份、更新源解析依据、applyMode、后端版本漂移 |
| POST | `/copree-plugin/apply` | 用本地构建执行换入（`self` 通道） |
| POST | `/copree-plugin/update` | 调 `dsh plugin update` 从原来源更新（`package-manager` 通道） |
| POST | `/copree-plugin/rollback` | 回滚到上一次换入前 |

**后端版本漂移检测**：构建时把后端 `/health` 的 `version` 写进清单，运行时再比一次。
后端 API 变了而插件内置的 `dist/` 还是旧的，会在设置页直接提示——这正是"插件 UI 调用了
已不存在的接口"这类事故的成因。

离线验证（全程临时目录，不碰 profile 安装副本）：

```bash
cd dsh-copree
node scripts/update-test.mjs   # 换入 / 幂等 / 篡改拒绝 / restart 判定 / 回滚
node scripts/smoke.mjs         # Host 半挂到 mock webServer，验代理与插件端点
```

---

## 4. 世界工作区（核心特性）

### 4.1 概念

每个 Copree 世界对应 DSH 工作区一个**真实目录**：

```
$DSH_HOME/copree-worlds/Copree群视界-<世界名>/
├── .copree-world.json   # 世界身份（worldId/name，工具据此路由）
├── .copree-sync.json    # 同步快照（本地/远端 mtime）
├── index.html / main.py / blocks/ ...   # 世界文件「本地镜像」
```

**本地镜像 = 世界文件的本地副本**。agent 用 **DSH 原生的 read / write / edit / glob / grep / bash** 直接读写镜像（bash 可直接跑世界 Python 代码测试），改完 `world_push` 同步回 Copree 世界。

### 4.2 同步机制（GitHub 式）

`.copree-sync.json` 记录每个文件「上次同步时的本地 mtime / 远端 mtime」，每次同步做**三路对比**：

| 分类 | 含义 | 处理 |
|---|---|---|
| `added` | 世界上新增 | 自动拉取 |
| `changedRemote` | 世界改了、本地未改 | 自动拉取 |
| `changedLocal` | 本地改了、世界未改 | 推送 |
| `conflict` | 两边都改 | **不自动**，交 AI/用户裁决 |
| `removed` | 世界上删除 | 删除本地（本地未改时） |

**自动拉取（温和）**：打开 Copree 时自动执行，**仅当「本地无未推送修改且世界有改动」**才拉取，返回 `+N 新增 ~N 修改 -N 删除` 报告。本地脏/冲突一律拒绝，**绝不覆盖 agent 正在工作的文件**（对话进行中文件不会被自动改动）。

**实现要点（正确性保障）**：

- **快照只记录"实际同步成功"的文件**：`.copree-sync.json` 更新时只写入本次真正拉取/推送成功的文件，其余保留旧记录——未同步的本地修改、被跳过的冲突、远端新改动**绝不会被"洗白"成已同步**，下次对比仍能识别
- **`force:true` 语义**：拉取 = 完全以远端为准（覆盖冲突 + 本地修改），推送 = 完全以本地为准（覆盖冲突）；温和模式 = 任何一边有未同步改动都拒绝
- 快照文件自身、`__pycache__`、`.pyc` 等运行产物不计入对比（不误报"本地新增"）

**版本提示**：任何 `world_*` 工具执行后，若世界有更新未拉取 → 结果附 `updateHint`；有冲突 → 附 `conflictHint`——AI 看到就知道该 `world_pull` 或处理冲突。

### 4.3 典型工作流

```
① 打开 Copree（自动：建文件夹+会话+上报 token+温和拉取）
② 工作区点开 Copree群视界-世界名 会话
③ 对 DSH agent 说：
   "看看我的世界有什么文件"      → world_list_files
   "读一下 main.py"             → 原生 read
   "把 style.css 背景改深"      → 原生 write/edit
   "跑一下 main.py 测试"        → 原生 bash
   "同步到世界"                 → world_push
④ 世界在别处改过？"拉取最新"    → world_pull
```

### 4.4 冲突裁决

冲突文件（本地与世界都改了同一文件）**不盲目覆盖**：

- `world_push` / `world_pull` 默认**跳过冲突文件**并报告
- AI 读两边内容决定：保留本地（`world_push force:true`）/ 采用世界（`world_pull force:true`）/ 手动合并

---

## 5. 工具参考（Host 注册，按会话所在世界目录自动路由）

| 工具 | 作用 | 鉴权 |
|---|---|---|
| `world_list_files` | 列世界文件树 | owner token |
| `world_read_file` | 读世界文件内容 | 免鉴权 |
| `world_write_file` | 写世界文件（覆盖/建目录） | owner token |
| `world_delete_file` | 删世界文件 | owner token |
| `world_api` | 调世界受控 API（world/chat/memories/usage/groups/group-messages/state/data） | 世界沙箱 `api_token` |
| `world_chat` | 读写世界绑定群聊消息（以世界身份发送） | 沙箱 token |
| `world_lifecycle` | 唤醒/休眠世界 | owner token |
| `world_push` | 镜像改动 → 世界（快照对比 + 冲突保护 + force） | owner token |
| `world_pull` | 世界 → 镜像（快照对比 + 冲突保护 + force） | owner token（列树） |
| `world_run` | 后端沙箱跑 Python（24MB/10s 配额） | owner token |
| `world_trigger` | 触发世界入口 `handle(event)` | owner token |

> 大部分文件/API 操作也可直接用 DSH 原生工具（read/write/edit/bash）完成，`world_*` 是精确操作与同步用。

---

## 6. 沉浸式界面与功能导航

- **群聊头部「沉浸式」按钮**：自动查 `/worlds/by-entity` 绑定世界 → 打开 `/copree-ui/world-view/{id}?embed=1`
- **AIC 侧边栏「功能」分组**：群视界 / 好友 / 我的AI / 管理 / 设置，点击在覆盖层打开对应前端页面
- **嵌入模式**（`?embed=1`）：前端隐藏自身侧边栏，API 基址走 `/copree-api`；401 通知宿主；`?token=` 注入复用登录态；router basename `/copree-ui`
- **世界页内嵌群聊**：后端注入 `window.WORLD_API` / `WORLD_UI`（DSH 嵌入 = `/copree-api` / `/copree-ui`，独立部署默认不变），世界代码的群聊面板/平台菜单/SSE 正确走代理
- **对话页的「命令」按钮**（插件 0.7.2 起）：列这条 DSH 会话可用的斜杠命令并执行，
  命令不进模型、不占一轮对话。实现是插件经 cordis 依赖声明取 DSH 的 `commands` 服务，
  按会话的 agent 作用域调 `list` / `execute`（执行时带 AbortSignal）。
  服务不可用时页面明说「暂不可用」，原因写在插件日志里。

---

## 7. 安全与隐私

- **零公网地址**：插件代码、UI、代理目标全部 loopback；`backendUrl` 来自配置不接受客户端输入
- **token 仅内存**：client 登录后把 token 按 worldId 上报 host（`worldTokenMap`），供 owner 鉴权写操作；**不落盘、不打日志**；dsh-web 重启后需重新打开 Copree 同步
- **同源代理**：无 CORS 面；剥离 hop-by-hop 头防请求走私；错误响应固定文案不回显内部
- **世界沙箱 token**（`api_token`）：仅内部调用用，绝不回传给模型

---

## 8. 排障

| 现象 | 处理 |
|---|---|
| 世界工具报"未连接登录态" | 打开一次 Copree（触发 token 上报）；查 `GET /copree-worlds/status` 看 `tokenWorlds` 是否含该世界 |
| 工作区没有世界文件夹 | 确认登录 Copree；只同步**自己创建**的世界（`/worlds` 只返回 owner） |
| `world_push` 跳过冲突 | 冲突裁决：读两边内容，`force:true` 或手动合并 |
| 世界页打不开/显示宿主界面 | 世界无 index.html（提示"让群视界机器人生成"）；或路径未走 `/copree-api` |
| token 丢了（重启后） | 重新打开 Copree 面板触发同步 |
| 工具报"不属于任何 Copree 世界" | 会话 cwd 需在 `copree-worlds` 目录下（打开 Copree群视界-* 会话） |
| 对话页「命令」按钮说"还没有命令服务" | 插件不是 0.7.2+，或改完没重启 DSH Web（Host 半必须重启）；确认 `~/.dsh/profiles/web/node_modules/dsh-copree/package.json` 的 version，再看 DSH 日志里 `dsh-copree:` 那几行 |
| 命令列表空但会话有命令 | 命令挂在**活动会话**的 agent 上；先选一条会话（页面会提示"先选一条会话"），被清理掉的旧会话要先续接 |
| 装插件报 `ERR_PNPM_UNEXPECTED_STORE` | 见下方「DSH_HOME 是符号链接时的两个坑」第 1 条 |
| 装插件报 `ENOENT ... /data_s001/tmp/...` 或 `.../relocated/dsh-session-recovery` | 见下方第 2 条 |

### 8.1 DSH_HOME 是符号链接时的两个坑

如果 `~/.dsh` 是指向另一块盘（如 `/data_s001/...`）的**符号链接**，DSH 本身工作正常，
但 pnpm 会把"进程看到的路径"和"文件系统的真实路径"混用，于是出两类故障：

**1. store 位置不一致 → `ERR_PNPM_UNEXPECTED_STORE`**

pnpm 默认挑「与项目同盘」的 store。符号链接让项目真实落在另一块盘上，pnpm 就会选一个
与现有 `node_modules` 不同的 store。对策是在 profile 自己的配置里 pin 住（不动全局）：

```yaml
# ~/.dsh/profiles/web/pnpm-workspace.yaml
storeDir: /root/.local/share/pnpm/store
```

改完用 `pnpm store path` 确认解析结果的**版本子目录**与现有 `node_modules` 记录的一致。

**2. lockfile 里的相对路径少一层 → `ENOENT`**

pnpm 写 lockfile 按**符号链接路径**计算 `file:` 依赖的相对路径，解析时却按**真实路径**计算，
于是 `file:/tmp/x` 被记成 `../../../../tmp/x` 而不是 `../../../../../tmp/x`，
解析结果指向并不存在的 `/data_s001/tmp/x`。

修法是按 bug 模型重算（错误形式 = `relative(符号链接路径, 目标)`，正确形式 =
`relative(真实路径, 目标)`），工具已随插件提供：

```bash
node dsh-copree/scripts/fix-profile-links.mjs --dry-run   # 先看会改什么
node dsh-copree/scripts/fix-profile-links.mjs             # 修（自动备份 .bak-links）
```

它是幂等的：路径已正确时报 0 处改动，连续运行不会叠加。

> 注意手工 `sed` 修容易漏：同一条路径在 lockfile 里出现三次——`version: file:...`、
> 键 `name@file:...`、以及 `resolution: {directory: ...}`。漏掉最后一处 pnpm 照样报错。

**为什么不干脆改掉符号链接？** 两个更"彻底"的方案都被否决：

- 把 `DSH_HOME` 指到真实路径：`/root/.dsh` 这个字符串被 DSH 自己持久化在
  `storages/session_projcache*` 里，改路径会让历史会话的项目缓存对不上。
- 把符号链接换成 bind mount：需要改 fstab 并处理 `/data_s001` 的挂载顺序；而且一旦挂载
  失败会**静默启动到空目录**，比现在悬空软链（起不来、至少会失败）更危险。

结论是保留符号链接，按需跑上面的修复工具。

---

## 9. 与 Copree 独立部署的关系

Copree 本体（docker-compose / 源码）保持独立可部署；插件是**加装层**，不改动 Copree 部署方式。后端世界文件仍在后端容器/数据目录，DSH 侧只是镜像 + 同步。

---

*相关：`docs/dev/TODO.md`（开发待办）、CHANGELOG v0.3.10（特性记录）、`dsh-copree/README.md`（插件包内说明）*
