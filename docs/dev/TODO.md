# 📋 开发待办

> 维护：本文件记录当前积压的开发任务。完成一项勾掉一项（`- [x]`）。
> 关联记忆：dsh-mneme 侧另有同步存档（侧边栏双板块重构：26d4421c）。

---

## ✅ 已完成（2026-08-20）

- [x] **DSH 插件 v1**（`dsh-copree`）：同源网关（`/copree-api` HTTP + `/copree-ws` WS 代理）+ 侧边栏 board + 设置页；已 commit `1f81c12`
- [x] **消息渲染照搬 DSH 风格**：官方 `MarkdownText`（GFM+KaTeX）、我方气泡 `--dsw-specific-bubble` 右对齐 + 名称靠右、图片附件 blob→objectURL、群聊邀请卡片
- [x] **401 自动登出**（不再静默"暂无联系人"）
- [x] **私信读取/发送修复**：改用带认证的 `/dm/{id}/messages`（后端 `/chat/messages` dm 分支硬编码 user_id=0、`/chat/message` 签名缺 dm_session_id——两个后端 bug，前端已绕开，后端已修复见文末备注）
- [x] **需求1：对话默认到底**（消息变化自动滚底）
- [x] **需求2：群视界沉浸式界面**——长线优雅方案：`/copree-ui` 静态托管（`BASE_URL=/copree-ui/` 构建 + SPA 回退 + 防穿越）+ iframe 沉浸式覆盖层；群聊头部自动查 `/worlds/by-entity` 显示"沉浸式"按钮；嵌入模式增强（API 基址走 `/copree-api`、401 通知宿主、`?token=` 注入、router basename 修复 404）
- [x] **需求3：私信/群聊设置页**（⚙：置顶/免打扰/公告/成员列表）
- [x] **需求4：AIC 功能导航**——AIC 侧边栏"功能"分组（群视界/好友/我的AI/管理/设置）+ DSH 设置页同款入口，点击在沉浸式覆盖层打开对应前端页面
- [x] **群聊名字解析修复**：成员表（含 AI）按 `type:id` 缓存名字
- [x] **iframe 登录引导**：token 失效时显示"请先在宿主应用中登录"引导页 → 通知宿主打开 Copree board 登录（不显示 Copree 自带登录表单）
- [x] **沉浸式内容修复**：WorldViewPage iframe 路径嵌入时走 `/copree-api`（否则被宿主 SPA fallback 接住显示 DSH 界面）；Host 代理重写 3xx Location 补前缀
- [x] **世界页内嵌群聊/平台菜单前缀**：后端注入 `window.WORLD_API` / `WORLD_UI`（经代理头），`chat-panel.js` / `sidebar.js` / `adventure.js` / `identity.js` 读取——独立部署默认值不受影响
- [x] **需求5：群视界世界嵌入 DSH 工作区**——每个自己创建的世界自动同步为工作区文件夹 `Copree群视界-世界名` + DSH 会话（官方 `workspaces.create` / `connectWorkspace`，幂等）；Host 注册 `world_*` 工具集（文件读写/世界 API/群聊/生命周期，按会话 cwd 路由）；token 仅内存；systemPrompt 泛化引导段
- [x] **需求5 浏览器实测（文件夹 ✓ / 工具 ✗）**：`Copree群视界-*` 文件夹出现在 DSH 工作区、会话 cwd 正确指向世界目录、`.copree-world.json` 可读（worldId 39 识别成功）；但 `world_list_files` 报"未连接登录态"
- [x] **token 上报 bug 修复**：client 同步里 `workspaces.create` 返回值主键是 **`workspaceId`**（不是 `id`），`ws.id` 恒 undefined → 跳过了 `connectWorkspace` + token 上报 → host 内存 `sessionTokenMap` 空（诊断端点 `/copree-worlds/status` 确认 tokenSessions=[]）。已改为 `ws.workspaceId || ws.id`
- [x] **诊断端点**：`GET /copree-worlds/status` → `{tokenWorlds, worldDirs}`（token 明文不返回）；token 上报打 `ctx.logger` 日志
- [x] **token 按世界路由**：改为 `{worldId, token}` 上报（sessionId 会被 DSH 新建会话流程更换，不稳定）；工具按 cwd 解析世界后取 token
- [x] **GitHub 式双向同步（世界文件本地镜像）**：`.copree-sync.json` 快照 + 三路对比（added/removed/changedRemote/changedLocal/conflict）；自动拉取仅当本地干净+世界有改动（温和，不覆盖 agent 工作文件）；world_pull/world_push 带冲突保护 + force；版本提示注入 updateHint/conflictHint；world_run/world_trigger 新增；agent 用 DSH 原生工具操作镜像 + world_push 同步
- [x] **需求5 全链路复测（全部通过）**：① tokenWorlds 非空（6 世界）✓ ② 工作区目录出现世界文件（自动拉取生效：星野镇10/星陨大陆32/棋盘大陆22/诗の子20；测试2/还没想好为远端空世界）✓ ③ world_* 工具注册+路由保护正常 ✓ ④ 冲突场景：温和拒绝不覆盖 ✓、force 覆盖冲突与本地修改 ✓、脏快照重建 ✓、远端改动自动拉取 ✓、无变化识别 ✓
- [x] **同步正确性三连修**（实测暴露）：① `.copree-sync.json` 自身未被排除 → 永远判"本地新增"→ 温和拉取永远拒绝（已排除）② force 拉取不含冲突/本地修改 → `ok:true` 却 `pulled:0`（已补齐）③ pull/push 后全量重建快照 → 未同步文件被"洗白"，温和拉取从此检测不到世界新版本（改为只更新实际同步成功的文件 + push 后重拉远端树写 rm）
- [ ] **内存 token 局限**：dsh-web 重启会清空内存 token，需重新打开 Copree 触发同步；后续可选持久化方案（如 host 侧加密落盘）

---

## 🔲 侧边栏双板块重构（dsh-copree 客户端架构改造——未实施）

> 用户 2026-08 凌晨提出，因当晚会话转向"消息渲染/设置页/沉浸式"需求而未实施，方案调研已完整存档。

### 需求

侧边栏两大板块**平级**：

1. **工作区** —— 本身做成可折叠/展开结构，展开后是工作区文件夹和对话（保持 DSH 现有工作区浏览）；
2. **Copree** —— 折叠展开外观与工作区**一模一样**，展开后排列置顶私信 / 置顶群聊 / 私信 / 群聊 + 联系人。

点开一个默认折叠另一个。这是对当前"全屏 board 方案"的优化：不再全屏覆盖，Copree 直接住在侧边栏里，与工作区平级。

### 官方 slot 架构硬约束（已验证）

- `sidebar.workspaces` 是 **single 槽**，被官方 WorkspaceBrowser（priority 0）占用，**不能 co-register**。
- `sidebar` 也是 single；替换整个侧边栏需重声明 children（sidebar.workspaces / sidebar.settings / sidebar.footer.action），但 children 已被官方声明，**重声明会 throw**（SlotCore.register 检查 `childRec.spec`）。
- 唯一官方机制：**用更低 priority（如 -2）"影子替换" `sidebar.workspaces`**，最低 priority 渲染，官方注册保留（删注册即还原）。
  - 社区验证：`dsh-organizer-sidebar`（npm）即用 priority -2 影子替换并完全自绘工作区浏览区域。

### 社区参考

- [build-deepseek-harness-plugin](https://github.com/oil-oil/build-deepseek-harness-plugin) `references/client-slots-and-theme.md`：sidebar 是 single、替换会丢子槽、优先 `sidebar.footer.action`；不默认替换 root/sidebar/conversation/details。
- `dsh-organizer-sidebar`（npm，v1.0.6）：`priority: -2` 注册 `sidebar.workspaces`，自绘整个浏览区域，官方注册保留。**验证了影子替换可行**。
- `@linxin666/dsh-client-ui-task-board`：DOM hack 插侧边栏入口（`document.querySelector("[data-pane=sidebar]")`）——**不优雅，排除**。
- 官方 master 无侧边栏板块切换（tab/board）类槽，官方不打算做多板块。

### 推荐方案（待确认两点后实施）

影子替换 `sidebar.workspaces`（priority -2），自绘"双板块容器"：

1. **板块头区**：`工作区 | Copree` 两个折叠展开按钮，外观完全仿官方板块头（sectionHeader 样式 + 文件夹 chevron）。
2. **工作区面板**：用官方 hooks（`useSessions` / `useWorkspaces` standard props）自绘工作区文件夹 + 对话列表，点击仍打开 DSH 会话。
3. **Copree 面板**：复用现有联系人列表（置顶私信/置顶群聊/私信/群聊 + 联系人），点击后在主区域渲染对话 + composer（发送到选中的 Copree 对话，不碰 DSH 会话语义）。

### 代价 / 风险

- 替换后工作区浏览 = **自绘简版**：官方搜索、拖拽排序、右键菜单、新建工作区对话框等功能会丢失，除非额外复刻。
- 若**完整保留官方工作区全部功能**，则需换设计（Copree 只做折叠展开入口 + 联系人列表，与官方工作区上下排列）——交互上不完全是"平级板块"。

### 待用户决策

- [ ] **a) 板块交互细节**：互斥折叠（点开一个折叠另一个）vs 各自独立折叠？
- [ ] **b) 工作区面板**：自绘简版（功能有损）vs 完整复刻官方（工作量大）？

### 实施步骤（决策后）

- [ ] 1. 改 `dsh-copree` 客户端：去掉全屏 board（shell.overlay 方案），改为 sidebar 内双板块容器
- [ ] 2. 影子替换 `sidebar.workspaces`（priority -2），自绘板块头 + 工作区列表 + Copree 列表
- [ ] 3. 主区域渲染：选中 Copree 对话时主区域显示该对话（沿用现有消息/composer 逻辑）
- [ ] 4. 重建（scripts/build.mjs）→ 同步 profile（/root/.dsh/profiles/web/node_modules/dsh-copree/）→ 页面刷新验证
- [ ] 5. 验证：工作区可正常开会话；Copree 板块外观与工作区一致；点开一个折叠另一个；聊天收发正常

---

## 🔲 阶段 2 插件化跟进项（可选）

- [ ] world 分类落地（协议里已留占位，本阶段不实现 —— YAGNI）
- [ ] 前端插件 UI（皮肤/技能插件的用户配置界面）
- [ ] Git 提交本轮变更（提交前检查 .gitignore 排除敏感文件：.env、token.txt 等）

---

## 📌 备注

- 后端遗留 bug 已修复（2026-09-13）：`/chat/messages` 的 dm 分支硬编码 `user_id=0`、`/chat/message` 签名缺 `dm_session_id` —— 两项都随**删除重复入口**一并消失。群消息现在只有一个入口 `GET/POST /gm/{group_id}/messages`，与 `/dm/{session_id}/messages` 同形（带认证、游标分页、返回列表）。插件已改用它，不再需要绕开。
