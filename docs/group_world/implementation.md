# 群视界（Group World）实现文档

> 状态：阶段 2 核心完成（2.1-2.5：沙箱/触发/受控API/群聊写/常驻+SSE）｜更新：2026-08-05
> 定位：**实现现状与架构**（设计见 `design/group_world_design.md`，接口见 `api/world_api_docs.md`，决策与踩坑见 §十二）

---

## 一、一句话

**世界 = 可编程的网页 + 一个专属世界 AI（群视界机器人）**。群聊绑定世界后，成员可从聊天页进入"沉浸界面"体验世界；世界 AI 能读档案、改世界信息、建文件、查/用积木，全程服务器端执行。

核心设计决策：
- **世界 AI 不是 agent**：无账号、无好友关系、不进 agents 表——独立实体表 `world_ais`
- **世界 AI 的表全部专属**：world_ais / world_chat_messages / 未来 world_ai_memories，不往主系统表塞类型
- **对话轮次服务器端全程执行**：不依赖网页连接存活；断开/刷新不丢，重连可"加入直播"

---

## 二、数据模型（迁移链 head = f2b3c4d5e6f7，2026-08-08）

| 表 | 职责 | 关键字段 |
|----|------|---------|
| `worlds` | 世界实体 | name / description / owner_id / status(active\|sleeping) / time_flow_rate / world_time / last_active_at / config(JSONB：sleep_memory_mb、chat_summary、workflow_memory) |
| `world_bindings` | 入口绑定（群聊/私信/用户 ↔ 世界，多对多） | world_id / entity_type / entity_id |
| `world_agents` | 世界居民 AI（**真 agent 入驻**，阶段 3 用） | world_id / agent_id / role |
| `world_ais` | **世界 AI 实体（每世界一个）** | world_id(唯一) / name / system_prompt / model / temperature / top_p / thinking / max_tool_rounds |
| `world_chat_messages` | 世界 AI 对话 | world_id / user_id / role(user\|ai\|tool\|note) / content / reasoning（思考过程，展示用不进上下文） |
| `world_ai_memories` | **世界 AI 长期记忆（2.6）** | world_id / title / content / embedding(Vector 1536) / 时间戳 |
| `world_llm_usage` | **LLM 用量与缓存命中（2.7）** | world_id / turn_id / round_no(0\|N\|final) / model / prompt / completion / reasoning / cached_tokens |
| `world_market_items` | **世界商城商品（8-07 MVP + 8-08 同步）** | kind(world\|block) / title / description / tags(JSONB) / author_id / author_name / source_world_id / package_path / package_size / downloads / status(on\|off) / source(local\|github) / github_path(同步目录) |
| `users`（GitHub 绑定，8-08） | **用户 GitHub 身份与签名** | github_token_encrypted（加密）/ github_username / github_id（**数字 id 身份锚**）/ github_sign_key_encrypted（Ed25519 私钥加密）/ github_public_key |

迁移链：`c0c1c2c3c4c5 → ab0d1f883cee → c2d3e4f5a6b7 → d4e5f6a7b8c9 → e5f6a7b8c9d0 → f6a7b8c9d0e1 → a2b3c4d5e6f7 → b3c4d5e6f7a8 → c4d5e6f7a8b9 → b5c6d7e8f9a0 → c6d7e8f9a0b1 → d7e8f9a0b1c2 → d9e8f7a6b5c4（world_market_items）→ e4f5a6b7c8d9（market source/github_path + market_config）→ f1a2b3c4d5e6（users github 绑定）→ f2b3c4d5e6f7（users github_id + 签名密钥）`

---

## 三、世界 AI 对话架构（服务器端 worker）

```
POST /worlds/{id}/chat ──入队──▶ WorldTurnWorker（每世界一个，独立 DB 会话）
                                    │ 逐条处理消息队列（DM 同款并发排队）
                                    ▼
                            stream_world_chat（完整轮次）
                              ├─ 首轮流式（SSE 透传 + 思考 [REASONING]）
                              ├─ 多轮工具循环（最多 max_tool_rounds，默认 50，最后 3 轮提醒收尾）
                              ├─ 强制收尾轮（不带 tools，保证必有最终回复）
                              └─ finally + shield：中断/取消也落库（历史必有闭环）
                                    │ 事件广播给所有订阅者（发布/订阅）
GET /worlds/{id}/chat/stream?turn_id= ──订阅直播（30s 心跳；断开重连=重新订阅）
```

要点：
- **并发排队**：上一轮还在跑时发新消息 → 入队，返回 `{turn_id, queued, position}`，前端显示排队位置
- **直播可重连**：SSE 断开不影响轮次（服务器照跑）；重连订阅即"加入直播"，缺口由历史（DB）补齐
- **中断恢复（工作流记忆）**：轮次无最终回复中断 → `worlds.config.workflow_memory` 记录已执行工具步骤；下次对话注入「【未完成工作流】…请继续完成，不要重复」，正常完成自动清除
- **出错闭环**：工具执行错误落「（对话中断：工具执行出错…）」；余额不足等错误友好提示（见 §八）

### 事件协议（SSE，`data: xxx\n\n`）
| 事件 | 含义 |
|------|------|
| `data: <增量>` | 正文逐 token（换行 `{NL}` 占位） |
| `data: [REASONING]<增量>` | 思考过程（工具轮也显示） |
| `data: [TOOL]{json}` | 工具执行结果 → 前端居中气泡（🔧） |
| `data: [ERROR]<友好信息>` | 错误 |
| `data: [DONE]` | 轮次结束 |

---

## 四、工具系统（与主站同名同义，执行限世界作用域）

| 工具 | 说明 |
|------|------|
| `file_read` / `file_write` / `file_edit` / `file_list` / `file_delete` | 世界文件夹读写（隔离目录+类型策略+防越界）；**file_edit 与主站共用同一份编辑核心** `app/utils/pure/file_edit.py`（str_replace 唯一性校验 / insert 行语义 / delete_lines 边界） |
| `file_move` / `file_copy` | **移动/重命名与复制（2026-09-15）**：走同一套越界与后缀校验；目录可整体搬移/复制 |
| `web_download` | **下载文件（2026-09-15 收敛）**：固定落点 `downloads/`、GitHub blob → raw 直链、违规内容拦截（`world_moderation`）、大小 32MB；审阅模式下由平台弹窗征得同意 |
| `ask_user` / `present_plan` | **审批与协作（2026-09-15）**：`ask_user` 事件类型关键词必填（download/delete/modify/other）弹窗问用户；`present_plan` 计划模式下先出计划、通过后本轮按自动模式执行——与平台门禁共用同一条审批通道 |
| `update_world_info` | 改世界名/简介（以世界主人身份） |
| `compact_context` | 压缩对话历史 → 存 `worlds.config.chat_summary`（复用主站 context_compression_service） |
| `list_world_blocks` / `view_world_block` / `apply_world_block` | 积木体系（§七） |
| `view_api_doc` | **接口文档分区查看（2026-08-05）**：工具描述只列区名+区介绍，AI 按需传区号（01~10）取详细 API（读 `backend/app/services/world/api_docs/sections/`，注册表白名单防穿越） |
| `store_memory` / `recall_memory` | **世界 AI 长期记忆（2.6）**：store 存 title+content（向量化失败降级无向量）；recall 语义检索（embedding <=> 余弦距离）→ 失败文本包含回退；世界按 world_id 隔离 |
| `get_bound_groups` / `get_group_messages` / `list_group_members` / `send_group_message` / `set_group_member_role` / `kick_group_member` | **群聊 API（以世界创建者身份）**：默认作用本世界绑定群，无需传群号；管理操作仅群主/管理员（§十一） |
| `web_search` / `web_fetch` | **上网（2026-08-05）**：**复用主系统同一份实现**（WebSearch/WebFetch 类，无 opencli 依赖）；web_fetch 支持 `delay_ms` 延迟抓取（AI 可设定等待后再取，应对慢速/动态加载页面）。**官方失败自动轮播国内镜像**（2026-09-16，表见 `file_operations/mirror_table.py`）：GitHub raw/release/压缩包、npm、PyPI、HuggingFace 直连失败时按序试镜像，结果里带 `via`；`web_download` 走同一条管线 |
| `POST /worlds/{id}/run`（端点） | **py 沙箱（2.1 MVP）**：subprocess + rlimit（有人在线 128MB / 无人后台 24MB）+ 超时 killpg 强杀 + env 白名单（不泄漏 DATABASE_URL/JWT 等）；配额 worlds.config 可配；生产加固后置（seccomp/Landlock） |
| `POST /worlds/{id}/trigger` + 工具 `run_world_code` | **触发文件（2.2）**：世界 `main.py` 实现 `handle(event)`（可 async），平台 harness 导入调用（世界代码零框架依赖）；世界 AI 可自测代码（code 脚本 / event 触发模式） |

**温和去重**（`execute_world_tool` 包装）：
- 同工具同参数、**5 分钟内**重复且**结果完全一致** → 提示「⏭ 已跳过…」不硬拦
- 结果变化（如写完文件后 list 看到新文件=验证场景）→ 正常执行
- 超过 5 分钟（用户可能改了文件）→ 允许重跑

**安全**：文件走 `world_file_service`（`data/worlds/{id}/` 隔离、`..` 拒绝、允许清单 + 禁用后缀三层防护）；工具以世界主人身份执行写操作；所有设计页端点仅创建者可调（`_require_owner`）。

**运行模式门禁（2026-09-15）**：世界有 `auto`（自动）/ `review`（审阅，默认）/ `plan`（计划）三档，由用户在设计页切换、**AI 无工具可改**。审阅模式下下载/删除/改动机制三类操作由 `world_ai_mode.gate_tool_call` 弹窗征得用户同意后执行（同类本轮一次），计划模式下先 `present_plan` 通过再按自动模式执行。

---

## 五、上下文与缓存

- **前缀稳定**：静态 system（档案+提示词+工具约定）→ 摘要 → 历史 → 用户消息；动态信息（懒通知/压缩提示/当前时间）全部放**末尾独立 system 消息**（与主对话同规则，不破坏 prefix cache）
- **当前时间**：`## 当前时间` 尾部注入（display_timezone，v4 思考默认开是正常行为）
- **compact**：接近上限（128K 的 60%）提示 AI 调 `compact_context` → 摘要存库 → 下次只发「摘要+最近 10 条」；一次压缩一次 miss，之后稳定命中
  - 触发提示的门槛 `WORLD_CONTEXT_MIN_MESSAGES` 必须**高于**保留窗口 `WORLD_CHAT_KEEP_LAST`：
    真实消息数 ≤ 保留窗口时压缩是空操作，门槛低于窗口就会"提示 AI 去压、压了却无可压缩"（旧值 6 < 10 踩过）
  - 工具的"可压缩性"按**过滤后的真实条数**判断（tool/note 不进 LLM 上下文，也不是可压内容）；
    空操作如实回「无需压缩」，**不算执行失败**——它读的是已落库的历史，与"本轮是否结束"无关
  - 注意世界每次只带最近 `CHAT_HISTORY_LIMIT = 30` 条，token 提示阈值（128K 的 60%）几乎不会触发，
    world AI 多半是**自己想起来**调 compact 的；此时上面的门槛检查就是唯一的护栏
- **请求日志**：每次发往 DeepSeek 的完整请求落盘 `data/world_llm_requests/{world_id}.jsonl`（每世界最近 10 条，含 turn_id/round 标记），排查问题直接看模型收到了什么

---

## 六、世界变量注入 + UI 桥

后端在服务世界 HTML（`/world/{id}/files/*.html` 与 `/preview`）时自动注入：

```js
window.WORLD_ID = 2;            // 世界编号
window.WORLD_AI_ID = 'world-2'; // 世界 AI 身份
window.WORLD_AI_NAME = '群视界机器人';
window.GROUP_ID = null;         // 入口群聊（?group_id= 传入）
window.USER_ID = null;          // 宿主环境补
window.WorldUI = {              // UI 桥（postMessage → 宿主 Layout）
  toggleSidebar, showSidebar, hideSidebar,
  hideFloatingIcon, showFloatingIcon,
};
```

- `/world/{id}/preview` → **307 重定向**到 `/world/{id}/files/index.html`（单一规范挂载点，页面内相对路径永远有效，打包即插即用）
- **打包原则**：世界代码零硬编码，一切标识走变量；相对路径引用资源（支持跨文件夹 `../`）

---

## 七、世界块体系（积木）

- 积木包：`data/world_blocks/{block_id}/`（manifest.json + 自包含 css/js）
- **分块结构**（2026-08-12）：应用后世界目录 `blocks/{id}/` = 主文件（平台管，可更新覆盖）+ `diy/` 用户定制区（`custom.css`/`custom.js` 自动加载、更新跳过不覆盖）+ `.bak/` 更新备份（旧主文件可回滚）
- **DIY 约定**：改样式/逻辑写 `diy/custom.css`|`diy/custom.js`（加载顺序在主文件后），**不改主文件**（更新会覆盖主文件但保留 diy/）
- **更新机制**：重新 apply 检测版本变化 → 懒通知注入世界 AI（「积木已更新 vX → vY，你的 DIY 已保留」）；管理员可 `POST /admin/blocks/{id}/update` 批量更新所有使用世界；主文件更新必须向后兼容（类名/CSS 变量/钩子只增不改）
- 现有积木：
  - `platform-sidebar`（平台侧边栏——**内置平台基础菜单：首页/聊天/世界列表/设置，必保留**（可折叠「平台」组，跳主应用 window.parent），+ 世界自定义菜单 `SIDEBAR_ITEMS`，组名/项名可自定义 `SIDEBAR_PLATFORM_TITLE/LABELS`，应用后自动 `WorldUI.hideFloatingIcon()`）
  - `group-chat`（**群聊对话窗，2026-08-05**：消息列表+输入发送+轮询；**样式与主界面聊天一致**——渐变头像（自己紫/别人青/系统红，有头像用图）、左右布局、相对时间（刚刚/X分钟前/HH:MM）、Markdown 轻量渲染（标题/粗体/代码/列表/引用/链接，先转义防 XSS）、附件（图片缩略图/文件芯片）；发送人区分：`window.USER_ID` 优先 + localStorage `user_info` 回退，`sender_type === 'human'`；挂载点 `<div id="group-chat">` + `GROUP_CHAT_CONFIG{mountId,groupId,height,title,apiBase,pollMs}`）
- AI 工具：查（list）/ 看（view，完整代码）/ 应用（apply → 复制进世界 `blocks/{id}/`，随世界打包导出）
- 约定：任何世界侧边栏必须保留平台基础菜单（四目的地），否则用户回不了主应用
- ⚠️ 已应用过积木的世界不会自动更新（文件是复制的），平台侧升级需重新 apply（或管理员批量更新）

---

## 八、错误处理

`_friendly_llm_error(err)`：
| 错误 | 提示 |
|------|------|
| 402 余额不足 | 💰 充值引导 +「已记录工作流，充值后说继续」 |
| 401/403 Key 无效 | 🔑 检查更新 API Key |
| 429 限流 | ⏳ 稍等再试 |
| 5xx | 🔧 服务繁忙稍后再试 |
| 其他 | 原文前 200 字 |

---

## 九、懒加载调度（world_scheduler）

- 60s 扫描：活跃超 10 分钟 → 休眠（零占用）；休眠但有近期活动（对话/预览）→ 唤醒 + **离线时间补偿**（`world_time += 真实时间差 × time_flow_rate`）
- 对话/预览都是活跃信号；阶段 2 世界代码常驻时此机制是省资源+世界线不丢的骨架

---

## 十、前端

| 界面 | 说明 |
|------|------|
| 世界列表 `WorldsPage` | 我的世界（创建者） |
| **设计页** `WorldDesignPage` | 左文件树/编辑器/预览 + 右世界 AI 对话（⚙️ 配置表单：名字/提示词/模型/温度/思考开关/工具轮上限）；可拖拽面板（复用 useResizableSidebar）；Markdown 渲染（共享 MarkdownContent）；历史滚到顶翻页 |
| **沉浸界面** `WorldViewPage` | 全屏渲染世界；网页端命名 `window.open` 独立窗口（复用+聚焦），桌面 Tauri 独立 WebviewWindow；侧边栏默认收起 + 悬浮球开关（世界代码可经 WorldUI 控制）；独立窗口里"返回"= 关窗口 |
| **群聊入口** | 绑定世界的群聊进对话页 → **全屏弹窗**「这个群聊绑定了群视界」→ 🎮 沉浸（独立窗口）/ ⚙️ 标准（关弹窗加载消息，门控先不加载） |

---

## 十一、已知边界 & 阶段 2 路线

**边界**：
- 前端 node_modules 残缺未构建验证（yarn dev 热更生效）
- 模型偶发不收敛（重复调工具）——温和去重 + 工作流记忆已缓解，观察中
- 记忆 MVP 无自动注入（AI 主动 store/recall）；后续可加：对话开始自动 recall 注入上下文

**阶段 2（沙箱先行，红线）**：
1. ✅ **2.6 世界 AI 记忆表**（2026-08-05 已完成：world_ai_memories + store_memory/recall_memory）
2. ✅ **2.7 缓存命中统计**（2026-08-05 已完成：world_llm_usage 三处落库 + GET /worlds/{id}/usage + 设计页命中率展示）
3. ✅ **2.1 py 沙箱**（2026-08-05 已完成：subprocess + resource.rlimit + 超时强杀进程组；env 白名单；有人 128MB/无人 24MB）
4. ✅ **2.2 触发文件**（2026-08-05 已完成：main.py handle(event)，harness 零框架依赖；-X utf8 强制 UTF-8）
5. ✅ **2.3 受控数据 API + 2.4 群聊写 API**（2026-08-05 已完成，见下）
6. ✅ 受控 API token 机制（每世界一个，懒生成存 worlds.config.api_token；沙箱 env 注入 WORLD_API_TOKEN/WORLD_API_BASE）
7. ✅ 动态限流（10 秒窗口 = 基础 + 每人加成 × 活跃人数；worlds.config 可配 4 个字段）
8. ✅ **群消息钩子**（2026-08-05 已完成：群消息→世界入口 handle(event) 异步感知；首条立即触发 + 2s 合并窗口可配；source=world 防死循环）
9. ✅ **唤醒改手动模式**（2026-08-05：AUTO_MANAGE=False，状态只由手动 wake/sleep 控制，唤醒后保持活跃）
10. ✅ **后台配额修复**（2026-08-05：sleep_memory_mb 默认 24→64MB，policy 硬下限 32MB——24MB 下解释器连 import 都跑不动）
11. ✅ **全局并发排队**（2026-08-05：asyncio.Semaphore，SANDBOX_MAX_CONCURRENT 默认 4 可配；返回 queued_ms）
12. ✅ **2.5 常驻世界程序 + 实时状态通道**（2026-08-05，见下）
13. 世界线一致性（阶段 3）

**2.5 常驻 + 状态通道详情**（2026-08-05）：
- world_resident.py：ResidentManager（单例）+ harness。世界 config `resident: true` + `tick_interval`（默认 30s）开启常驻
- 常驻进程 = python -I -X utf8 + harness：handle(event) 处理事件（进程内 asyncio 队列）+ on_tick() 定时推演 + on_stop() 优雅退出存状态；stdin/stdout 行协议（JSON）；stdout 转发后端日志
- 生命周期：手动唤醒→启动常驻；手动休眠→发 stop 优雅停止（超时 5s killpg）；后端启动 restore_all 恢复常驻世界；默认不限常驻个数（预留可配）
- ⚠️ harness 必须放 /tmp 且不 unlink（世界目录/删除都有 exec 竞争 → exit 2）；世界目录经 env WORLD_DIR 注入；start 前 ensure api_token
- 实时状态通道（零轮询）：世界代码 POST /world/{id}/api/state 发布状态（受控 API 鉴权+写限流+100KB 上限）→ 后端内存快照+广播+落 state.json；页面 EventSource GET /world/{id}/events（公开，连接发快照，15s 心跳，慢消费者只留最新）
- 群消息钩子路由：常驻世界→dispatch 常驻进程；否则临时触发 fallback
- 测试（临时世界 20，已删）：常驻进程启动✅、tick 2s 推演推送✅、群消息→handle→状态 SSE 实时✅、sleep 优雅停止 code=0✅

**2.3+2.4 详情**（2026-08-05）：
- 数据面：GET /world/{id}/api/{world,chat,memories,usage,groups} + POST /api/memories（复用 get_chat_history / app.tools.world.run_world_tool 同一份逻辑）
- 群聊写面：GET /api/group/{messages,members} + POST /api/group/{messages,roles,kick}（身份=世界自身，底层借世界主人权限并做群角色检查；作用域=仅绑定群 _check_bound_group；写操作独立限流）
- 鉴权：Authorization: Bearer <WORLD_API_TOKEN> 或 X-World-Token；secrets.compare_digest 常量时间比较
- 活跃埋点：对话/设计页端点 record_world_activity（worlds.py），动态限流按活跃人数加成
- 沙箱 env：WORLD_API_TOKEN / WORLD_API_BASE（默认 http://127.0.0.1:8000/world/{id}/api）；`python -I -X utf8` 修中文 print
- 文档：world_api_docs 分区 09（世界代码接口手册，含 urllib 示例与 URL 编码警告）
- 测试（沙盒手动 uvicorn + 临时世界 16，已删）：全链路 12 项通过（含 401/403/404、触发文件内调用）
- ⚠️ 已知：世界代码带中文 query 必须 urllib.parse.quote 编码（urllib 只收 ascii URL）

---

## 十二、阶段 2 架构决策与踩坑记录（2026-08-05）

> 格式：每个决策含「实现要点 / 这么做的原因 / 提示」——原因回答“为什么这样”，提示是给后来人的注意事项。

### 12.1 沙箱配额：为什么默认 64MB、硬下限 32MB

- **实现要点**：后台/无人配额 `sleep_memory_mb` 默认 64MB，`policy_for_world` 对后台内存做 `max(32, min(v, 512))` 钳制；有人在线 `runtime_memory_mb` 默认 128MB（16-2048 可配）。
- **这么做的原因**：RLIMIT_AS 是**虚拟内存**口径，`python -I` 解释器启动 + 标准库 import（importlib/asyncio 等）实测就需 **≥32MB**。初始默认 24MB 导致后台触发**从未真正跑通过**——解释器连 import 都失败（exit 非 0），而 2.2 的 6 项测试全过是因为全走前台 128MB 路径，掩盖了问题。24→48→64 是逐步实测校准的结果。
- **提示**：① 配额低于 32MB 无意义（解释器起不来）；② 虚拟内存 ≠ 常驻内存，64MB 配额下世界代码实际可用堆约 30-40MB；③ 调整配额后先跑一次最小脚本验证（`print("ok")` 都失败就是配额问题）。

### 12.2 沙箱为什么用 `python -I -X utf8`

- **实现要点**：所有沙箱执行（临时/触发/常驻）统一 `[python, "-I", "-X", "utf8", 目标文件]`。
- **这么做的原因**：`-I` 隔离模式隐含 `-E`（忽略全部 PYTHON* 环境变量），所以 `PYTHONIOENCODING=utf-8` 无效；而 stdout 是 pipe 时 Python 用 locale 编码，世界代码 `print("中文")` 直接 `UnicodeEncodeError: ascii`。`-X utf8` 强制 UTF-8 mode，一行解决。
- **提示**：沙箱内**不要**用 `-E` 之外的 PYTHON* 环境变量方案；世界代码侧中文 query 参数必须 `urllib.parse.quote`（urllib 只接受 ascii URL，见 09 分区文档）。

### 12.3 受控 API：为什么 token 存 worlds.config 而非新列

- **实现要点**：每世界一个 API token，懒生成存 `worlds.config.api_token`；`update_world` 的 config 是 merge（`{**旧, **新}`），不会被用户改配置覆盖；沙箱 env 注入 `WORLD_API_TOKEN` / `WORLD_API_BASE`。
- **这么做的原因**：零迁移（config 是 JSONB 不用改表）；token 只对本世界数据有效，不是后端密钥（env 白名单原则不破坏：不泄漏 DATABASE_URL/JWT）。
- **提示**：受控 API 端点用 `secrets.compare_digest` 常量时间比较；token 经 `POST /run`、`/trigger`、常驻 start 时 ensure 生成（**常驻 start 前必须 ensure**，否则 env 无 WORLD_API_BASE，世界代码顶层 `os.environ["WORLD_API_BASE"]` 直接 KeyError 崩溃）。

### 12.4 动态限流：为什么“基础 + 每人加成 × 活跃人数”

- **实现要点**：10 秒窗口，`实际 = 基础 + 每人加成 × 活跃人数`；4 个 config 字段可配（读 120+60/人，写 20+10/人）；活跃 = 最近 10 分钟对话/开设计页的不同用户（worlds.py 端点 `record_world_activity` 埋点）。
- **这么做的原因**（产品定）：固定配额对“群里人多”和“没人”的世界不公平——人越多世界代码被调用越频，配额应随之放大；写操作（发群消息/管理）比读更敏感，独立更严的配额。
- **提示**：世界代码死循环打爆代理的最后防线是限流 + 沙箱超时双层；429 响应体带当前配额值方便世界代码自适应退避。

### 12.5 群消息钩子：为什么 `source="world"` 不触发

- **实现要点**：`create_message` 加 `source` 参数（默认 `"user"`）；世界程序/世界 AI 发消息传 `source="world"` → 钩子跳过；**第一条消息立即触发**（保证瞬时可达），随后 `group_trigger_interval` 秒（默认 2s，0=每条）窗口内到达的消息合并成一条 event 再触发一次。
- **这么做的原因**：防**自触发死循环**（世界程序 handle 里发群消息 → 又触发自己 → 无限递归）；合并窗口是防群消息爆发把沙箱跑死，同时不丢信息（合并进 `event.messages` 数组）。等满窗口才触发会让世界对第一条消息的延迟等于窗口长度，故改成首条立即 + 后续合并。
- **提示**：event 的 messages 带 `sender_name`（批量查 User 一次）；世界入口缺失/异常时静默跳过，不影响群聊主流程；钩子触发不改世界 status（沉睡世界也可感知，唤醒保持手动）。

### 12.6 唤醒为什么改手动模式

- **实现要点**：`world_scheduler.AUTO_MANAGE = False`——调度器不再自动休眠（active 超时→sleeping）/自动唤醒，状态只由手动 `wake`/`sleep` 端点控制；保留开关可恢复。
- **这么做的原因**（产品）：手动唤醒后 10 分钟无活动又被自动转回休眠，“唤醒了不应该就继续了吗”——自动休眠机制与“手动唤醒=长期活跃”语义冲突。对话仍会唤醒（`apply_time_compensation`，人主动互动）。
- **提示**：世界时间靠唤醒时离线补偿（`world_time += 真实差 × 流速`），不依赖调度器；常驻世界（resident）的推演时间由世界代码自己管理。

### 12.7 全局并发排队：queued_ms 与 duration_ms 的区别

- **实现要点**：`asyncio.Semaphore`（`SANDBOX_MAX_CONCURRENT` 默认 4，1-32）包住全部沙箱执行；返回值含 `queued_ms`（排队等待）与 `duration_ms`（执行）两个字段。
- **这么做的原因**（产品拍板“各用各的解释器 + 排队”）：共享解释器（多群一进程）隔离是硬伤（一个世界死循环卡死全部、全局变量互踩），放弃；排队的是**短任务**不是人——在线不占位，只有执行中的几秒占槽位。
- **提示**：`duration_ms` 只统计信号量内执行耗时——首次测试三个任务都 3000ms 是误判（第三个实际排了 3 秒，只是没统计）；看总耗时用 `queued_ms + duration_ms`。

### 12.8 常驻进程：为什么 harness 放 /tmp 且不删除

- **实现要点**：常驻 harness（`/tmp/resident_{id}.py`）由 `python -I -X utf8` 运行，世界目录经 env `WORLD_DIR` 注入；harness 写完**不 unlink**。
- **这么做的原因**：踩了两个坑——① harness 写世界目录后在 finally 删除，与子进程 exec 存在**竞争**（exec 时文件已删 → exit 2）；② 世界目录内写 harness 同样有该问题，且污染世界文件。放 /tmp 不删（下次启动覆盖写），子进程启动瞬间文件必然存在。
- **提示**：进程生命周期——wake 启动、sleep 发 `{"type":"stop"}` 优雅停止（超时 5s killpg）、后端重启 `restore_all` 恢复；常驻进程 stdout 全部转发后端日志（`🌐 世界 #N 常驻: ...`），崩溃排查先看这个。

### 12.9 状态推送为什么选 SSE 不选 WS

- **实现要点**：世界代码 `POST /world/{id}/api/state` 发布状态（受控 API 鉴权+写限流+100KB）→ 后端内存快照 + 广播 + 落 state.json；页面 `EventSource GET /world/{id}/events` 订阅（连接即发快照，15s 心跳，慢消费者只留最新）。
- **这么做的原因**（产品：界面要及时，但别为延迟写一大坨）：页面只“收”状态、不“发”（发走受控 API/HTTP），SSE 单向正好；EventSource 内置断线自动重连，比 WS 少一半连接管理代码；主程序已有 SSE 先例（世界对话流 chat/stream）。
- **提示**：SSE 端点公开（与静态资源同理），只推世界自身发布的状态，不含敏感数据；慢消费者用 `Queue(maxsize=1)` 覆盖式投递保证永远最新；状态同时落 `state.json`（刷新/调试可查，世界代码也可自己读）。

### 12.10 开发期验证限制（前端）

- **实现要点**：前端验证靠宿主 vite HMR + 人工复查 + 括号配平粗检。
- **这么做的原因**：沙盒内 `node_modules` 残缺，`npx tsc` 是占位符（输出“This is not the tsc command you are looking for”）——之前多次“tsc 通过”是误报。
- **提示**：前端改动后让宿主 HMR 生效再确认；后端 Python 验证用 `PYTHONPYCACHEPREFIX=/tmp python3 -m py_compile`（沙盒 __pycache__ 无写权限）。

### 12.11 迁移是动态执行的

- **实现要点**：后端启动自动 `alembic upgrade head` + uvicorn `--reload` → 迁移文件写好几分钟内**自动应用**，不等手动跑。
- **这么做的原因**（产品拍板保留自动）：迁移本身正确就不必关，其他人改了能马上用；防护放在生成端。
- **提示**：生成后立即人工审，只留目标改动；模型改了先注册 `app/models/__init__.py`（未注册会被 autogenerate 当成“该删的表”）；验证 `docker compose exec backend alembic current`。

---

## 十三、2D 冒险游戏落地与后期修复记录（2026-08-05）

> 第一个“活的世界”示例：星野镇（世界 21）。游戏页面 + 常驻世界程序 + 群消息驱动 + SSE 实时状态，完整闭环。

### 13.1 2D 冒险积木（data/world_blocks/2d-adventure/）

- **实现要点**：完整可玩页面（Canvas 像素风：瓦片地图/移动碰撞/NPC 对话/宝箱/传送门/本地存档/触屏虚拟键），零外部依赖纯程序化绘制；世界程序 main.py 用**关键词语法提取**把群消息翻译成即时游戏指令（`旅人说 xx` / `旅人移动到 x,y` / `公告 xx` / 兜底村长回应）+ `on_tick` 推演。
- **这么做的原因**：先有页面不叫落地——NPC 要接入群消息转命令才算“活”。游戏页面 `EventSource /world/{id}/events` 实时应用世界程序发布的状态（npc_say/npc_move/banner），命令输入框（页面底部）直接发到绑定群，全链路复用（钩子→常驻→语法提取→SSE）。
- **提示**：① 说话规则正则必须“动词或冒号至少一个”（`(?:说|讲|喊|曰)[:：]?|[:：]`），否则“旅人移动到 2,3”会被误吞成说话；② 钩子节流窗口内多条消息合并进 `event.messages`，handle 要**遍历处理**（只处理 messages[0] 会吞指令）；③ 走格取格用 `Math.floor(p+0.5)`（`round` 会把整数格进位，方向错位）；④ 命令输入框聚焦时要屏蔽游戏按键（E/WASD 留给打字）。
- **移动手感**（产品定）：**按一下走一格**（经典 RPG 滑步，stepDuration 0.16s），按住不连续跑；触屏点一下走一格。

### 13.2 群消息成员校验（安全修复，产品发现）

- **实现要点**：`create_message` 加 `_is_group_member` 校验（human/ai 成员表），非成员 raise ValueError → 各调用方转 400；世界 AI 给绑定群发消息 `allow_non_member=True`（world_tools 层已有绑定校验兜底）。
- **这么做的原因**：实测任意登录用户可往任意群发消息（POST /groups/{id}/messages 无成员校验）——灌水/骚扰漏洞。
- **提示**：`create_message` 是所有群消息入口（REST/WS/AI/工具），校验放这里一处生效；校验失败不 500（各调用方都有 except）。

### 13.3 reload 死锁事故（SSE 长连接）

- **实现要点**：SSE 心跳时检测 `request.is_disconnected()` 主动断开；compose 加 `--timeout-graceful-shutdown 5`。
- **这么做的原因**：页面开着 SSE 长连接 → 改代码触发 `--reload` → uvicorn 优雅退出等旧 worker 的 SSE 请求结束 → 无限流不结束 → reload 卡在 Reloading，backend 起不来（两次事故）。
- **提示**：① 改代码前先关掉开着的世界页面；② compose command 变更要 `up -d` 重建容器（`restart` 不读新参数）；③ 世界代码在 `data/`（`--reload-exclude 'data/*'` 排除，世界代码变化不该重启 backend）。

### 13.4 世界静态文件缓存策略（ETag）

- **实现要点**：`serve_world_file` 按 `mtime+size` 生成 ETag，`If-None-Match` 命中返回 304；不设 Cache-Control。
- **这么做的原因**：世界代码频繁变化——no-cache 每次重拉（浪费），长缓存又看不到新版（产品反馈刷新没效果）；ETag 条件缓存两者兼顾：更新自动拿新版（免强刷），未更新走 304 零下载。
- **提示**：ETag 用 `st_mtime_ns`（纳秒）避免同秒修改漏判；记得给端点加 `request: Request` 参数（漏了会 NameError 500）。

### 13.5 世界文件路径：沙盒测试与生产是两套

- **实现要点**：生产容器 `data/worlds/` = 宿主 `./data/worlds`（compose 挂载 `./data:/app/data`）；沙盒手动 uvicorn（cwd=backend）写 `backend/data/worlds`。
- **这么做的原因**：沙盒测试自洽（自己读写同路径），但宿主 backend 看不到沙盒测试世界的文件——世界 21 曾因此“找不到 index.html”。
- **提示**：① 沙盒测试后要让宿主可见需复制到生产路径（或直接用宿主 backend API + 宿主签发 token 上传——沙盒 JWT 因 compose 覆盖 secret 签名失败）；② 世界转 owner 后，旧 owner token 立刻 403（权限正常工作的体现）。

---

## 附：模块清单

```
backend/app/
├── models/world.py              # worlds / world_bindings / world_agents / world_ais / world_chat_messages
├── routers/worlds.py            # 世界 CRUD/绑定/唤醒/creator 配置/对话/文件（owner 校验）
├── routers/world_proxy.py       # /world/{id}/files/* + /preview（307）+ 变量/WorldUI 注入 + 受控 API（2.3/2.4/2.5：token 鉴权/动态限流/数据与群聊代理/state 发布）+ SSE events
├── services/world/
│   ├── world_service.py         # CRUD/绑定/唤醒/懒通知/世界 AI 实体（再导出拆分模块）
│   ├── world_chat_service.py    # 世界档案/历史/凭证/流式对话/多轮工具/收尾/工作流记忆/请求日志
│   ├── world_tools.py           # 工具定义 + 执行 + 温和去重（5min 窗口）+ 摘要
│   ├── world_turn.py            # 轮次 worker（队列 + 直播广播 pub/sub）
│   ├── world_scheduler.py       # 懒加载调度（手动模式 AUTO_MANAGE=False）
│   ├── world_blocks.py          # 积木注册表（查/看/应用）
│   ├── world_file_service.py    # 世界文件隔离读写（安全）
│   ├── world_sandbox.py         # py 沙箱（rlimit+超时强杀+全局并发排队+env 白名单）
│   ├── world_event_hook.py      # 群消息钩子（节流合并→常驻/临时触发）
│   └── world_resident.py        # 常驻进程管理（handle/on_tick/on_stop + stdin 行协议）
├── utils/pure/file_edit.py      # 增量编辑核心（主站 file_edit 与世界共用）
frontend/src/
├── pages/WorldsPage.tsx / WorldDesignPage.tsx / WorldViewPage.tsx
├── components/ChatView.tsx      # 群聊世界入口全屏弹窗 + 沉浸独立窗口
├── components/Layout.tsx        # 沉浸界面侧边栏收起 + 悬浮球 + WorldUI 桥监听
├── components/shared/MarkdownContent.tsx  # 共享 Markdown 渲染
└── hooks/useResizableSidebar.ts # 可拖拽面板（left/right）
data/
├── worlds/{id}/                 # 世界文件（隔离）
├── world_blocks/                # 积木包
└── world_llm_requests/{id}.jsonl  # 实际 LLM 请求日志（最近 10 条/世界）
```

## 十三、世界商城（2026-08-07 MVP + 08-08 GitHub 同步/机器人模式）

- **数据**：`world_market_items`（kind=world，block 后置；source=local/github；github_path 同步目录）；包文件存 `data/market/{uuid}.zip`（发布 = `export_zip(include_content=False)` 代码区打包，content/ 不进包）；`system_settings.market_config` 存 GitHub 配置（repo/token/auto_sync/bot 签名密钥，token 加密存）
- **API**：`POST /market/items`（发布，owner 校验 + 同名查重 409）/ `GET /market/items`（本地板块）/ `GET /market/github/items`（GitHub 板块=快照）/ `POST /market/github/refresh`（管理员拉索引→快照）/ `POST /market/github/import`（快照条目→下载 zip→建世界）/ `POST /market/items/{id}/sync-github`（机器人同步）/ `GET|PUT /market/settings`（管理员配置，token 脱敏前4后4）/ `POST|GET|DELETE /market/github/bind`（用户绑定 GitHub，token 加密 + 签名密钥）
- **GitHub 同步（08-08，机器人模式）**：
  - **写权限模型**：仓库写权限只给机器人（系统 token）；用户 token 只验证身份（绑定门槛低，无需仓库写权限）
  - **同步流程**：校验绑定 token 有效性（GET /user，github_id 匹配）→ 目录所有权（worlds/{世界名}/ 不存在→创建；作者==自己→更新；别人→403）→ 查重 → 机器人写入
  - **双签名**：作者 Ed25519 签名（meta.signature）+ 机器人背书签名（bot_signature），信任根=系统机器人公钥（lazy 生成存 market_config）；payload 覆盖 id/title/description/author_github_id/updated_at/zip_sha256/downloads（含 zip 哈希，换包也会验签失败）
  - **身份锚**：GitHub 数字 user id（改名不变）；meta 记录 author_github_id + author_github（展示名）
  - **快照**：refresh 拉 index.json → 本地快照文件 `data/market/github_index_cache.json`（GitHub 板块数据源，不实时请求）；refresh 时验机器人签名 → signature_valid
  - **同步状态**（本地板块）：unsynced / synced（本地 updated_at ≤ 云端）/ stale（同步后又改过）；下载数双轨（本地 downloads + 云端 github_downloads）
- **前端**：`/market` 双板块（本地/GitHub tab）+ 卡片详情（桌面弹窗/手机全屏）+ 同步状态徽标 + 👑我的/✅机器人已验证/⚠️签名无效 + 绑定条（跳 /me?bind=github）；`/market/publish` 独立发布页（设计页/商城页发布按钮统一跳转，支持 ?world_id= 预选）；「我的」页 GitHub 绑定（照邮箱样式，加密存 token）
- **已知限制**：block 积木商品后置；GitHub 板块暂只同步 world 类

## 十四、沙箱加固（2026-08-07，v2）

- **skill 子进程沙箱**：`skill_runner.py`（子进程入口：预加载白名单模块 → apply_isolate → 安全加载 code.py）+ `skill_sandbox.py`（宿主：协议循环，ctx 请求回宿主校验执行）+ `sandbox_isolate.py`（Landlock + seccomp 公共库，ctypes 直调 syscall 444/445/446 + BPF）
- **世界代码沙箱**：runner/harness 注入 `apply_isolate(world_dir, deny_net=False, deny_process_creation=False)`——保留网络（受控 API）与线程，禁 execve/挂载/ptrace/内核接口
- **skill 沙箱**：`deny_net=True` + `deny_process_creation=True`（纯计算+协议 IO）+ 标准库只读授权（stdlib_readonly）
- **验证**：正常/file 协议/权限最小化/超时 30s 强杀/恶意 import 拒/内省逃逸（读 /etc → EACCES、Popen → EPERM、socket → 无模块）
- **坑**：setrlimit CPU 要 int；`-I` 模式 sys.path 无脚本目录（手动加）；asyncio self-pipe 用 socketpair（seccomp 放行 53）；Landlock 后 import 需预加载/授权 stdlib；非 x86_64 跳 seccomp

## 十五、同名 skill 冲突策略（2026-08-07，方案 1+3）

- **去重注入**（response_worker）：同名 skill 只注入一个定义，当前群绑定世界优先
- **world_id 参数**（build_world_tools 自动追加，skill 作者无感）：AI 可指定执行其他世界的同名版本
- **执行路由**（ToolRegistry.dispatch 兜底）：world_id ∈ 已绑定世界（agent+group）才放行，防越权；缺省群绑定世界优先遍历
- **清单告知**（llm.py【本群世界】）：同名技能列出所有颁布世界 id

## 十六、世界 AI 中间轮输出（2026-08-07）

工具循环中间轮（还要继续调工具时）的正文：流式 yield（前端 [TOOL] 封存机制天然拆成独立气泡）+ 落库 role=note（历史可见、不进 AI 上下文）——此前只覆盖变量被吞，用户只能看到首轮和收尾轮两句话。

## 十七、列槽位健康检查（2026-08-07，事故防御）

- **事故**：groups 表历史反复 ADD/DROP is_federated → pg_attribute 积累 1584 个 dropped 槽位 → 触顶 1600 → 任何 ALTER ADD COLUMN 失败 → 新代码首次启动崩溃（uvicorn exit 3）
- **检查**：`_check_column_slot_health` 启动时扫全表（>1100 warning / >1400 ERROR）；迁移异常 print+logger 双通道
- **清理**：VACUUM FULL 对 dropped 槽位无效（PG 怪癖）→ 用 RENAME 重建法（DROP 入站 FK → LIKE INCLUDING ALL 建新表 → INSERT → RENAME → `ALTER SEQUENCE ... OWNED BY` → DROP 旧表 → 重新 ADD FK；FK 按名字跟随 RENAME，序列 OWNED BY 会级联删序列需先改归属）
