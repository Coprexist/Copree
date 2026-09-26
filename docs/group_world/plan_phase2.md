# 群视界阶段 2 规划：世界代码运行沙箱

> 状态：**2.1-2.5 全部完成**（2026-08-05：沙箱/触发/受控API/群聊写/常驻+SSE）；**沙箱加固 v2 完成**（2026-08-07：Landlock+seccomp+协议转发，见 world_skill_design.md 安全模型）｜更新：2026-08-07
> 前置阅读：`implementation.md`（实现现状与踩坑）、`design/group_world_design.md`（总设计）、`api/world_api_docs.md`（接口）
> 红线：**py 沙箱不解决，阶段 2 一切免谈**（世界代码 = 任意代码执行）

---

## 一、阶段 2 目标

世界从"活的网页 + 会干活的 AI"升级为**活的应用**：
- 世界文件夹里的 `.py` 代码能在服务器真正执行（事件/请求触发）
- 世界可以持续演化（后台常驻任务、NPC 自主活动、世界时间流动）
- 世界代码能安全地读写世界数据、与群聊交互

对照：阶段 1 的世界 AI 工具（file_edit 等）走**受控服务层**（白名单+隔离），不需要沙箱；阶段 2 的世界代码是**任意代码**，必须沙箱。

---

## 二、清单（按依赖顺序）

| # | 交付 | 说明 | 依赖 |
|---|------|------|------|
| 2.1 | **py 沙箱** | ✅ **MVP 已实现**（2026-08-05）：`world_sandbox.py` subprocess + rlimit + 超时 killpg 强杀 + env 白名单；`POST /worlds/{id}/run`（仅创建者）。**配额语义**：无人/后台 = `sleep_memory_mb`（默认 24MB）；**有人在线 = `runtime_memory_mb`（默认 128MB）**；超时/CPU/文件大小/进程数恒生效；RLIMIT_AS 是虚拟内存口径（实际可用堆 = 配额 - 解释器开销） | 无（最先） |
| 2.2 | **触发文件** | ✅ **已实现**（2026-08-05）：世界入口 `main.py` 实现 `handle(event) -> dict`（可 async）；平台 harness 导入调用（世界代码零框架依赖，print 重定向不污染结果）；`POST /worlds/{id}/trigger` + 世界 AI 工具 `run_world_code`（code 脚本或 event 触发模式）；6 项测试全过（同步/async/异常/缺handle/缺文件/超时） | 2.1 |
| 2.3 | **受控数据 API** | ✅ **已实现**（2026-08-05，与 2.4 一起）：`/world/{id}/api/*` 数据面（world/chat/memories/usage/groups）；每世界专属 token（懒生成存 worlds.config.api_token，沙箱 env 注入 WORLD_API_TOKEN/WORLD_API_BASE）；动态限流（基础+每人加成×活跃人数，4 个 config 字段可配）；复用 get_chat_history / app.tools.world.run_world_tool 同一份逻辑；12 项测试通过 | 2.1 |
| 2.4 | **群聊写 API** | ✅ **已实现**（2026-08-05，与 2.3 一起）：`/world/{id}/api/group/*`（读消息/发消息/成员/改角色/踢人）；身份=世界自身（底层借世界主人权限+群角色检查）；作用域=仅绑定群；写操作独立限流 | 2.3 |
| 2.5 | **后台常驻任务** | ✅ **已实现**（2026-08-05）：`world_resident.py` 常驻进程（handle/on_tick/on_stop + stdin 行协议）；config `resident:true`+`tick_interval` 开启；wake 启动/sleep 优雅停止/后端重启 restore；**实时状态通道**：POST /api/state（世界代码发布）+ GET /world/{id}/events（页面 SSE，零轮询）；默认不限常驻个数（预留可配）；配额 64MB（多解释器）/32MB 硬下限 | 2.1 |
| 2.6 | **世界 AI 记忆表** | ✅ **已实现**（2026-08-05）：`world_ai_memories` 专属表（title/content/embedding Vector(1536)）+ 工具 store_memory/recall_memory（向量检索 + 文本回退）；迁移 `b3c4d5e6f7a8` **待产品跑** | 无 |
| 2.7 | **缓存命中统计** | ✅ **已实现**（2026-08-05）：`world_llm_usage` 专属表，首轮（stream_options.include_usage）/工具轮/收尾轮全部落库；`GET /worlds/{id}/usage` 返回调用次数/token/命中率；设计页配置表单展示命中率；迁移 `c4d5e6f7a8b9` | 无 |
| 2.9 | **群消息钩子** | ✅ **已实现**（2026-08-05）：群消息 → 世界入口 `handle(event)` 异步感知（首条立即触发 + 2s 合并窗口，`group_trigger_interval` 可配；`source="world"` 防死循环）；唤醒改手动模式（AUTO_MANAGE=False，唤醒后保持活跃） | 2.2 |
| 2.8 | **会话状态服务** | 状态化会话管理器（不可变追加日志 + 规范化序列化 + 缓存统计 + 压缩管理）；为世界代码提供对话状态；DeepSeek 无服务端会话，本质是"请求形状稳定 + 可观测" | 2.3 配合 |

---

## 三、架构草图

```
用户/群聊/世界AI ──触发──▶ 世界调度器（复用 world_scheduler / world_turn 模式）
                              │
                              ▼
                    沙箱管理器（2.1：进程池 + 配额）
                              │ 启动世界进程，注入触发文件（2.2）
                              ▼
                    世界进程（隔离，24MB 内）
                              │ 调用受控 API（2.3）
                              ▼
              主实例 API 代理 ──JWT 校验/身份(触发者)/限流──▶ 世界数据/群聊
```

- 懒加载与常驻互补：交互型世界懒加载（现有），持续演化型世界后台常驻（2.5）
- 世界 AI 对话 worker（world_turn）可作为触发源之一（AI 调工具触发世界代码）

---

## 四、关键设计决策（开工前需定）

1. **沙箱技术选型**：
   - 方案 A：Docker 容器（每世界一个，镜像轻量）——隔离最强，启动慢，资源重
   - 方案 B：子进程 + seccomp/rlimit（Python subprocess + 资源限制）——轻量，隔离中等
   - 方案 C：受限解释器/wasm——最安全但能力受限
   - 倾向：MVP 用 B（快），生产用 A（稳）；**待定**
2. **触发文件约定**：入口文件/函数签名（如 `handle(event: dict) -> dict`）、生命周期（启动/每事件/定时）
3. **配额管理**：CPU 时间片、内存上限、网络白名单（只能访问主实例 API？）、执行超时
4. **世界代码依赖**：允许 pip 装包吗？装哪？（离线镜像？）
5. **安全边界**：世界代码能碰什么——文件系统（世界目录内？）、网络（仅主实例？）、系统调用（禁哪些）
6. **身份模型**：世界代码以谁的身份调用 API——世界主人？世界 AI？触发者？（设计文档已有：写操作以触发者身份）

---

## 五、与现有系统衔接

- **可复用**：world_turn（事件触发模式）、world_scheduler（调度）、world_blocks（代码分发）、world_file_service（文件隔离）、_friendly_llm_error（错误处理）、recover_orphaned_turn（健壮性模式）
- **需要新增**：沙箱管理器、受控 API 代理、群聊写 API、记忆表、会话状态服务
- **阶段 3 衔接**：世界线一致性（AI 绑定世界 + 跨入口共享世界数据）——依赖 2.3 的数据 API

---

## 六、当前状态快照（compact 参考，2026-08-05）

**阶段 1 已完成**：
- 世界实体（worlds/world_bindings/world_agents）+ 世界 AI 专属表 `world_ais`（非 agent、无账号）
- 对话：world_chat_messages（user/ai/tool/note + reasoning 保存）；服务器端 worker（world_turn：队列 + 直播 SSE pub/sub + 重连）；多轮工具循环（默认 50 轮，最后 3 轮提醒）；强制收尾轮 + finally/shield 落库；工作流记忆（中断 → worlds.config.workflow_memory → 下次注入继续）；回收机制（recover_orphaned_turn：重载后强制收尾孤儿轮次）
- 工具（与主站同名）：file_read/file_write/file_edit（共享 apply_file_edit 核心）/file_list/file_delete/update_world_info/compact_context/积木三件（list/view/apply_world_block）；温和去重（5 分钟窗口 + 结果一致）；请求日志（data/world_llm_requests/，最近 10 条/世界）
- 上下文：前缀稳定 + 动态尾部（缓存友好）；compact 摘要；当前时间尾部
- 变量注入 + WorldUI 桥；/preview 307 重定向（相对路径永远有效）
- 懒加载调度（world_scheduler：60s 扫描、10 分钟超时休眠、唤醒时间补偿）
- 积木：platform-sidebar（平台基础菜单必保留约定）
- 错误友好化（402/401/429/5xx）
- 前端：设计页（可拖拽/翻页/Markdown）、沉浸界面（独立窗口/侧边栏收起/悬浮球）、群聊全屏弹窗

**已知问题/遗留**：
- ⚠️ 后端 worker 被挂起 LLM 调用堵死世界队列（httpx 120s 超时理论上自愈，但重载杀 worker 会断；回收机制已补重载场景）——产品否了"加超时"方案，待定优雅解
- 前端 node_modules 残缺未构建验证（yarn dev 热更生效）
- 模型偶发不收敛（重复调工具）——温和去重 + 工作流记忆已缓解，观察中
- 世界 AI 记忆表待建（2.6）
- 阶段 1 的工具名与主站统一了（file_*），记忆工具名将统一为 store_memory/recall_memory

**迁移链**：c0c1c2c3c4c5 → ab0d1f883cee → c2d3e4f5a6b7 → d4e5f6a7b8c9 → e5f6a7b8c9d0 → f6a7b8c9d0e1 → a2b3c4d5e6f7（head）

**文档三件套**：design（设计稿）/ api（接口契约）/ implementation（实现现状）——本文件（plan_phase2）为阶段 2 规划

---

## 七、路线图估算（2026-08-05，产品两段愿景拆解）

| 阶段 | 内容 | 预估 | 状态 |
|---|---|---|---|
| 2.1 | py 沙箱（subprocess+rlimit+超时强杀） | 2-3 天 | **开工中** |
| 2.2-2.5 | 触发文件 / 受控数据 API / 群聊写 API / 后台常驻 | 5-8 天 | 待 |
| 群消息钩子 | 群消息 → 世界 AI 感知/响应 | 1 天 | 待 |
| 群聊 API 注入提示词 | 世界观/工具注入（design 已有 API 设计） | 1-2 天 | 待 |
| 文件式 skill/tool | 写在后端文件夹由实例识别提供（**方向**：world_command 等世界能力走此机制，不平台硬编码；明日开工） | 2-3 天 | **明日开工** |
| AI 绑定世界 | 跨入口一致环境（多空间模型接入，design §8.3） | 2-4 天 | 待（阶段 3） |
| 世界线一致性 | 同代码多世界线（design §8） | 3-5 天 | 待 |
| 商城 | 完整世界+组件块发布/搜索/一键添加+GitHub | 基础 2-3 天 / 社区 3-5 天+ | 待 |
| 游戏化/跨平台 | 卡牌、剧本杀、QQ 接入等 | 每项 3-7 天 | 依赖上表 |

**核心闭环（群消息感知 + AI 绑定世界 + 世界代码能跑）≈ 2 周；完整愿景 ≈ 1-2 个月持续迭代**
