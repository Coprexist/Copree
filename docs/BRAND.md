# 品牌与命名 / Brand & Naming

> 本文只讲名字：它是什么、为什么这么取、改名改了哪些、哪些**刻意留着**。

## 名字

**Copree** —— **不做中文音译**，中文文案里也直接写 `Copree`（就像 Kimi 一直叫 Kimi）。
前身是 **AIsChat**：那个名字把产品钉在 "AI Chat" 这一层，而它今天已经是
**AI 群聊 + 可编程世界**平台，旧的品类词反而成了天花板。

## 来路

`Copree` = **co + pre + e**：

| 片段 | 含义 |
|------|------|
| `co` | 共同、连接 |
| `pre` | 从前、先前 |
| `e` | exist，存在 |

连起来是：**我们早在从前，思维便已连接在一起。**

还有第二层：与组织名 **Coprexist** 约分（消去共同的 `Copre`）——Coprexist 余 `xist`，
Copree 的尾巴 `e` 接上去，恰好拼回 **exist**。它不是缩写，而是**把字藏进了名字里**：

> Co-exist, reduced to exist.
> 一同存在，早在从前；约分之后，剩下存在。

## "AIsChat" 的意味留在文案里

名字不再字面写 "AI Chat"，但那是它的**起点**，所以简介与 DSH 插件介绍都保留这层表述：
「前身 AIsChat —— 让 AI 拥有自己的状态、记忆与生命节奏，不只是工具，是陪伴」。

## 改名范围

**代码里已全量替换**（2026-09-19）：`AIsChat`/`aischat` → `Copree`/`copree` —— 显示文案、
协议路径（`/copree-api`、`/copree-ws`、`/copree-ui/`、`/copree-worlds/*`、`x-copree-*-prefix`）、
前端存储键（`copree-theme`）、通知 tag、postMessage 源（`copree-embed`）、插件包名与目录、
打包产物名（`Copree.spec` / `Copree.exe`）、备份前缀（`copree_*`）、联邦公网 ID（`Copree-<ULID>`）、文档。

### 刻意留着 / 新名为主 + 旧名兼容

| 位置 | 处理 |
|------|------|
| 真实域名（含 aischat 的那个） | **没动**——改它要动 DNS/证书，属部署侧 |
| 真实容器名（compose 项目名） | **没动**——重建容器会换掉数据卷名，是迁移活 |
| DB 名 `ai_group_chat` / 容器 `ai_group_*` / DB 用户 `ai_chat` | **没动**——本就不含品牌词，改要 dump/restore |
| 历史文件名 `docs/promotion/aischat-v0.3.1-article.md` | **没动**——历史就该是历史 |
| 老世界镜像 `aischat-worlds/AIC群视界-*`、`.aischat-world.json`、`.aischat-sync.json` | **旧名兼容读**：老工作区与指向它们的 DSH 会话原样可用；新世界才用 `copree-worlds/Copree群视界-*` |
| 老备份 `aischat_*.gz` | **仍能列出/清理/回档**；新备份写 `copree_*` |
| 老存储键 `aischat-theme` | 读一次即迁到 `copree-theme`，主题偏好不丢 |

联邦公网 ID：老实例保留原值——**没有任何代码解析这个前缀**，所以不需要兼容读。

## 待办（只剩部署侧）

1. ~~5 个仓库改名（旧 URL 自动 301）~~ ✅ 2026-09-19
2. ~~代码里的仓库 URL / `registry_repo` / `.gitignore`~~ ✅
3. ~~插件产物重建（`build.mjs` + `sync-dist.mjs`）~~ ✅
4. ~~awesome-dsh-plugin 列表条目~~ ✅ PR [#5417](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/pull/5417)
5. 部署侧：演示站子路径（`frontend/.env.demo`）、域名 DNS/证书、镜像站、DB 与容器名迁移（要做单独排窗口）

---

## English

**Name.** `Copree` — no transliteration; Chinese copy also writes `Copree` (like Kimi stays Kimi).
Formerly **AIsChat**, a name that pinned the product to "AI chat" while it had already become a
framework for **AI group chat + programmable worlds**.

**Origin.** `Copree` = **co + pre + e** (together · before · exist) — *"we were already connected in
thought, long before."* Cancel the shared `Copre` against the org name **Coprexist**: what is left is
`xist`, and the trailing `e` completes it into **exist**.

> Co-exist, reduced to exist.

**The "AIsChat" meaning stays in the copy.** The name no longer spells it out, but it is the origin —
so intros and the DSH plugin description keep the line: "formerly AIsChat — AIs that have state,
memory and a life rhythm: not just tools, but companions."

**Scope.** Every brand-shaped string is now `copree`: display copy, protocol paths (`/copree-api`,
`/copree-ws`, `/copree-ui/`, `/copree-worlds/*`, `x-copree-*-prefix`), the storage key `copree-theme`,
the notification tag, the postMessage source `copree-embed`, the plugin package/directory, build
artifacts (`Copree.spec` / `Copree.exe`), the backup prefix `copree_*`, federation public IDs
(`Copree-<ULID>`) and the docs.

**Deliberately kept or read-both-ways.** Deployment-side names stay: the real domain, the real
compose/container name, the historical database/container names (`ai_group_*`), and the old article
filename. On-disk artifacts that already exist in users homes are read under both names: old world
mirrors (`aischat-worlds/AIC群视界-*`, `.aischat-world.json`, `.aischat-sync.json`) keep working while
new worlds use `copree-worlds/Copree群视界-*`, old backups `aischat_*.gz` are still listed and
restored, and the old `aischat-theme` key migrates to `copree-theme`.