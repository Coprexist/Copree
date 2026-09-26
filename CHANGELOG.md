# CHANGELOG

本 CHANGELOG 遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 规范，
版本号遵守 [语义化版本](https://semver.org/lang/zh-CN/)。

> **当前阶段**：v0.4.10 — 会话历史账本（缓存纪律 + 轮末结算）+ QQ 通道能力（@ id 化、撤回、引用、自测、全量模式）。
>

## [Unreleased]

## [v0.4.11] - 2026-09-26

### ✨ 新增功能
- **按人静音**：AI 可以只把某个人静音——他说话（哪怕 @ 你）都不唤醒你，消息照常进群、之后能翻到。
  可按分钟、按条数（他再说 N 条内），两个都不给就是永久；工具 `silence_member`，
  取消用 `cancel_dnd` 带上同一个 `target_user_id`。与免打扰/屏蔽的区别：那两条管"整个群"，这条管"某个人"。
- **通道规则变化会通知 AI**：QQ 群切换「获取群内全部消息」（全量模式）时，AI 会收到一条通道变更说明；
  它每轮读到的通道规则说明也跟着当前模式走。
- **记忆有权值与淡出**：每条记忆带一个 1-5 的设定权值（日常流水最低，人物与关系最高）。
  越久没被想起、或这段时间聊得越多，它就越容易淡出——最低那一档会被直接清掉腾出空间，
  其余只是不再自动进上下文，显式检索仍可取回。被想起过就重新计时，设定权值不随时间改动。
- **焦段：记忆的适用范围**。记忆可以锚在「一组同类对话」（如所有学生群）或「一段话题」（如化学教学）上，
  前者决定它在哪些会话里能被想起，后者跨会话共享；什么都不锚则只在当前会话与当前话题下可见。
  焦段由 AI 自己选、建、改名、合并，平台预置一个「所有聊天」表示全局；会话归入焦段是显式动作，
  合并前会先把两边挂着的记忆列出来。
- **记忆有字数上限**：标题 30 字、内容 200 字以内；超了仍按原样存下，只提醒一次，由 AI 自己收敛
  ——平台不替它改字。
- **记忆每日自动整理**：退场的最低档日常流水被清掉腾出空间，自动提取的重复条目合并；
  权值高的记忆只是不再自动进上下文，记录留着，显式召回仍取得到。

### 🐛 修复
- 刷新后群消息的「来自 QQ」标签消失、撤回消息显示为空内容：群消息接口的响应模型按一份手抄的字段清单
  过滤，`via` / `revoked` / `sender_state` 均被丢弃。已移除该响应模型，REST 与 WS 推送同形——
  字段清单仅保留 `message_serializer` 一份。
- 闹钟数量上限未生效：检查逻辑引用了不存在的模块且异常被静默捕获，计数条件亦与实际状态不符（应为
  `pending`）。已按 `pending` 计数并移除静默兜底。
- 按人静音被 AI 理解为「对方被禁言」：工具描述与返回值统一为单向口径（对方照常在群里发言，仅不再唤醒本人），
  账本的工具行同时附带「只对某人单向」摘要，后续轮次无须按工具名推断。
- 全量模式下消息正文里的原始提及（`<@openid>`）会漏进来：AI 看到的是一串 openid，界面也显示乱码。
- 回复里的 @ 会丢：腾讯只在回复「@ 事件」送来的消息时自带 @对方，全量事件送来的消息（哪怕正文 @ 了机器人）
  得由平台自己把 @ 发出去。
- 纯文本模式下 @ 发不出去：腾讯的纯文本消息不渲染内联 @（两种写法都显示成尖括号原文），
  改为退成 `@名字`（看得懂、不提醒对方）；要真 @ 就把通道的正文格式设成默认或 Markdown。
- 迁移编号会撞车：新迁移沿用手写的随机 id，2026-09-26 撞上既有文件，Alembic 判出多个 head、服务起不来。
  新迁移改用连续编号（文件名与编号一致），并加用例守住「id 唯一、单一 head、编号连续」。
- 数据库时区未固定：库级默认时区若与 UTC 不一致，`func.now()` 写入的无时区时间戳会被按错误时区解释。
  已固定为 UTC，并加用例守住「时间戳列一律无时区、时间只走统一入口」。
- 删掉两处从未被调用过的衰减/归档代码（写回式价值分衰减、按标题相似度提升归档）：
  前者与「设定权值不随时间改动」的口径相悖，留着只会误导后来人。

## [v0.4.10] - 2026-09-26

### ✨ 新增功能
- **消息撤回**：群聊与私信都支持。本人 2 分钟内可撤，群主/管理员可撤他人的消息；外部通道同步撤回，
  撤不掉时如实回报（站内已撤、通道侧未撤）。撤回后给已经看过这条消息的 AI 补一条作废通知，
  界面显示「XXX 撤回了一条消息」。
- **AI 撤回工具** `recall_message`：AI 说错话后可自己撤回（仅限自己发的、2 分钟内）。
- **@ 提及统一为 id**：平台与外部通道统一用 `<@!id>`（QQ 是 `<@!openid>`、NapCat 是 CQ 码），
  改名、重名、昵称带空格都不会 @ 错人；旧的 @名字 写法继续兼容。
- **通道自测**：在真实出口上发一条测试消息并回显通道侧原始响应，通道卡片一键触发。
- **全量群消息模式**：群里开启「接收所有消息」后，群里的每条消息都进入平台（镜像群里完整的对话），
  只有点名到机器人的那条才带唤醒令牌；@到其他成员的内容自动补成平台的提及。
  与 @机器人模式两条事件按消息 id 去重，内容更完整的一方自动补全。
- **出站精准引用**：AI 回复时可引用 QQ 群里的具体某条消息（官方 `message_reference`）。
- **长消息折叠**：单条上限 2048 字，超出保留前 75% + 后 25%，中间标注省略字数并给出展开入口。
- **通道可配**：正文格式（默认 / 纯文本 / Markdown）与「引用回复」开关；插件配置支持下拉与勾选框。
- **消息操作菜单**：悬停消息出现操作入口，菜单含 回复 / 复制 / 撤回。
- 会话列表预览与聊天记录导出显示人名而非内部标记；配对提示文案两条通道统一。

### 🐛 修复
- 撤回按钮在 UTC+8 时区永远不出现：窗口判定把服务端时间当成本地时间解析。
- 非安全上下文下复制静默失败，改为带回退的实现。

## [v0.4.9] - 2026-09-25

### ✨ 新增功能
- **会话历史账本**：请求体固定为「锁定系统段 + 历史 + 尾部读数」，历史段只追加、仅在压缩/解锁点重写；
  条目按渲染后的最终内容落库，保证每轮请求前缀稳定、缓存命中。
- **跨状态交接与便签**：私信里的约定能带到群聊；便签以账本条目投递、每个会话各一份，
  解锁后仍在有效期内的重新投递。
- **前缀缓存纪律**：动态内容一律沉到上下文尾部；记忆索引走版本链冻结，新消息不再打断前缀。
- **轮末结算**：`end_turn` 支持 `keep_thinking` 与 `key_note`，工具总账与关键交接写入账本。
- **压缩阈值三档**：压后上限 / 久未活跃 / 热触发，系数与压缩目标可配置；上下文窗口按模型取值。
- **解锁清单即契约**：解锁步骤集中一处执行并记录，压缩后可立即重写账本。
- 群聊与私信历史统一读账本；一次性事件（能力变更、便签撤下、建议回复）落成条目。

### 🐛 修复
- 压缩取样口径错误（按最后 200 行统计，真实对话被工具卡片淹没，永远判定「无需压缩」）。
- 「答上一条」漂移：上下文顺序与空闲判定时区。
- token 估算低估中文（压缩阈值形同虚设）。

## [v0.4.8] - 2026-09-25

### ✨ 新增功能
- 控制台重构：管理页统一到通用组件与设计令牌。
- 服务插件协议 v3：插件类别、插件级配置、运行态不漂移。
- 插件商城与社区索引仓；DSH 桥接（管理端给 DSH 本体会话发消息）。
- 群发现、入群审批与待审列表；私信出口、未读与常驻通知；通俗模式（用户级表达偏好）。
- 启动流程后台化，整套测试从 108 秒降到 18~20 秒。

## [v0.4.7] - 2026-09-25

### ✨ 新增功能
- **外部聊天通道**：QQ 官方机器人通道与 NapCat/OneBot 通道。
- **外部身份与配对**：陌生人私聊先领配对码，批准后才能与 AI 对话；支持拉黑与解除。
- 通道配置通用化：卡片与弹窗按插件声明渲染；平台可托管协议端（扫码登录）。
- 同一个 AI 可以同时接多条通道；通道能力与限制写进提示词与界面。

### 🐛 修复
- 两个 QQ 通道实例互相顶掉出口，导致部分群的回复被静默丢弃（改句柄注册 + 并发分发）。

## [v0.4.6] - 2026-09-25

### ♻️ 重构
- 世界对话主流程分阶段拆分（首轮流式、工具循环、命令分发各自独立）。
- 世界工具全面插件化：一个工具一个文件，展示文案随工具走，支持外部插件目录。

### 🐛 修复
- 世界 AI 模型解析统一入口，修复默认模型解析崩溃。
- 世界会话时间比较类型错误、工具调用链悬空导致的请求失败。
- 排队弹窗清不掉、中途插话阻塞、思考展示重复、滚动跟随中断等对话体验问题。
- 群消息接口统一为 `/gm`（与 `/dm` 对称），顺带修掉两个必然 500 的入口。

## [v0.4.5] - 2026-09-25

### 🔒 安全
- 权限判定收敛为统一入口；匿名下载改为白名单；公网自测报告逐条整改。

### 📚 文档
- 仓库与示例文案去地址化；不能入库的资料统一目录；新增《安全与权限模型》。
- 群视界 API 文档索引补齐；文档面向国内/国际读者各给一套命令。

### 🔧 开发工具与测试
- 覆盖率增量门禁、CI 前端类型检查门禁、授权边界静态守卫、测试不写生产数据目录。
- 演示截图流水线一键产出脱敏图。

## [v0.4.4] - 2026-09-24

### 📝 文档与插件
- DSH 插件 README 按通行规格重写（英文主版 + 中文版），补主仓库介绍与返回链接。
- 项目定位句统一；旧目录名保留跳转占位页。

## [v0.4.3] - 2026-09-19

### ✨ 新增功能
- **品牌更名**：AIsChat → Copree（显示层、仓库地址、协议路径、存储键、备份前缀统一，兼容旧名读取）。
- 世界会话「起点解锁」前缀快照与轮次预算；计划模式可只读运行并申请提额。
- 会话改名与聊天记录下载（Markdown / JSON）；世界工具支持目录检索与省 token 的列表。

### 🐛 修复
- 世界工具落盘前语法自检与改动摘要；收尾轮保留 tools（否则整段前缀掉出缓存）。
- i18n 三语补齐并加静态检查脚本。

## [v0.4.2] - 2026-09-14

### 🐛 修复
- 世界对话的刷新竞态、中途消息插入、中间轮文字重复；排队弹窗与 `active_turn` 状态错误。
- 工具执行异常时仍落库工具响应，避免后续请求报错。
- MiMo 接入地址修正与迁移。

### 🧪 测试
- 无 pytest 运行器与用例选择器；纯函数用例与覆盖率门禁；群视界发图链路端到端冒烟。

## [v0.4.1] - 2026-09-06

### ✨ 新增功能
- **备份服务策略模式**：支持 PostgreSQL 与 SQLite 两套策略；完整备份跳过数据库数据目录。
- **群视界实时 WebSocket 通道**；强注入提示词补「常驻模式与实时通道」规范段。
- 启动迁移彻底切到 Alembic（移除旧迁移模块）。

### ♻️ 重构
- 业务层 Repository 化收尾；API Key 解密失败统一异常与降级通知。

### 🐛 修复
- 补齐缺失的 await（删除与 flush/refresh），修复 Windows 启动器登录 405。

## [v0.4.0] - 2026-08-24

### 🚀 新功能

#### 桌面 EXE 打包支持（Windows）
- **安装程序**：基于 NSIS 3.09 编译，支持语言选择（简体中文/English）
- **一键安装**：自动创建桌面和开始菜单快捷方式
- **最小化到托盘**：关闭窗口时最小化到系统托盘
- **开机自启动**：通过 Windows 注册表实现
- **端口配置**：设置页支持修改服务端口
- **日志面板**：支持简要/详细模式切换，批量渲染优化
- **GUI 启动器**：CustomTkinter 实现，支持启动/停止服务、打开浏览器、日志切换
- **EXE 打包配置**：PyInstaller 配置（隐藏导入、排除项、图标、数据文件）

#### 备份服务策略模式重构
- **策略模式**：`DatabaseBackupBackend` 抽象接口，支持 PostgreSQL / SQLite 双后端
- **PostgreSQL 后端**：`PostgresBackupBackend`（pg_dump / psql），`.sql` 扩展名
- **SQLite 后端**：`SQLiteBackupBackend`（sqlite3.backup API + WAL checkpoint），`.db` 扩展名
- **原子恢复**：SQLite 恢复使用临时文件 + `shutil.move` 原子替换 + `PRAGMA integrity_check` 校验
- **安全加固**：恢复后返回 `restart_required` 标志，前端明确提示"请重启应用生效"
- **路径解析**：`_db_path()` 相对路径基于 `settings.data_dir` 解析
- **异步改进**：`asyncio.to_thread()` 替代已弃用的 `get_event_loop().run_in_executor()`
- **错误处理**：未知数据库后端直接抛出异常而非静默回退

#### 前端备份页面
- 新增 `/admin/backup/info` API 端点，返回当前数据库类型和文件扩展名
- 备份页面顶部显示当前数据库类型（PostgreSQL/SQLite）和兼容性警告
- 下载/上传文件扩展名由后端动态决定
- 恢复操作支持 `.sql` 和 `.db` 两种文件格式
- SQLite 恢复后提示"请重启应用生效"

#### 后端脱离 DB 模式
- 支持 SQLite 直连模式（无需外部数据库）
- 数据目录自动创建（exe 同级 `data/`）
- 强制 SQLite 模式，简化部署

### 🐛 修复的 Bug

#### GUI 启动优化
- **健康检查**：从主线程同步阻塞改为后台线程异步探测，消除 GUI 卡顿
- **日志面板**：批量渲染限制（60行/帧），防止单帧大量 Tk 重绘导致卡顿
- **日志模式切换**：简要→详细增量追加，详细→简要全量重绘

#### 后端优化
- **数据库连接**：支持 SQLite 直连模式
- **健康检查**：异步化，避免启动阻塞
- **日志系统**：队列处理器支持，避免日志丢失

### 🖥️ 桌面启动器 UI 改进

#### 界面优化
- **窗口图标**：使用真正的 `.ico` 文件（多尺寸 16/32/48/64/128/256），修复 Windows 蓝色方框问题
- **标题**：改为「Copree 启动器」
- **字号**：全局加大（标题 22px、状态 15px、设置 14px、页脚 11px）
- **设置按钮**：emoji 改为纯文字（「设置」/「返回」）

#### 功能增强
- **设置页滚动**：支持小窗口下滚动查看
- **单窗口视图切换**：主页 ↔ 设置页，顶栏常驻
- **状态卡片**：有背景、圆角、微妙边框，层次分明
- **日志面板**：浅灰背景，阅读舒适
- **页脚**：显示版本号和端口

### 📚 文档更新

- **README**：添加安装程序下载链接
- **CHANGELOG**：完整记录 v0.3.14 → v0.4.0 所有改动
- **安装指南**：详细的安装和卸载步骤

### 🔧 开发工具

- **.gitignore**：更新（`.venv*`、`data/`、`*.log`、`__pycache__/`、`frontend/dist/`）

### 📦 依赖更新

- **bcrypt**：降级到 `4.0.1`（修复密码验证问题）
- **PyInstaller**：添加 `pystray` 依赖（托盘功能）
- **NSIS**：使用 3.09 编译安装程序

---

## [v0.3.14] - 2026-08-23

### 🚀 新功能

#### DSH 插件系统
- **DSH 插件 v1**：同源代理 + 侧边栏 board + 完整聊天渲染
- **沉浸式界面**：设置页 + 功能导航 + 消息渲染完善
- **GitHub 式双向同步**：世界文件本地镜像 + 快照三路对比 + 冲突裁决 + 版本提示注入
- **世界文件夹同步**：世界文件夹 + 会话自动同步、world_* 工具集
- **DSH 接入指南**：完整的接入文档和实现要点

#### 自习室功能
- **白噪音系统**：森林风声、海面/深海音色、细雨/暴雨等多层声源
- **立体声空间化**：HRTF 双耳渲染、声像移动、单声道/立体声切换
- **圆形进度环**：SVG 进度条、双模式、双端口
- **待办清单**：添加/删除/完成待办事项
- **云端统计**：在线时间、累计时间、近 15 天统计
- **响应式布局**：手机端适配、小屏竖屏优化

#### 主题系统
- **主题设计工具**：实时预览、取色器、hex 输入、多套预设
- **皮肤插件**：管理员全局 + 用户个人两级开关
- **主题投票**：A/B 快投页面与接口
- **语义色系统**：CSS 变量驱动深浅双主题

#### 插件系统
- **统一插件系统**：目录即插件，两级开关
- **行为插件协议 v2**：@skill 装饰器 + owner 注册表
- **插件关闭回退**：WS 广播 + 轮询 + 焦点恢复

### 🏗️ 架构重构：Repository 化

**新增 Repository 层**（`backend/app/repositories/`）：
- `user_repo.py`、`friend_repo.py`、`world_repo.py`、`group_type_repo.py`、`agent_repo.py`
- `system_settings_repo.py`、`verification_repo.py`、`api_key_pool_repo.py`
- `audit_repo.py`、`capability_repo.py`、`content_repo.py`、`export_repo.py`
- `federation_repo.py`、`infra_repo.py`、`invitation_repo.py`、`memory_repo.py`
- `plugin_repo.py`、`search_repo.py`、`skill_repo.py`

**彻底解耦模块**（业务函数签名改为 `repo: XxxRepository`）：
- 用户、好友、世界核心、审计、能力版本、大脑控制器
- 邀请、搜索、导出、内容（对话日志/OpenCLI）

**兼容过渡模块**（`_ensure_repo` 幂等包装）：
- 记忆、技能、AI 代理配套、基础设施、联邦、文件服务、插件、群类型

**路由层依赖注入**（`app/routers/deps.py`）：
- 新增 `get_user_repo`、`get_friend_repo`、`get_world_repo`、`get_group_type_repo`、`get_agent_repo`、`get_invitation_repo`、`get_search_repo`、`get_export_repo`、`get_content_repo` 等

### 🐛 修复的 Bug

#### 安全性修复
- **全局异常处理**：泄露 `str(exc)` → 改为安全模糊响应
- **验证错误**：过滤 `input` 字段，避免泄露请求体
- **健康检查**：添加超时保护（`asyncio.wait_for` + 5s 超时）

#### 数据库与 Repository
- **异步误用**：Repository `add`/`delete`/`flush`/`refresh` 被误定义为异步 → 改为同步
- **业务代码**：大量错误的 `await repo.add(...)` → 全面清理
- **常驻世界恢复**：查询使用 `astext` 导致启动警告 → 改用 `cast`
- **bcrypt 版本**：过高导致密码验证失败 → 降级到 `4.0.1`

#### 服务稳定性
- **后台任务**：重启计数永不重置 → 稳定运行 10 分钟后自动重置
- **延迟重启**：任务未被管理 → 纳入 `_DELAYED_RESTART_TASKS`
- **健康检查**：添加超时保护，避免启动阻塞
- **日志目录**：创建失败降级 → 仅控制台输出
- **metrics.flush()**：缺少 `await` → 修复
- **federation_service**：`db.refresh()` 缺少 `await` → 修复
- **皮肤插件**：默认启用 → 改为默认禁用

#### DSH 插件修复
- **workspaceId 字段修复**：世界同步字段错误
- **backendRequest chunk 拼接**：UTF-8 截断问题
- **world_pull HTML**：走原始接口
- **快照文件误判排除**：同步正确性修复
- **force 覆盖补齐冲突**：本地修改保护
- **快照只更新实际同步文件**：防洗白

#### 自习室修复
- **白噪音切换**：无缝切换、森林无周期、声像连续渐变
- **深海气泡**：活跃池可重叠发声、大小分布优化、音量调整
- **细雨滴答**：专业雨滴声学重做、稀疏滴答感
- **立体声摆位**：所有层开播立即摆到初始方位
- **进度环**：SVG 方案重做、rAF 平滑
- **响应式布局**：小屏手风琴、竖屏断点、字体缩放

#### 前端修复
- **按钮高度**：图标/无图标按钮严格等高
- **文档弹窗**：手机全屏、滚动陷阱、背景漏色
- **主题色**：CSS 变量驱动、深浅双主题
- **世界列表**：按钮固定高度、移动端适配

### 🎨 应用入口优化（`main.py`）

#### 路由重构
- **关闭 Redoc**：保留 `/openapi.json`
- **Swagger 文档**：拆出到 `routers/swagger_docs.py`
- **基础路由**：拆出到 `routers/system.py`（`/`、`/health`、`/maintenance-msg`）

#### 错误处理
- **全局异常日志**：使用 `exc_info=exc` 并添加请求 ID
- **验证错误响应**：只保留 `loc`/`msg`/`type` 白名单
- **状态码**：使用 `fastapi.status` 常量
- **移除冗余**：`HTTPException` 处理器

#### 配置管理
- **APP_VERSION**：纳入 `settings.app_version`
- **版本管理**：统一版本号管理

### 🖥️ 桌面启动器 UI 改进

#### 界面优化
- **窗口图标**：使用真正的 `.ico` 文件（多尺寸 16/32/48/64/128/256），修复 Windows 蓝色方框问题
- **标题**：改为「Copree 启动器」
- **字号**：全局加大（标题 22px、状态 15px、设置 14px、页脚 11px）
- **设置按钮**：emoji 改为纯文字（「设置」/「返回」）

#### 功能增强
- **设置页滚动**：支持小窗口下滚动查看
- **单窗口视图切换**：主页 ↔ 设置页，顶栏常驻
- **状态卡片**：有背景、圆角、微妙边框，层次分明
- **日志面板**：浅灰背景，阅读舒适
- **页脚**：显示版本号和端口

### ⚡ 性能优化

#### GUI 启动优化
- **健康检查**：从主线程同步阻塞改为后台线程异步探测，消除 GUI 卡顿
- **日志面板**：批量渲染限制（60行/帧），防止单帧大量 Tk 重绘导致卡顿
- **日志模式切换**：简要→详细增量追加，详细→简要全量重绘

#### 后端优化
- **数据库连接**：支持 SQLite 直连模式
- **健康检查**：异步化，避免启动阻塞
- **日志系统**：队列处理器支持，避免日志丢失

#### 前端优化
- **按钮规范**：统一 .btn 语义类 + 组件共享单一规范源
- **移动端适配**：世界列表、文档弹窗、主题色选择器
- **响应式布局**：手机端全屏、横向分区栏、底部抽屉

### 📚 文档更新

- **README**：添加安装程序下载链接
- **CHANGELOG**：完整记录 v0.3.14 → v0.4.0 所有改动
- **安装指南**：详细的安装和卸载步骤
- **技术文档**：架构重构说明
- **DSH 接入指南**：完整的接入文档和实现要点
- **自习室文档**：白噪音/云端/布局/部署/未解决问题
- **插件系统文档**：统一插件系统文档更新
- **前端规范**：统一按钮规范、语义色系统、移动端适配

### 🔧 开发工具

- **异步检查**：`scripts/scan_async_full.py` 检查 `async/await` 遗漏
- **Repository 检查**：`scripts/scan_db_issues.py` 检查转换遗漏
- **.gitignore**：更新（`.venv*`、`data/`、`*.log`、`__pycache__/`、`frontend/dist/`）

### 📦 依赖更新

- **bcrypt**：降级到 `4.0.1`（修复密码验证问题）
- **PyInstaller**：添加 `pystray` 依赖（托盘功能）
- **NSIS**：使用 3.09 编译安装程序

---

## [v0.3.14] - 2026-08-23

### 🚀 新功能

#### DSH 插件系统
- **DSH 插件 v1**：同源代理 + 侧边栏 board + 完整聊天渲染
- **沉浸式界面**：设置页 + 功能导航 + 消息渲染完善
- **GitHub 式双向同步**：世界文件本地镜像 + 快照三路对比 + 冲突裁决 + 版本提示注入
- **世界文件夹同步**：世界文件夹 + 会话自动同步、world_* 工具集
- **DSH 接入指南**：完整的接入文档和实现要点

#### 自习室功能
- **白噪音系统**：森林风声、海面/深海音色、细雨/暴雨等多层声源
- **立体声空间化**：HRTF 双耳渲染、声像移动、单声道/立体声切换
- **圆形进度环**：SVG 进度条、双模式、双端口
- **待办清单**：添加/删除/完成待办事项
- **云端统计**：在线时间、累计时间、近 15 天统计
- **响应式布局**：手机端适配、小屏竖屏优化

#### 主题系统
- **主题设计工具**：实时预览、取色器、hex 输入、多套预设
- **皮肤插件**：管理员全局 + 用户个人两级开关
- **主题投票**：A/B 快投页面与接口
- **语义色系统**：CSS 变量驱动深浅双主题

#### 插件系统
- **统一插件系统**：目录即插件，两级开关
- **行为插件协议 v2**：@skill 装饰器 + owner 注册表
- **插件关闭回退**：WS 广播 + 轮询 + 焦点恢复

### 🏗️ 架构重构：Repository 化

**新增 Repository 层**（`backend/app/repositories/`）：
- `user_repo.py`、`friend_repo.py`、`world_repo.py`、`group_type_repo.py`、`agent_repo.py`
- `system_settings_repo.py`、`verification_repo.py`、`api_key_pool_repo.py`
- `audit_repo.py`、`capability_repo.py`、`content_repo.py`、`export_repo.py`
- `federation_repo.py`、`infra_repo.py`、`invitation_repo.py`、`memory_repo.py`
- `plugin_repo.py`、`search_repo.py`、`skill_repo.py`

**彻底解耦模块**（业务函数签名改为 `repo: XxxRepository`）：
- 用户、好友、世界核心、审计、能力版本、大脑控制器
- 邀请、搜索、导出、内容（对话日志/OpenCLI）

**兼容过渡模块**（`_ensure_repo` 幂等包装）：
- 记忆、技能、AI 代理配套、基础设施、联邦、文件服务、插件、群类型

**路由层依赖注入**（`app/routers/deps.py`）：
- 新增 `get_user_repo`、`get_friend_repo`、`get_world_repo`、`get_group_type_repo`、`get_agent_repo`、`get_invitation_repo`、`get_search_repo`、`get_export_repo`、`get_content_repo` 等

### 🐛 修复的 Bug

#### 安全性修复
- **全局异常处理**：泄露 `str(exc)` → 改为安全模糊响应
- **验证错误**：过滤 `input` 字段，避免泄露请求体
- **健康检查**：添加超时保护（`asyncio.wait_for` + 5s 超时）

#### 数据库与 Repository
- **异步误用**：Repository `add`/`delete`/`flush`/`refresh` 被误定义为异步 → 改为同步
- **业务代码**：大量错误的 `await repo.add(...)` → 全面清理
- **常驻世界恢复**：查询使用 `astext` 导致启动警告 → 改用 `cast`
- **bcrypt 版本**：过高导致密码验证失败 → 降级到 `4.0.1`

#### 服务稳定性
- **后台任务**：重启计数永不重置 → 稳定运行 10 分钟后自动重置
- **延迟重启**：任务未被管理 → 纳入 `_DELAYED_RESTART_TASKS`
- **健康检查**：添加超时保护，避免启动阻塞
- **日志目录**：创建失败降级 → 仅控制台输出
- **metrics.flush()**：缺少 `await` → 修复
- **federation_service**：`db.refresh()` 缺少 `await` → 修复
- **皮肤插件**：默认启用 → 改为默认禁用

#### DSH 插件修复
- **workspaceId 字段修复**：世界同步字段错误
- **backendRequest chunk 拼接**：UTF-8 截断问题
- **world_pull HTML**：走原始接口
- **快照文件误判排除**：同步正确性修复
- **force 覆盖补齐冲突**：本地修改保护
- **快照只更新实际同步文件**：防洗白

#### 自习室修复
- **白噪音切换**：无缝切换、森林无周期、声像连续渐变
- **深海气泡**：活跃池可重叠发声、大小分布优化、音量调整
- **细雨滴答**：专业雨滴声学重做、稀疏滴答感
- **立体声摆位**：所有层开播立即摆到初始方位
- **进度环**：SVG 方案重做、rAF 平滑
- **响应式布局**：小屏手风琴、竖屏断点、字体缩放

#### 前端修复
- **按钮高度**：图标/无图标按钮严格等高
- **文档弹窗**：手机全屏、滚动陷阱、背景漏色
- **主题色**：CSS 变量驱动、深浅双主题
- **世界列表**：按钮固定高度、移动端适配

### 🎨 应用入口优化（`main.py`）

#### 路由重构
- **关闭 Redoc**：保留 `/openapi.json`
- **Swagger 文档**：拆出到 `routers/swagger_docs.py`
- **基础路由**：拆出到 `routers/system.py`（`/`、`/health`、`/maintenance-msg`）

#### 错误处理
- **全局异常日志**：使用 `exc_info=exc` 并添加请求 ID
- **验证错误响应**：只保留 `loc`/`msg`/`type` 白名单
- **状态码**：使用 `fastapi.status` 常量
- **移除冗余**：`HTTPException` 处理器

#### 配置管理
- **APP_VERSION**：纳入 `settings.app_version`
- **版本管理**：统一版本号管理

### 🖥️ 桌面启动器 UI 改进

#### 界面优化
- **窗口图标**：使用真正的 `.ico` 文件（多尺寸 16/32/48/64/128/256），修复 Windows 蓝色方框问题
- **标题**：改为「Copree 启动器」
- **字号**：全局加大（标题 22px、状态 15px、设置 14px、页脚 11px）
- **设置按钮**：emoji 改为纯文字（「设置」/「返回」）

#### 功能增强
- **设置页滚动**：支持小窗口下滚动查看
- **单窗口视图切换**：主页 ↔ 设置页，顶栏常驻
- **状态卡片**：有背景、圆角、微妙边框，层次分明
- **日志面板**：浅灰背景，阅读舒适
- **页脚**：显示版本号和端口

### ⚡ 性能优化

#### GUI 启动优化
- **健康检查**：从主线程同步阻塞改为后台线程异步探测，消除 GUI 卡顿
- **日志面板**：批量渲染限制（60行/帧），防止单帧大量 Tk 重绘导致卡顿
- **日志模式切换**：简要→详细增量追加，详细→简要全量重绘

#### 后端优化
- **数据库连接**：支持 SQLite 直连模式
- **健康检查**：异步化，避免启动阻塞
- **日志系统**：队列处理器支持，避免日志丢失

#### 前端优化
- **按钮规范**：统一 .btn 语义类 + 组件共享单一规范源
- **移动端适配**：世界列表、文档弹窗、主题色选择器
- **响应式布局**：手机端全屏、横向分区栏、底部抽屉

### 📚 文档更新

- **README**：添加安装程序下载链接
- **CHANGELOG**：完整记录 v0.3.14 → v0.4.0 所有改动
- **安装指南**：详细的安装和卸载步骤
- **技术文档**：架构重构说明
- **DSH 接入指南**：完整的接入文档和实现要点
- **自习室文档**：白噪音/云端/布局/部署/未解决问题
- **插件系统文档**：统一插件系统文档更新
- **前端规范**：统一按钮规范、语义色系统、移动端适配

### 🔧 开发工具

- **异步检查**：`scripts/scan_async_full.py` 检查 `async/await` 遗漏
- **Repository 检查**：`scripts/scan_db_issues.py` 检查转换遗漏
- **.gitignore**：更新（`.venv*`、`data/`、`*.log`、`__pycache__/`、`frontend/dist/`）

### 📦 依赖更新

- **bcrypt**：降级到 `4.0.1`（修复密码验证问题）
- **PyInstaller**：添加 `pystray` 依赖（托盘功能）
- **NSIS**：使用 3.09 编译安装程序

---

## [v0.3.13] - 2026-08-22

### Changed — 🏗️ 代码审查第二轮修复

- **config.py `MODEL_OPTIONS` 清理**：移除 `get_model_options()` 中残留的 `os.getenv("MODEL_OPTIONS")`，
  新增 `model_options: str = ""` 字段声明，Pydantic 自动从环境变量读取 JSON 字符串。
- **加密密钥文件路径不再硬编码**：`_ENCRYPTION_KEY_FILE` 改为 `Path(self.data_dir) / "encryption_key"`，
  开发环境（如 Windows/macOS）下不再依赖 `/app/data` 固定路径。
- **请求日志中间件迁出 main.py**：`request_logging_middleware` 移入 `app/middleware.py`，
  在 `register_middlewares()` 中统一注册，主文件只负责组装。
- **Swagger UI 文档路由迁出 main.py**：`/docs`、`/docs/zh` 移入 `app/routers/swagger_docs.py`，
  通过 `get_all_routers()` 自动发现注册，main.py 不再包含路由定义。
- **Request ID 增强**：优先使用客户端透传的 `X-Request-ID` 请求头，否则生成完整 UUID（不再截断 8 位）。
- **未识别配置字段警告**：启动时检测 `pydantic_extra` 中的未识别字段并记录 WARNING（帮助排查环境变量拼写错误），
  保留 `extra = "allow"` 兼容 DB 覆盖层注入。
- **清理误导注释**：`logging_config.py` 移除未实现的 `LOG_EMOJI=false` 开关描述。

## [v0.3.12] - 2026-08-22

### Changed — 🏗️ 后端代码质量全面优化

- **全局异常处理器安全增强**：区分 `HTTPException`（原样返回 detail）和未预期 `Exception`（只返回模糊"服务器内部错误"），
  防止数据库表名、SQL 语句等敏感信息泄露给客户端。
- **后台任务管理修复**：`spawn_task` 重启计数在任务稳定运行 10 分钟后自动重置（偶发故障恢复后可重新重启）；
  延迟重启任务纳入 `_DELAYED_RESTART_TASKS` 集合，`cancel_all_tasks()` 统一取消，防止应用关闭后任务复活。
- **大脑初始化改为同步等待**：`brain_controller.initialize` 从 `spawn_task` 包装改为直接 `await`，
  失败则记录 CRITICAL 并中止启动（核心依赖快速失败原则）。
- **删除重复数据库探测**：`_start_browser_service` 移除冗余的数据库就绪等待（lifespan 已确保）。
- **数据库连接池释放**：lifespan 关闭流程末尾调用 `engine.dispose()` 释放连接池。
- **异步 API 修正**：`wait_for_db` 中 `asyncio.get_event_loop()` → `asyncio.get_running_loop()`。
- **配置管理 Pydantic 化**：`config.py` 移除所有 `os.getenv` 手动调用，改为 Pydantic 原生字段声明 +
  `field_validator`（`db_backend`/`embedding_backend` 自动小写，整数字段自动类型转换），
  支持 `.env` 文件和环境变量自动解析。
- **加密密钥安全策略**：引入 `ENVIRONMENT` 环境变量（默认 development）；
  生产环境未设置或复用 JWT 密钥时中止启动（RuntimeError）；
  开发环境自动生成随机密钥并持久化到 `data/encryption_key`（文件权限 0600），
  避免每次重启生成新密钥导致已加密数据无法解密。
- **数据库模块清理**：`database.py` 移除 `__import__("sqlalchemy").text()` 动态导入，
  改为顶层 `from sqlalchemy import text`；新增 `dispose_db()` 函数供外部调用。
- **日志配置完善**：
  - 统一 uvicorn 日志格式（`uvicorn`/`uvicorn.error`/`uvicorn.access` 纳入 dictConfig），
    避免默认格式与应用日志不一致。
  - 日志格式增加文件名和行号（`%(filename)s:%(lineno)d`），便于问题排查。
  - 启动时自动创建日志目录，创建失败降级为仅控制台输出（不崩溃）。
  - 日志 emoji 保留（信息设计），后续可通过 `LOG_EMOJI=false` 关闭。

### Changed — 🏗️ main.py 优雅化重构

- **日志配置独立模块**：`logging.basicConfig` → `logging.config.dictConfig`（`app/logging_config.py`），
  显式覆盖 root logger 配置（不受第三方库 import 时已配置的影响）；支持 `LOG_LEVEL` 环境变量动态调整日志级别。
- **APP_VERSION 支持环境变量注入**：CI/CD 可通过 `APP_VERSION` 环境变量覆盖版本号，回退硬编码默认值。
- **CORS 统一收入 middleware.py**：CORS 中间件注册从 main.py 移入 `register_middlewares()`，
  所有中间件（CORS + IP 追踪 + 维护拦截）单一入口管理。
- **全局异常处理器**：注册 `exception_handler(Exception)` 统一返回 JSON 格式错误响应 + 完整堆栈日志，
  替代 FastAPI 默认的 HTML 500 页面。
- **请求日志 + Request ID**：新增 `request_logging_middleware`，每个请求生成 8 位 Request ID（`X-Request-ID` 头），
  记录 `METHOD PATH → STATUS (Xms)` 格式的访问日志。
- **健康检查增强**：`/health` 增加 `asyncio.wait_for` 5s 超时保护，返回标准 HTTP 状态码（200/503），
  不再暴露数据库连接细节。
- **维护状态接口精简**：`/maintenance-msg` 仅返回 `msg` + `hard`，不再暴露 `soft` 状态和完整配置。

## [v0.3.10] - 2026-08-19

### Fixed — 🔧 DSH 世界同步两个编码/注入 bug（dsh-copree）

- **`backendRequest` 响应拼接 utf8 截断**：`res.on('data', c => text += c)` 对每个 TCP chunk 单独 utf8 解码，
  多字节字符（如 `──`）跨 chunk 边界时损坏为 U+FFFD 乱码——`world_pull` 拉取含中文注释的 JS 文件会静默写坏本地。
  修复：Buffer 数组收集 + 最后统一 `Buffer.concat().toString('utf8')`。
- **`world_pull` 拉 HTML 拉到注入版**：走 `/world/{id}/files/{path}` 静态路由会对 HTML 注入世界变量（WORLD_ID 等），
  拉回本地的是注入后文件而非原始代码。修复：`.html/.htm` 改走带 token 的 `/worlds/{id}/files/content` 原始读取接口。
- **同步快照单位**：`.copree-sync.json` 的 `lm` 必须用毫秒（对齐 DSH `statMtime` 的 mtimeMs）；
  单位不一致会导致全文件误判 changedLocal，force 拉取覆盖本地。
- **`world_pull` force 模式误报「无变化」**：`pulled: N` 但 message 报「无变化」——force 拉取的
  conflict/changedLocal 覆盖未计入统计。修复：pulled > 0 时如实上报 `↓N 覆盖`。

### Added — 📖 平台机制文档与 AI 提示词增强（同步/限流）

- **接口文档新增 10 分区「同步与限流机制」**（`api_docs/sections/10-sync-rate-limit.md`）：DSH 世界镜像
  双向同步（快照三路对比 / `world_pull` pulled 语义 / `world_push`「无变化」含义）、写操作限流（429 =
  太快不是故障、前端降级规则）、事件通道说明、常见问题速查表——群视界 AI（`view_api_doc`）与
  DSH 世界会话（`world_view_doc`）共用同一份。
- **世界 AI 强注入提示词**（`FORCED_PROMPT_SEGMENTS`）：新增【文件同步机制】与【限流机制】两段常驻，
  防止把 push「无变化」误判成没生效、把 429 误判成通道故障。
- **`view_api_doc` 分区号动态化**：工具描述不再写死 `01~08`，自动跟随文档分区（现 01~10）。
- **DSH 世界会话按需指路**：`world_push`/`world_pull` 工具 description 补关键语义提醒（调用瞬间可见，
  零常驻 token）；世界上下文提示词加「10 分区」指路，细节单一来源在文档。


### Added — 🧩 嵌入兼容层（DSH 插件化第一步：Copree 可作为插件嵌入宿主）

- **`?embed=1` 嵌入模式**：URL 带 `embed` 参数时，`Layout` 隐藏全局侧边栏/移动导航、`ChatArea` 隐藏聊天列表侧边栏，只渲染对话区——宿主（如 DSH）提供导航与外壳，界面即"融合"
- **嵌入桥 `frontend/src/embed/bridge.ts`**（新模块，纯增量、不改既有流程）：
  - 嵌入模式下向宿主 `postMessage` 上报：`ready`（加载完成+登录态）、`contacts`（群聊/私信列表，复用现有 `/groups`、`/dm/sessions` API）
  - 接收宿主指令：`navigate`（站内导航，走现有 router）、`refresh`（重新上报）
  - **安全边界**：`access_token` 绝不跨窗口传输，只交换联系人元数据与导航路径；宿主侧校验 `event.origin`
- **接入点**：`App.tsx` 创建 router 后注册导航器并初始化桥；未嵌入时零开销（`isEmbedded()` 惰性求值）

### Added — 🧩 行为插件协议（阶段二：Copree 自身全面插件化）

- **行为插件入口 `backend/app/services/plugin/api.py`**（新模块）：`@skill(type, category, name, description, config_schema)` 装饰器——**声明与行为合一**，一个装饰器同时完成 SkillRegistry 元数据注册 + skill_engine 行为处理器注册，消灭"skill.json 声明 + handlers 注册"的双写
- **插件目录契约扩展**：插件目录内可选 `plugin.py`（行为入口），与现有 `plugin.json` + `skill.json` 共存；无 `plugin.py` 的旧声明插件完全兼容
- **owner 来源追踪**：skill_engine 注册表条目从 `type → handler` 改为 `type → (owner, handler)`（单一来源，无并行表）；内置 handler owner=None 永不回收，插件 handler 停用/卸载时按 owner 精确回收，同名类型谁注册删谁
- **skill_bridge 统一加载/卸载**：`_load_behavior_plugin`（importlib 加载 plugin.py，owner 经 `set_current_plugin` 注入）+ `_unload_plugin`（行为 + 元数据一起回收）；生命周期与声明插件一致（启动 + 开关切换 + rescan）
- **示例行为插件 `backend/plugins/keyword-autoreply/`**：关键词自动回复（inject 类），展示完整契约
- **语言中立契约**：处理器上下文为普通对象/字典，不绑定 Python 特定机制，未来后端换语言时插件契约可平移
- **四层验证闭环**：加载层、分发层、开关层、集成层全部通过

### Added — 🔌 DSH 插件 `dsh-copree`（Copree 以一等公民嵌入 DeepSeek Harness）

- **同源网关（Host 半边 `dsh-copree/src/index.ts`）**：`/copree-api` HTTP 代理（剥离 hop-by-hop、转发 Authorization、502 兜底）+ `/copree-ws` WebSocket 升级代理（重放 `/ws?token=`、双向透传）——浏览器永不接触后端地址，**全程 loopback、零公网地址**
- **侧边栏 board（Client 半边）**：`shell.overlay` 全帧面板，左侧 rail（置顶私信/置顶群聊/私信/群聊 + 用户行 + 退出），右侧对话列（消息 + 专属 composer，发送到选中的 Copree 对话，不碰 DSH 会话语义）
- **登录统一**：`aisc.token` 存浏览器 localStorage；401 自动登出回登录页（不再静默显示"暂无联系人"假空态）
- **消息渲染完全照搬 DSH 风格**：复用官方 `MarkdownText`（GFM + KaTeX 公式 + 安全过滤）——我方消息 DSH 用户气泡样式（`--dsw-specific-bubble` + label-primary + 22px 圆角 + 名称靠右），对方消息原生排版；**任何针对 DSH 对话风格的主题/插件改动自动作用到 Copree**
- **图片附件**：`attachments` 里 `image/*` 提取，带 token fetch blob → objectURL（官方 resolveImage 同款机制，按 file_id 缓存）
- **群聊邀请卡片**：`message_type=group_invitation` 专用卡片（📨 + 邀请人/群名 + 接受/拒绝按钮 + 状态）
- **对话默认到底**：消息列表变化自动滚动到底部
- **私信/群聊设置页**：对话头部 ⚙——群聊（置顶/免打扰/公告/成员列表）、私信（置顶/免打扰）
- **群聊名字解析修复**：成员表（含 AI）按 `type:id` 缓存名字，不再显示"用户11"/裸"AI"

### Added — 🖼️ 沉浸式界面与功能导航（长线优雅：前端静态托管 + 同源 iframe）

- **前端静态托管（Host）**：`/copree-ui` 前缀服务 `BASE_URL=/copree-ui/` 构建产物（SPA 回退、路径穿越防护、immutable 缓存）
- **嵌入模式增强（前端）**：`api/client.ts` 嵌入时 API 基址走 `/copree-api` 代理；401 不跳出 iframe 改为通知宿主；`?token=` URL 注入复用登录态（写入后从地址栏清除）；router basename `/copree-ui`（修复嵌入路由 404）
- **沉浸式覆盖层（Client）**：`shell.overlay` 全局 iframe 面板——群聊头部"沉浸式"按钮（自动查 `/worlds/by-entity` 绑定世界 → `/copree-ui/world-view/{id}?embed=1`）+ AIC 侧边栏"功能"分组（群视界/好友/我的AI/管理/设置）+ DSH 设置页同款导航
- **登录引导**：iframe 内 token 失效时不再显示 Copree 自带登录表单，改为"请先在宿主应用中登录"引导页 → 通知宿主打开 Copree board 登录（登录态统一由 DSH 插件管理）

### Added — 🗂️ 群视界世界嵌入 DSH 工作区（对话用 DSH 的 agent，操作对象是 Copree 世界）

- **世界 → 工作区文件夹自动同步**：登录 Copree 或打开面板时，每个**自己创建的世界**自动同步为 DSH 工作区文件夹 `Copree群视界-世界名`（`$DSH_HOME/copree-worlds/` 真实目录 + `.copree-world.json` 世界身份）+ 一个 DSH 会话——全走官方 `workspaces.create` / `connectWorkspace` API，官方工作区自动显示、官方更新零影响，重复同步幂等
- **世界操作工具集（Host `world_*`，按会话所属世界自动路由）**：
  - `world_list_files` / `world_read_file` / `world_write_file` / `world_delete_file`：世界页面代码的文件树/读写（owner 鉴权写，`/worlds/{id}/files`）
  - `world_api`：世界受控 API（world/chat/memories/usage/groups/group-messages/state/data，经沙箱 `api_token` 鉴权）
  - `world_chat`：读写世界绑定群聊消息（以世界身份发送）
  - `world_lifecycle`：唤醒/休眠世界
- **按会话路由**：工具从会话 cwd 解析 `.copree-world.json` 定位世界——`Copree群视界-*` 会话里直接可用，普通会话自动拒绝并提示
- **token 仅内存**：client 同步时上报 `{sessionId, token}`，host 存内存供 owner 鉴权写操作（不落盘、不打日志）
- **同步 bug 修复**：`workspaces.create` 返回值主键是 `workspaceId`（非 `id`）——误用 `ws.id` 会跳过会话创建与 token 上报（浏览器实测暴露），已改 `workspaceId || id`
- **token 按世界路由**：改为 `{worldId, token}` 上报（DSH 新建会话流程会换 sessionId，按会话路由不稳）——工具按 cwd 解析世界后取 token，会话新建/切换不影响
- **诊断端点**：`GET /copree-worlds/status` → `{tokenWorlds, worldDirs}`（不含 token 明文），排障用
- **GitHub 式双向同步（世界文件本地镜像）**：
  - **工作区目录 = 世界文件镜像**：`world_pull` 把世界文件拉到工作区目录，agent 用 **DSH 原生 read/write/edit/glob/grep/bash** 直接操作（bash 可跑世界代码测试），`world_push` 同步回世界——不再依赖专用文件工具
  - **`.copree-sync.json` 快照 + 三路对比**：记录每文件「上次同步时本地/远端 mtime」→ 分类 added / removed / changedRemote / changedLocal / conflict（两边都改）
  - **自动拉取（温和）**：打开 Copree 时自动执行，仅当「本地无未推送修改且世界有改动」才拉取，返回 `+新增 ~修改 -删除` 报告；本地脏/冲突一律拒绝，绝不覆盖 agent 正在工作的文件（对话中文件不被自动改动）
  - **冲突裁决**：冲突文件不盲目覆盖——world_pull / world_push 默认跳过并报告，`force:true` 才强制；AI 读两边内容决定（保留本地 / 采用世界 / 手动合并）
  - **版本提示注入**：任何 world_* 工具执行后，若世界有更新未拉取 → 结果附 `updateHint`；有冲突 → 附 `conflictHint`（GitHub 式"有可用更新"提示）
- **新增工具**：`world_push` / `world_pull`（快照对比 + force）、`world_run`（后端沙箱跑 Python，24MB/10s）、`world_trigger`（触发 handle(event)）；原有 world_read_file / write_file / list / delete / api / chat / lifecycle 全保留
- **提示词注入**：systemPrompt 注册泛化世界操作引导段（工作区目录 = 世界镜像，用 DSH 原生工具 + world_push 同步；world_pull 拉最新）
- **世界页内嵌群聊修复**：`chat-panel.js` / `sidebar.js` / `adventure.js` / `identity.js` 读取注入的 `window.WORLD_API` / `WORLD_UI`（DSH 嵌入 = `/copree-api` / `/copree-ui`，独立部署保持 `/api` / 空）——世界页内群聊、平台菜单、SSE 不再落到宿主 SPA fallback
- **代理 Location 重写**：Host 代理对后端 3xx 重定向补 `/copree-api` 前缀（`/world/{id}/preview` → `/copree-api/world/{id}/files/...`），沉浸式 iframe 内世界页可完整加载

### Fixed — 🔧 同步正确性三连修（GitHub 式同步实测暴露）

- **快照文件自身误判**：`.copree-sync.json` 未被 `isMirrorExcluded` 排除，被 `walkDir` 计为本地文件且快照无其记录 → 永远判为「本地新增」→ 温和自动拉取**永远拒绝**（浏览器实测：token 正常上报、目录已建、文件却拉不下来）。已把快照文件加入排除列表
- **force 覆盖不全**：`world_pull force:true` 之前只放行检查，`pullTargets` 不含冲突文件与本地修改文件 → 返回 `ok:true` 却 `pulled:0`，**冲突/本地修改实际未被覆盖**。已补齐：force 拉取时 `conflict + changedLocal` 一并入队，以远端为准覆盖
- **快照无条件「洗白」（最严重）**：pull/push 成功后用**全量本地文件**重建快照并写入当前 mtime——未同步的本地修改、被跳过的冲突、远端新改动全部被标成「已同步」，**温和自动拉取从此检测不到世界新版本**（实测：制造真实冲突后 force 拉取返回「无变化」）。已改为：**只更新实际成功同步的文件**，其余保留旧记录；push 后用重新拉取的远端树写 rm（PUT 会更新远端 mtime）
- **验证**：温和拒绝（本地脏/冲突不覆盖）✓、force 覆盖冲突与本地修改 ✓、脏快照重建（force 全量以远端为准）✓、远端改动自动拉取 ✓、无变化识别 ✓

### Added — 📖 DSH 侧平台文档查看（world_view_doc）

- **受控 API 新增两个端点**（`world_proxy.py`，复用 `_authorize_world_api` api_token 鉴权 + 现有限流）：
  - `GET /world/{id}/api/docs` → 平台 API 文档分区列表（id/标题/区介绍，DB 权威）
  - `GET /world/{id}/api/docs/{section}` → 指定分区完整内容（防路径穿越，只允许注册表内 id，未知分区 400 / 缺失 404）
- **DSH 新工具 `world_view_doc`**（`dsh-copree/src/index.ts`，参照 `world_api` 同款实现：`registerWorldTool` + `resolveWorldApiToken` + `backendRequest`）：不传 section 列分区、传 section（01~09）读分区内容——DSH 世界会话与 Copree 世界 AI 的 `view_api_doc` 同一份文档注册表，写世界代码时接口细节按需可查
- **验证**：分区列表（9 区）✓、读分区内容 ✓、越界分区 400 ✓、世界会话路由引导 ✓

---
## [v0.3.9] - 2026-08-17

### Changed — 🔧 后端 main.py 质量重构（对照代码审查十项逐条处理）

- **启动流程拆分**：`main.py` 470 行 → 约 150 行；lifespan 拆到新模块 `app/bootstrap.py`，按职责分组 `_startup_db / _startup_plugins / _startup_workers / _startup_world / _startup_federation / _startup_brain_and_skills`
- **维护模式统一入口**：文件标记与文案逻辑集中到 `app/services/infrastructure/maintenance.py` 的 `MaintenanceManager`，中间件/lifespan/admin 路由全部走该入口（admin 原裸写文件与重复的文案读写已删除）；状态读取带 3s TTL 缓存，写入即时失效，开关切换零延迟
- **日志配置上移**：`basicConfig` 移到所有项目 import 之前，去掉重复的 `getLogger` 定义
- **import 整理**：无循环依赖风险的模块级 import 全部上移（已验证无模块反向 import main）
- **CORS 显式配置**：默认**不启用**（同源代理部署不需要跨域）；`ALLOWED_ORIGINS`（逗号分隔）配置后启用，含 `*` 时不携带凭据（浏览器规范禁止 `*` 与凭据组合），显式域名列表才允许带凭据
- **中间件独立模块**：IP 追踪 + 维护拦截迁到 `app/middleware.py`，`main.py` 只负责注册

### Added — ⚙️ 运行健壮性

- **后台任务自动重启**：`spawn_task` 支持异常退出按指数退避自动拉起（1,2,4...封顶 60s，超 10 次放弃记 CRITICAL），循环型 worker 全部启用
- **数据库就绪等待**：启动时 0.5s 轮询、30s 超时**快速失败**（替代原先 warning 后带病运行）；浏览器启动同样改为等待 DB 就绪而非 `sleep(3)`
- **定时任务对齐固定时刻**：审计清理/每日备份改为 `sleep_until(3,0)` 计算到下一次凌晨 3 点的延迟（原 24h sleep 会漂移且首次执行晚一天）
- **日志滚动**：`app.log` 改用 `RotatingFileHandler`（单文件 10MB，保留 5 份），避免无限增长占满磁盘

### Fixed — 🐛 修复

- **Alembic 静默吞日志**（隐藏已久）：`alembic/env.py` 的 `fileConfig` 会重置根 logger 配置并禁用已存在 logger，导致每次重启后"后台 worker 已全部启动 / Application startup complete"等日志全部消失——`disable_existing_loggers=False` + 迁移后恢复应用日志双通道
- **`_spawn` 异常日志失效**：`exc_info=exc`（异常实例被当 truthy，记录的是当前上下文而非捕获的异常）→ 改为 `exc_info=(type(exc), exc, exc.__traceback__)`
- **关闭流程重复日志**：删除重复的"系统关闭"日志行
- **X-Forwarded-For 伪造漏洞**：中间件手写解析 XFF 绕过了 uvicorn 的可信检查（`forwarded_allow_ips`），任何客户端可伪造审计 IP——改为只取 uvicorn 解析后的 `request.client`，安全默认不信任代理头（已验证伪造 XFF 被忽略）

---

## [v0.3.8] - 2026-08-17

### Fixed — 🔧 维护通知系统全面修复（概览页"通知"区块）

- **维护路径统一**：`admin.py` 不再硬编码 `/tmp`，全部跟随 `MAINTENANCE_DIR` env（与中间件一致）；预设/图片库文件移入 `settings.data_dir`（容器 `/app/data`，随数据目录持久化）
- **`X-Maintenance` 头不再漏发**：软维护开启时所有响应（含 `/auth`、`/admin`、`/maintenance-msg` 等 bypass 路径）都带头 → 修复"用户访问认证/管理接口后维护提示被误判已关闭而消失"的闪烁 BUG
- **维护弹窗图片可加载**：`/fs/public` 加入维护中间件 bypass → 硬维护期间维护弹窗配图不再 503
- **保存即同步**：保存文案 / 应用预设后广播 `maintenance_update`，在线用户实时看到新文案（此前需等开关或刷新）
- **`/maintenance-msg` 返回 hard/soft 状态**：前端刷新页面后能立即恢复维护提示（此前刷新后提示丢失，且非聊天页收不到 WS 广播）→ Layout 增加 30s 权威状态轮询兜底
- **软维护弹窗不再反复弹出**：关闭后本会话不再弹，管理员重新开启（WS 广播）后恢复
- **硬维护弹窗可关闭**：用户点"知道了"后本会话不再被轮询强制弹出
- **预设字段补全**：`_DEFAULT_PRESETS` 补齐 `hard_text_color/hard_image/hard_style/soft_style/soft_text_color`，应用预设不再丢字段（此前 `hard_style` 变 undefined → 弹窗样式错乱）
- **保存预设不再先删后建**：后端同名覆盖（upsert），避免网络失败时原预设丢失；预设保存/重命名/删除全部改为内联 UI（移除 `prompt/alert/confirm` 原生弹窗）
- **错误可见**：加载失败、保存失败、上传失败均有明确提示（此前静默失败，编辑器直接消失）
- **桌面端图片 URL 修复**：上传后的维护图片按实例地址拼 URL（此前硬编码 `/api` 前缀，桌面端加载失败）

### Changed — 🎨 维护通知 UI 重构（概览页）

- **OverviewTab 状态条**：改用 i18n key（zh/en/ja）+ 状态图标底色 + 操作中禁用/加载态 + 错误提示
- **MaintenanceMsgEditor**：硬维护/软维护均增加实时预览（弹窗/顶栏两种样式）；图片库改为缩略图网格（点选应用、悬停删除、选中高亮）；布局与全站卡片风格统一

### Fixed — ✍️ 维护文案编辑体验（用户反馈：不知道怎么保存 / 改了没变化 / 还有 emoji）

- **自动保存**：文案字段变更后 1.5s 自动保存（debounce），不再依赖页面底部的保存按钮；顶部保存栏显示实时状态（未保存 / 保存中 / 已保存 / 保存失败）
- **保存入口前置**：保存状态 + 「立即保存」按钮移到编辑器顶部，随文案编辑区一起可见（此前在页面最底部，滚动才看得到）
- **明确"为什么没变化"**：顶部显示当前维护开关状态（未开启 / 温馨提示中 / 暂停服务中）+ 提示条"维护未开启——保存的文案将在开启后展示"，避免误以为保存失败
- **清除管理界面 emoji**：维护模式状态描述（zh/en/ja 翻译 key）与编辑器区块标题（🔒📢 → lucide 图标 Lock/Megaphone）全部去除 emoji

### Changed — 🔘 维护文案分栏独立保存（暂停服务 / 温馨提示 各自保存）

- 「暂停服务」（硬维护）与「温馨提示」（软维护）两个区块**各自拥有保存按钮**（栏标题右侧），改完哪栏点哪栏
- 按钮状态独立：未修改显示「保存并更新」，有改动变「保存更改」（高亮主色），保存中 / 已保存（绿色）逐栏反馈
- 自动保存保留（1.5s 防抖，全量提交）：即使不点按钮也不会丢修改
- 顶部保存栏移除，改为轻量状态提示行（维护开关状态 + 未开启提示 + 错误信息）

### Fixed — 💾 维护文案持久化（用户反馈：改了颜色/弹窗方式保存后仍显示旧值）

- **根因**：维护文案 `maintenance_msg.json` 写在容器内 `/tmp`（`MAINTENANCE_DIR` 默认值）——容器重启（如部署后端修复）即清空，用户保存的颜色/弹窗配置全部丢失，回到默认值
- **文案文件移入持久化数据目录**（`settings.data_dir` = 容器 `/app/data`，docker-compose 挂载 `./data`，重启不丢），与预设/图片库同一持久卷
- **自动迁移**：启动时若旧 `/tmp/maintenance_msg.json` 存在则复制到新位置，历史配置不丢
- **保存前确保目录存在**（`makedirs`），数据目录不可写时返回明确 500 错误而非静默失败
- **修复前端自动保存闭包陈旧 BUG**：`save` 改用 `msgRef` 读取最新值，避免定时器/闭包把改之前的值写回

### Fixed — ⚡ 保存后立即生效（用户反馈：改了颜色/弹窗方式保存后立刻看不到变化）

- **根因**：管理员保存文案的请求走 `/admin/*`（维护中间件 bypass、无维护头），前端 `client.ts` 把它误判为"维护已关闭"→ 触发 `maintenance-cleared` → **刚保存完提示条就被清掉/重置**，看起来"改了没变化"
- **保存后即时刷新**：保存成功 / 应用预设成功后 dispatch `maintenance-saved` 事件，Layout 立即重新拉取权威状态并应用新颜色/弹窗方式（不再等 30s 轮询）
- **清除逻辑收窄**：`client.ts` 只在曾显示硬维护弹窗（`_maint_hard_visible`）且本次确认非维护时才清除；软维护提示的关闭统一由 Layout 轮询权威判定，不再被保存动作误触发


### Added — 🎨 主题选色定稿：应用第一轮投票 + 第二轮 A/B 快投

- **应用 ShuAICFR 第一轮方案为平台默认主题**（`index.css` 浅色 `:root`）：主色强调加深 `#6D28D9`，琥珀实底 `#B45309`，活跃绿 `#10B981/#34D399`，危险玫红 `#C9364F/#E84A69`
- **同步自选色候选板**（ThemeCustomizer「当前」标注指向新默认色，补齐缺失色）与 **皮肤「默认」卡片色板**（SkinPicker）
- **新增第二轮快投页面** `frontend/public/color-picker-r2.html`：每类型只剩 A（当前应用版）/ B（第一轮代表）两个候选，点选实时预览，提交即投票
- **新增第二轮投票接口** `/theme-vote-r2`（GET stats / POST 提交），数据存 `data/theme_votes_r2.json`，与第一轮隔离互不影响（自动发现注册，重启生效）

### Added — 🎨 主题设计工具：选色页体验产品化（用户要求：不做二轮投票，直接做成工具）

- **设置页「主题定制」升级为完整工具**（ThemeCustomizer 重写）：左侧手机壳**实时预览**（引用 CSS 变量，点选/输入即时变化），右侧每字段候选色板 + **原生取色器** + **自由 hex 输入**
- **预设系统**：内置「平台默认（定稿版）」「极光青碧」两套 + 用户自存多套「我的预设」（存 `ui_prefs.theme_presets`，JSONB 后端零改动）；点击整卡应用，删除按钮随时清理
- **皮肤冲突自动处理**：保存自定义色时若皮肤生效（皮肤会覆盖自选色），自动停用皮肤并提示，保证"保存了就能看到"
- i18n 三语（zh/en/ja）补齐，无 emoji

---


## [v0.3.7] - 2026-08-17

### Added — 🧩 统一插件系统（DSH 式插件化：目录即插件）

- **插件协议**：`plugins/<id>/plugin.json` manifest（id/name/description/category/version/author/icon/default_enabled），复制目录即安装、**装好即可用**（无需改代码/重启）
- **双目录扫描**：内置 `backend/plugins/`（随代码 git 跟踪）+ 用户 `$DATA_DIR/plugins/`（持久化，覆盖内置同名）；启动同步 + API 懒同步 + 管理面板手动重扫
- **两级开关**：`plugins.enabled`（管理员管理面板一键开放/关闭）+ `user_plugin_prefs.enabled`（用户设置页一键启用/停用）；生效 = 两者都开，默认即用
- **皮肤插件**：`skin.json` 声明 light/dark 两套 CSS 变量（含气泡 `--tw-bubble`），设置页一键启停 + 即时应用 + 登录/主题切换自动应用 + 皮肤互斥（启用 A 自动停其余）；停用后用户自选主色自然恢复
- **关闭回退保障**：管理员全局关闭/卸载后在线用户自动回退——WS 广播 `plugins_changed`（秒级）+ 页面切回立即刷新 + 60s 轮询兜底；技能类型随开关注销/恢复（SkillRegistry unregister）
- **技能插件**：`skill.json` 声明技能类型 → 注册进 SkillRegistry（`unregister` 支持注销），管理员关闭/卸载即回收，绝不误删内置类型
- **API**：`GET /plugins`、`POST /plugins/{id}/toggle`（管理员）、`POST /plugins/{id}/pref`（用户）、`POST /plugins/rescan`（管理员）
- **数据库**：`plugins` + `user_plugin_prefs` 表（Alembic `a9b8c7d6e5f4`）
- **内置示例**：极光青碧（skin）、写作工坊（skill：`writing_studio` 技能类型）
- **前端**：设置页外观新增「皮肤」区块（`SkinPicker`）；管理面板插件管理新增「统一插件」区（`PluginManager` 升级）

---

## [v0.3.6] - 2026-08-15

### Added — 🧠 Embedding 提供方插件化（向量记忆解耦 DeepSeek）

- **背景**：DeepSeek 官方无 embedding API（调用 404），向量记忆此前依赖 DeepSeek base_url 导致全库 0 向量、向量检索形同虚设
- **`embedding_providers` 包**（对齐 db_providers 模式）：`disabled`（默认降级）/ `ollama`（复用本地实例）/ `api`（OpenAI 兼容端点）/ `local`（fastembed 离线）四后端可切换，`get_embedding_provider()` 按 `EMBEDDING_BACKEND` 注册表选择
- **向量哲学**（对齐 dsh-mneme）：向量是可选增强——embed 失败返回 None 自动降级文本检索，永不打断主流程；配置与 chat 完全解耦（`EMBEDDING_BACKEND/BASE_URL/API_KEY/MODEL`）；接口语义对齐 OpenAI 兼容 `/embeddings`，未来转 JS 契约不变
- `get_embedding()` 签名不变，27 处调用点零改动；显式自定义端点走直连，其余走插件

### Added — 📐 向量维度可配置化（速度 vs 质量自选）

- `EMBEDDING_DIMENSION` 环境变量（默认 1536 兼容现状），模型列定义改读配置（不再写死）
- **prestart 启动自动对齐**列维度：无向量数据 → 直接 ALTER（毫秒级）；已有向量数据 → 明确告警不静默失败；维度优先级 DB 覆盖 > 环境变量
- **有数据安全迁移脚本** `scripts/migrate_embedding_dimension.py`（对齐 pg-raggraph expand/contract）：`prepare`（加临时列）→ `backfill`（并发重生成向量，幂等可重入）→ `cutover`（守卫检查后删旧列+改名+重建 HNSW）→ `cleanup`；另有 `fill` 子命令日常回填 NULL 记忆
- 共享模块 `scripts/_shared.py`（表清单/维度查询/URL 解析/并发回填集中一处，prestart 与迁移脚本共用）

### Added — 🖥️ Embedding 配置前端图形化（部署后零门槛管理）

- **DBConfigSource**（pydantic-settings `customise_sources` 官方机制）：settings 读取自动「DB 覆盖 > env > 默认」三层分层，所有字段零侵入生效
- 持久化：`system_settings.embedding_config` JSONB + api_key Fernet 加密；读路径走内存缓存（快），保存热更新（无需重启）
- 管理 API：`GET/PUT/DELETE /admin/embedding-config` + `POST test`（实际调 embed 验维度）
- 前端：系统设置页新增 **Embedding 向量配置卡片**（后端类型/端点/模型/维度表单 + 测试连接 + 保存生效 + 恢复默认）

### Added — ⚙️ 通用配置组机制（第二批前端化：runtime 参数）

- **`app_config_service` 泛化**：多配置组机制——每个组 = `system_settings` 一个 JSONB 列 + schema 声明；**加一组配置 = 加一个声明，前后端自动支持**（CONFIG_GROUPS 注册表）
- **通用 API**：`GET /admin/configs`（返回 schema）/ `GET|PUT|DELETE /admin/configs/{group}`
- **新增 runtime 组**（`runtime_config` 列，Alembic `e6f7a8b9c0d2`）：检索参数（default_top_k / vector_weight / bm25_weight / time_decay_weight）+ 显示时区 + 摘要缓存 TTL
- **前端 ConfigGroupCard**：读 schema 自动渲染表单（select/number/float/text/secret）+ 来源标注（界面改/环境变量）+ 保存热更新 + 恢复默认
- **小白友好**：每个字段带"是什么 + 为什么改"说明文案；手写 EmbeddingConfigCard 删除，由通用卡片替代（保留测试连接专属能力）

### Changed — 🌐 i18n 规范（翻译禁 emoji，文案走命名空间）

- **新增 `admin_config.ts` 分区**（对齐 tool.ts 模式）：`configGroup.*` 三语 zh/en/ja——UI 按钮/字段标签/说明 hint 全部 i18n 化，禁止硬编码中文
- **后端 schema 只下发 i18n key**（`label_key`/`hint_key`），前端 `t()` 翻译，多语言友好
- **翻译禁 emoji**：`✅❌⚠️` 等从字典移除，图标由前端统一渲染（lucide Check/X/Loader2 + 状态色）；清理主字典 3 处历史遗留 emoji

### Changed — 🔍 记忆检索对齐 dsh-mneme（关键词优先 + 向量补位）

- `merge_keyword_and_vector()`：关键词命中总是排最前（字面词精确性最高）+ 向量结果去重补位；重叠条目回填真实向量分（O(n) dict）；`mode` 元信息（keyword/vector）
- `extract_keywords()`：中文友好分词——标点拆分 + 2-gram 滑窗（修复长中文查询整段 LIKE 失效），纯英文不 ngram 防碎片噪音，去 3-gram 精简
- `_text_search_memories`：title 命中优先排序（对齐 mneme `CASE WHEN`）
- `recall_memory` 工具去掉 O(n²) 嵌套循环补丁

### Added — ⚡ 向量检索过滤先行（数据量增长时性能保障）

- Alembic `d5e6f7a8b9c1`：过滤列 btree 索引（rough `(owner_id, scope)` / detail `(rough_id)` / 消息表 `(group_id)`）——先按结构化字段缩小候选集（O(log N)），再对"自己的"少量记忆精确向量排序（O(M log M)），与全站总量解耦
- 对齐 pgvector 官方过滤策略（HNSW 过滤在低比例条件下召回崩，btree 过滤列才是低比例场景正解）
- 5 万行实测：有索引 2.4ms vs 无索引 4.0ms，行数越大差距越显著

### Changed — 🗄️ 迁移体系统一（Alembic）

- `embedding_config` 加列从自研 migration.py 统一迁入 Alembic（`c3d4e5f6a7b8`，IF NOT EXISTS 幂等），避免双轨混乱

### Added — 🎨 统一 UI 组件库 + 按钮规范（前端焕新）

- **UI 组件库** `components/ui/`：Button / Input / Modal / Select / Card / ConfirmDialog（confirmAsync 替换原生 confirm）
- **统一按钮规范**（`.btn-*` CSS 语义类，单一规范源）：3 尺寸（32/40/48px 固定高度，图标/无图标严格等高）+ 6 变体（primary/accent/secondary/ghost/danger）；圆角统一 rounded-lg（对齐群视界顶部按钮）
- **浅色主题对比度修复**：elevated/border 加深 + 全局 `.bg-elevated` inset 描边——修复 321 处旧按钮"与背景分不开"

### Changed — 📱 移动端全面适配（手机/桌面双端友好）

- **世界商城移出主导航**（侧边栏 + 手机底部），入口保留在世界列表页内
- **世界列表**：卡片手机竖排 + 操作按钮自动换行
- **设计页接口文档弹窗**：手机全屏（h-full + 横向分区栏）+ 桌面居中双栏；修复 flexbox 滚动陷阱（min-h-0）；下载弹窗改底部抽屉
- 商城搜索行手机换行（标签输入框响应式）

### 📚 文档

- 新增 `docs/memory_system/design/vector_search_and_embedding.md`：架构总览 / 插件化 / 维度配置 / 前端图形化 / 检索策略 / 索引性能 / 生产启用清单 / FAQ

---

## [v0.3.5] - 2026-08-13

### Added — 🎛️ 决策技能机制（阶段二核心闭环，产品定）

- **AI 自配置决策技能**：入驻 AI（agent 居民）/ 群助手各自持有自己的决策技能——「遇到什么情景我干什么、什么情景才唤醒我本体」；世界体系提供 list/write/delete_decision_skill 工具（ToolRegistry 插件，所有 AI 可自配置）
- **技能结构**：`{name, when:{event, conditions}, do, notify}`——条件 DSL 递归逻辑树（and/or/not 自由组装 + 字段运算 contains/starts_with/matches/数值比较）；do 三选一（reply_template 零成本 / call_tool 平台工具 / run_script 沙箱脚本）；notify=true=命中后仍唤醒本体，false=程序处理完即止
- **决策引擎**：群触发链路优先匹配（AI 自写规则 > mention_only 平台默认兜底）——命中且 notify=false → 程序化处理（reply 代发群，不唤醒 LLM）；未命中才走触发模式；群助手 LLM 调用带决策工具（仅工具调用时静默不回复）
- 情景：group_message 已接入；member_join/leave、friend_request、scheduled 引擎通用、事件钩子后续接

### Added — 🎯 群助手独立实体（不走 agents 表，产品定）

- **群助手 ≠ agent**：新表 `group_assistants`（group_id/world_id/group_type_slug/name/system_prompt/model/api_key 加密/config）——每群每助手一行；绑定群时按类型模板直接建实体，不再 create_agent/入群成员表/WorldAgent 登记，与群视界 AI 同形态（无账号、无好友）
- **触发链路改造**：`response_worker` 查 group_assistants → 独立 LLM 路径（system_prompt+群历史→chat_completion→发群消息 source=world 防循环→用量记账 agent_id=-2 虚拟聚合）；sender_id 用 -ga.id 负值避免与 user_id 冲突；群助手自己的消息不触发防自循环；群消息列表显示助手名
- **历史数据迁移**：agent 41「冒险团团长」→ group_assistant 1（7 条历史消息 sender 69→-1；清理 friendships/group_members/agents/users，备份在 backend/backup/）
- **群助手也可以是纯后端程序**：世界程序 main.py handle 直接接群消息处理（不依赖 LLM）

### Added — 🖥️ 页面静默事件通道（页面 → 世界程序，产品定）

- **`POST /world/{id}/api/event`**：页面操作（移动/攻击/开宝箱等）直接触发世界程序 handle(event)，**不产生群消息、不进群聊**（解决页面操作刷屏：30 步 = 30 条群消息）——用户登录鉴权 + 群绑定/成员校验 + 写限流；服务端注入 user_id/user_name（不信任页面自报）；常驻世界入队 / 临时触发同步返回 handle 结果
- **接口文档 06 补 4.1 章节**（世界 AI/页面代码可直接用）；世界 39 改造补丁（main.py 支持 page_command + game.js sendCommand 改走事件通道，数据区 root 属主待应用）
- 配套【消息同步纪律】强注入段：非必要消息不同步到群，前端能展示的一律不发群

### Changed — ⚡ 插入消息立即落库显示（产品定改）

- 之前：工具轮进行中发的普通消息**等下一轮 LLM 调用前**才落库+画气泡 → 用户感觉"没插进去"
- 现在：发消息**瞬间**立即落库 + 广播 [INSERTED]/[INSERT]（气泡马上画入、排队弹窗秒清）；`_inserts` 仅作上下文注入；轮次结束兜底补发未落库项（已落库跳过防重复）

### Fixed — 📊 LLM 缓存命中统计（显示 0% 实为提取 bug）

- 根因：DeepSeek 首轮 usage 顶层有 `cached_tokens`，**工具轮**只有 `prompt_tokens_details.cached_tokens` / `prompt_cache_hit_tokens` → `_record_usage` 只读顶层 → world_llm_usage 工具轮全记 0 → 前端统计 0%
- 修复：`_extract_cached_tokens` 三层 fallback；历史 27 条记录按 conversation_log 原始 usage 同秒重算（实际命中率 99%+，如 51730→51584）

---

### Added — 🎯 群视界触发模式（mention_only）+ 决策技能设计（产品定）

- **群消息非 @ 不触发 AI**（所有绑定群视界的群，暂时统一）：AI 不可能一直触发——大量群事件应由决策程序/世界程序处理，只有关键时刻（＠AI / ＠all / 群公告）才唤醒 LLM 本体。实现：`response_worker` 触发群助手前查群绑定世界 + `worlds.config.group_trigger_mode`（默认 `mention_only`；`all` 恢复旧行为）；拦截不影响世界程序感知通道（`world_event_hook` 照常喂事件，即「决策程序代替 AI」的雏形）；未绑定群视界的普通群零开销
- **配置 AI 可改、可随包分发**：世界 AI 工具集新增 `update_trigger_mode`（mention_only ↔ all，AI 自主决定本世界活跃度）；`export_zip` 附虚拟条目 `world_meta.json`（config 白名单快照，不落盘零污染）→ `import_zip` 读回合并——分享/发布世界时触发模式跟着走，导入只补缺失键、不覆盖宿主已有设置
- **决策技能设计文档**：`docs/group_world/design/world_decision_skill.md`——阶段一（触发模式）已落地；阶段二（决策技能）设计定稿：预置情景列表（群消息/成员进出/好友申请/定时/世界事件/命令）+ 决策技能结构（`when` 条件 DSL + `do` 动作 + `notify` 是否唤醒本体）+ 决策引擎（事件→技能匹配→程序化处理 or 唤醒 LLM）；条件 DSL 支持**递归逻辑树**（and/or/not 自由组装 + 字段运算 contains/starts_with/matches/比较）

### Fixed — 🧮 化学公式渲染（KaTeX \ce / mhchem 三连，产品实测暴露）

- **公式节点被 sanitize 剥掉**：`rehype-sanitize` 跑在 `rehype-katex` 前，defaultSchema 无 `math`/`math-inline` → `$...$` 公式变成裸 LaTeX 文本。修复：白名单补两节点（所有渲染入口走共享组件，单点生效）
- **KaTeX 不内置 mhchem**：`\ce` 是未定义命令——错误回退把 `\ce` 标红、花括号当分组吞掉、`->` 变数学减号 `−>`（即 "\ceCu+Cl2−>[点燃]CuCl2" 乱码的真相）。修复：加载 `katex/dist/contrib/mhchem.mjs`（side-effect 注册 \ce/\pu）
- **katex 双实例（真凶）**：rehype-katex 依赖 `katex ^0.16.0` 而顶层是 0.17.0 → npm 嵌套安装 0.16.47 → mhchem 注册到顶层实例、rehype-katex 用嵌套实例，\ce 永远未定义。修复：vite `resolve.dedupe: ['katex']` 强制单实例（KaTeX 官方推荐，对后人 npm install 复现同样生效）+ `optimizeDeps.include` 预构建；模块级验证 rehype-katex 与 mhchem 同源同 chunk

---

## [v0.3.4] - 2026-08-12 ~ 08-13

### Fixed — 🔔 系统通知链路 + ⚙️ 12h 空闲压缩（用户 API Key 失效场景暴露）

- **系统通知静默丢失（三层 bug）**：AI 的 API Key 失效（401）时，降级通知和最终错误通知都没送达用户——
  ① `get_or_create_dm_session` 返回 dict 但 executor 用 `.session_id` 访问（AttributeError）
  ② 系统用户 0 与用户非好友，新建 0_X 会话被 `_require_friendship` 拦截
  ③ 通知直接写 DMMessage 不更新会话 last_message → 侧边栏预览/排序不刷新
  **修复**：通知复用 `send_dm_message(skip_friendship_check=True, message_type='system')`（统一处理 last_message/未读标记），
  chat_api 包装方法补齐透传（原来 skip_friendship_check/message_type 被吞）
- **12h 空闲压缩不触发**：原逻辑在工具循环内（LLM 调用成功后）——key 失效等早期失败根本执行不到，
  且判定只看"最后消息距今"（12 天没聊但今天刚发消息时历史堆积不压缩）
  **修复**：压缩前移到首次 LLM 调用前 + 增加"对话跨度"判定（首条→最后消息 > 12h 也压缩），
  效果：137→22 条（-83% token）；当天对话不误压缩
- **12h 空闲压缩优先级**（产品定）：API 通 → LLM 总结压缩（保要点）；API 不通/总结失败 → 内联截断兜底

### Added — 🛠️ 世界 AI 工具链升级（2026-08-13 下午，AI 工具调用体验 + 前端展示）

- **普通消息插入工具轮**（产品定）：AI 工具轮进行中用户发普通消息→不打断，在下一轮 LLM 调用前自然注入（drain → 落库 → 发事件 → 拼上下文）；只有命令（/compact 等）等整轮结束。两段式协议：[INSERTED] 信号清排队弹窗 + [INSERT] 真实 id 画气泡（断联后 loadChat 位置正确）
- **工具轮+收尾轮流式化**：正文/思考逐 chunk 显示（不再等整次调用结束一次性出）
- **工具状态事件 [TOOL_UPDATE]**：同 tool_id 多状态气泡（running 正在执行 XX → update 进度 → done 完成原地更新）；run_world_code 分阶段（创建脚本→运行中→返回结果）；落库按 tool_id 去重只留最终态；WorldChatMessage 加 tool_id 列
- **思考/正文独立气泡**：思考用思考 id、正文用正文 id（顺序递增）；无正文不占位（去「（…）」）；思考默认 2 行+渐变淡化+点击展开；工具长摘要折叠+展开/收起
- **file_grep + file_read 分页**：AI 看文件先 grep 定位（命中行+行号）再 offset/limit 分段读，不再整文件全读（对齐 OpenClaw read 设计）
- **i18n 命名空间化**（对齐 i18next）：新增 i18n/tool.ts 独立分区（zh/en/ja），translations 两级结构 {lang:{common,tool}}，getTranslation 支持 'ns:key' + {var} 插值；现有 t('xxx') 完全兼容；未来分区同模式加文件、支持 addResourceBundle 补充
- **stream_world_chat 大函数拆分**：545→370 行（_prepare_world_chat/_execute_tool_round/_inject_pending_user_messages 收口）

### Fixed（2026-08-13 下午，AI 工具调用连环失败系列）

- **DeepSeek 流式分片**：name 分片带 id、arguments 分片不带 id → 按 id 区分导致分离。修复：id 主 key + index 桥（index_to_id），两者结合不再二选一
- **DeepSeek DSML/XML 格式**：模型有时把工具调用输出成 XML 文本（<invoke name=...><parameter name=...>）而非标准 JSON——参数全丢、AI 连环失败（20+ 次）。修复：_parse_dsml_tool_calls 兼容两种 XML 格式
- **工具轮 [DONE] 误发**：_stream_llm_once 每轮 yield [DONE] → 前端收到即退出订阅，第二轮之后不显示。修复：[DONE] 只由主流程整轮结束时发
- **工具轮 400**：thinking 模式 assistant 消息必须回传 reasoning_content（首轮+后续轮都补）
- **单波浪线被当删除线**：remark-gfm 默认 singleTilde=true（GitHub 非标准扩展）→ '1~2和2~3' 被渲染成删除线。修复：singleTilde:false，只有 ~~ 才是删除线（全局共享组件单点修复）
- **迁移 revision 撞号**：a1b2c3d4e5f6 已被占用 → 改为 b1c2d3e4f5a6

### Added — ✨ 前缀内容版本化（锁）+ 群类型无限 + Skill 分层注入 + 世界直开

- 🔒 **前缀内容版本化（所有进前缀的内容保证缓存命中）**：用户可改提示词（world-prompt-{id}）、强注入段（forced-prompt）、昵称（world-name-{id}）、主站 agent 提示词（agent-prompt-{id}）统一走 capability_versions 版本链（known 告知 / effective 生效）——用户改提示词、系统更新强注入、改名都是正常操作，**不再断前缀缓存**；变更只动态尾部注入 changelog 告知；compact / clear（= 新对话）解锁后正式生效；锁定态尝试应用变更 → 拒绝 + 后端报错记录（guard_apply_change 防御）
- 🔒 **强注入段从用户可改中提出**（产品定）：平台强约束（工具约定/能力边界/接口文档/记忆约定/UI/群类型/侧边栏/路径/编号/运行规范）不再是散装拼接，独立为 FORCED_PROMPT_SEGMENTS + `forced_prompt` 字段返回前端只读展示；群类型约定强化（写世界类型 + AI 加入类型，关键剧本角色可用角色名/职位名做类型名）；新增【AI 侧 skill】（按类型分层注入）与【入口场景响应】（私信/群聊/直进不同 index 响应）强注入段；昵称正式注入对话（之前 AI 不知道自己叫什么）
- 🗂️ **Skill 分层注入**：manifest 支持 `types` 字段（省略/["*"] = 所有类型通用；["blacksmith"] = 仅铁匠类型可用）；`build_world_tools_for_type` 按绑定类型过滤；群 AI 能力清单按 agent/群绑定类型取并集注入（world_chat_service / llm.py 双入口）
- ∞ **群类型 bind_limit=-1 = 无限**（不是极大值凑）：默认类型群聊和 AI 数目都无限；前端显示 ∞、满员判断跳过 -1、新建类型输入支持 -1
- 👁️ **世界列表「打开」按钮**：/world/{id}/preview 新窗口进沉浸界面——只绑定 AI（entity_type='agent'）的世界也能直接进去看，不依赖群聊

### Changed

- **设置页提示词 UI 重做**（WorldCreatorConfig）：群视界机器人名字默认收起 + 🖊 图标点击可改；系统提示词默认收起 + 🖊 点击展开输入框；新增「平台强注入提示词」深灰只读展示（🔒 不可修改）；对话生命周期区块移至模型与参数上方；删除重复的 LLM 缓存命中率块
- **群视界定义更新**（docs）：从仅聊天，到可编程、可视化、AI 可入驻的世界——造物主（世界 AI + 用户）/ 原住民（用户 + 用户的 AI）；设计文档补 8.5 群类型系统 / 8.6 世界运行不强制绑定群 / 4.3 入口场景响应 / Skill 分层注入三·六
- **强注入段瘦身 4129→3326 字符（-20% token）**：删【工具约定】段（工具描述已通过 function calling 传，重复）；记忆约定 607→129 只留结构化记忆核心；新增【注意事项】（含糊主动确认/不重复工具输出/建议要阐述/收尾实质内容）与【设计美学】（配色/层级/留白/移动端/动效/加载空状态/引导，占一部分控制 token）段；世界运行规范与接口文档导引保留
- **强注入段新增【内容提炼与动态加载】原则**（产品 2026-08-12 定）：剧情/大量按钮列表/大量设定等**内容不准写死在渲染中**——能提炼为文档/列表/数据文件的提炼掉，页面动态加载（fetch/import）；不固定数目；资源同理；同构多实例（NPC/卡牌/角色）**每个实例一个文件**（如 npcs/lihua.json），改内容/新增实例都不碰渲染代码

### Added（续）

- 🧠 **记忆管理增强（世界 + 主站对齐）**：manage_records / sr_* 新增 `rename`（category/sub_key/field 任一级改名）与 `move`（整组或单条跨目录移动）动作；记忆工具描述补 `user` 个性分类约定（偏好/风格/审美/习惯/关系）；新增 `build_memory_map` 缩进树记忆地图（只注入有内容的路径，空目录不出现），新会话/clear 后自动注入、普通延续对话不注入（省 token + 缓存稳定）；⭐（重要）/❗（硬约束）软锚定——value 前缀标记在地图上提到名字前，产生 Attention 特征峰值引导 LLM 优先处理；主站 format_db_records_for_prompt 重写（瘦身 + 软锚定 + 去 emoji），两套记忆行为统一

### Fixed — ⚡ LLM 缓存命中率修复系列（世界 AI 工具轮 + 前缀稳定）

- **工具轮 payload 漏 max_tokens**：首轮手写流式带 `max_tokens: 64000`，`_stream_llm_once`（工具轮）漏了 → DeepSeek 前缀缓存 key 不同 → 工具轮永远 miss 首轮缓存（命中率 99%→0%）。修复：补 `max_tokens` 对齐首轮
- **payload 字段顺序敏感**：字段顺序不一致也会让缓存 key 不同 → 工具轮 payload 字段顺序对齐首轮（彻底排除顺序敏感）
- **世界时间在 system 前缀内**：活跃对话每轮时间变化 → 前缀 hash 变 → 缓存全 miss。修复：世界时间移出 system 前缀
- **usage 收集依赖 choices**：DeepSeek usage 块可能带非空 choices → 用量/缓存统计漏记。修复：fetch `j.get("usage")` 独立于 choices 判断（命中率统计 110→118 次）
- **命中率不随对话刷新**：普通对话（非工具场景）完成后不更新缓存统计 → 前端累计命中率越看越低。修复：普通对话完成也刷新
- **思考折叠收尾**：preview 凑满两行即固定（无换行长思考也固定，不再显示最新）；折叠简化为渲染层截断（一个对象，收起只渲染前两行）；残留插入消息在 turn 结束时补发（前端气泡不再丢失）

### Docs（2026-08-12 新增）

- 7.6 触发与并发调度：群 AI 走红黑树（MAX_CONCURRENT=3）受排队影响；世界 AI 拉模式（HTTP 流式）不受；世界程序节流合并通道不受
- Skill 设计准则（产品定）：可操作性 / 简便性 / 效果最大化——AI 用最少次调用完成用户意图 = 好 skill（含反面例子 add_item/remove_item 应合并为 give_weapon）
- 适时拆分文件原则：文件大了/不利维护就拆成职责单一的小文件
- 内容提炼与动态加载（6.2）、前缀版本化 mermaid（锁定/解锁）

---

## [v0.3.3] - 2026-08-11

### Added — ✨ 接口文档服务 + 世界包下载/导入 + 会话体系 + 结构化记忆

- 📖 **接口文档服务**：设计页/管理页查看平台接口文档（与世界 AI `view_api_doc` 同源，代码区 `services/world/api_docs/` 随 git 跟踪、改文档即时生效）；分区查看 + 单区/全部合并下载；**md → docx 导出**（pandoc 可选能力，管理页插件管理一键在线安装，未装自动隐藏入口）；**docx 产品化**——表格边框 + 斑马纹（python-docx 硬编码，任何查看器显示）、品牌紫标题/表头/链接（#7C3AED）、代码块 Consolas 9pt + tango 语法高亮、LaTeX 公式转 Word 原生 OMML
- 📦 **世界包下载/导入**：设计页工具栏（桌面文字按钮/移动端图标）——下载弹窗（完整备份含数据 / 仅代码与资源）、导入弹窗（保留数据文件推荐 / 连同数据文件替换，**同名替换增量合并**、包外文件保留，导入前确认）；Windows 解压中文文件名正常（UTF-8 flag）；`import_zip` 默认跳过 content/ 数据文件（商城导入新世界不受影响）
- 💬 **对话会话体系**：/new /sessions /use /pin /unpin + 生命周期（auto_new 每日 4 点按用户时区、idle compact 18h、retention 90 天 + 收藏保护）；流式正文根治（气泡函数式更新，不再丢内容）
- 🧠 **结构化记忆**：world_structured_records 表 + `manage_records` 工具（set/get/list/summary/categories/delete），prompt 约定「记忆一律用 manage_records」（DeepSeek 无 embedding，向量 404 不可靠）
- 🔗 **绑定群 + AI 重做**：BindGroupModal 双 tab（群聊/AI）+ bind-entries 批量接口 + 默认类型「默认类型」开箱即用；`group-types` 按 entity_type 分别统计绑定数（AI 计数此前从未正确）；入口分流 WORLD_ENTRY（沉浸窗口按群类型/私聊/直进渲染）；默认群类型 + prompt 群类型约定（slug 稳定/改名不动/默认兜底）
- 🏪 **商城 GitHub 完善**：实例配置入口（管理员配实例 token）、token 回退 .env、挂载即预加载快照、绑定弹窗「去 GitHub 生成 token」链接
- 👥 **群聊体验**：邀请成员弹窗（好友列表分页/已选独立列表/搜索置顶/在线圆点）、AI 好友 friend_id=user_id 标准统一（兼容旧数据双通道解析）

### Fixed

- 🔥 **422 local_kw 悬案（根治）**：`/kb/status`、`/kb/install` 带 token 必 422 `local_kw required`——路由 `Depends(_async_session)` 把 **async_sessionmaker 实例**当依赖，FastAPI 从 `sessionmaker.__call__(self, **local_kw)` 挖出必填 query 参数；源码 grep 不到、看起来像 nginx 拦截（改路径/换词/查反代全无效）；修复：`Depends(get_db)`，排查法 = 打印 route.dependant 依赖树；全项目扫描确认无同类写法（330 处 get_db 均安全）
- **export_zip 路由签名漏 db 参数**（`_require_owner(db,...)` 直接 NameError 500）——接口从未被 HTTP 调用过，加下载 UI 才首次触发
- **绑定 AI 显示群类型**：文案按 tab 区分（AI 类型 / x 个 AI / 勾选 AI），bound_count 按 entity_type 统计
- **docx convert 500**：subprocess input 传 str → encode utf-8；模板注入误判（pandoc 默认 Table 自带 firstRow tblStylePr 被当「已注入」跳过，边框/斑马纹从未生效）
- **流式正文三连修**：收尾总结刷新后消失（full_content 只写强制收尾分支）、recall_memory 分词回退（整串子串匹配永远空）、工具轮思考保留（首轮兜底 + note 补 reasoning）
- **暗色模式**：hljs 补 tag/name 颜色、滚动条交汇角落不再白块、文档弹窗表格夜间变量默认值
- **WS/好友**：群聊推送补 sender_name（以 users 表为准）、thinking/typing 事件统一 user_id（前端禁止 agent_id）
- **AI 文件跳转**：load 闭包 currentFile 冻结 → currentFileRef；拖拽手柄 z-index 统一（高于遮罩）
- **代码质量加固（外部 review 三轮 15 条，采纳 8 条）**：
  - main.py：维护/日志路径 env 可配（MAINTENANCE_DIR/LOG_FILE）、lifespan warning 补 exc_info 保留 traceback、全部 18 个后台任务统一 `_spawn` 异常监控（Worker 异常退出记 ERROR 不再静默）
  - executor.py：LLM 压缩失败降级 inline 兜底并标记（不再反复重试耗 API）、中断消息注入异常时回写缓冲（不再永久丢失）、`_is_conversation_idle` 未来时间统一防御、max_tool_rounds 默认 3→5（典型 5 轮工具链不再被截断，有 end_turn 提前退出不会死循环）
  - 驳回 7 条均附依据：连接池已显式配置（10/40/pre_ping）、chat_chain 单线程原子无竞态、联邦重连已有指数退避+上限、沙箱路径已 resolve+startswith 校验、状态栈双写完整持久化、pending_results 用 tool_call_id 关联与顺序无关、「统一会话」是坏建议

### Changed

- **接口文档路径统一 /kb**：前端路径不带 /api 前缀 → 浏览器单 `/api/kb/...` → vite 剥一层 → 后端 prefix `/kb`（避开双 /api/api 段）；路径不再含 export 词
- **后端重启建议 `up -d --force-recreate`**（restart 个别情况留孤儿 uvicorn 占 8000 致诡异 500/新代码不生效，`docker top` 见 2 个 uvicorn 即实锤）——troubleshooting 2.4 + 手册速查已更新
- **文档体系**：troubleshooting 新增第九章（422 依赖注入坑）+ 错误码表 422 行、开发者手册加「依赖注入红线」、新增接口文档服务设计文档（api_docs_service.md）、SUMMARY v3.2.1
- **类型债清零**：tsc --noEmit 38 → 0（顺带修真 bug：setActiveSection→setActiveTab、WebviewWindow await、demoSetup blobToBase64、Toggle enabled→checked）
- **图标统一**：全项目残留 emoji/符号 → lucide（下载/导入用 Download/Upload 成对）

---

## [v0.3.2] - 2026-08-09

### Added — ✨ 情感状态栈 + 商城 GitHub 体系 + 群类型系统

- 🎭 **情感状态栈（交接驱动）**：Plutchik 8 轴情感向量（独立轴，双高双低可表达）+ `update_emotion` 工具（增量/完整向量/概括词）；摘要只注入「当前帧+本次交接」（旧交接不重复注入）；pop 选择性回跳（target_frame_id + 跳过层归档汇报）；pop 回来交接（📝 刚完成）；分状态调用计数 + mood homeostasis 情感衰减；工具按状态隔离；摘要上限 500 可配（agents.state_stack_max_chars）；配置开关 emotion_vectorized
- 🏪 **商城 GitHub 同步（机器人模式）**：仓库写权限只给机器人（系统 token），用户 token 只验证身份；目录所有权（worlds/{世界名}/ 只能写自己的）+ 查重 + **双签名**（作者 Ed25519 + 机器人背书）；GitHub 数字 id 身份锚（改名不变）；快照缓存；同步状态三态；token 全加密 + 管理员脱敏（前4后4）；用户 GitHub 绑定（我的页/商城页）
- 📂 **群类型系统**：世界预设群类型（规则/绑定上限/助手模板），**配置在 group_types.json 随世界打包**、状态在 DB（slug 绑定）；群绑定类型时按模板自动创建群助手（agent 归属群、不占额度）；群主填 API/一键全局（加密）；群视界机器人 get/update_group_types 工具；群消息事件注入 group_type
- 📥 **世界 AI web_download**：下载网页资源到世界文件夹（两阶段用户确认 + SSRF + 扩展名白名单）；世界文件上限 5→32MB
- ⚡ **性能**：向量记忆 HNSW 索引（rough/detail/world_ai_memories）；前端虚拟列表（ChatView 窗口化渲染）
- 💌 **好友申请 AI 闭环**：AI 上下文注入待处理好友申请（📨 申请人+留言）；新增 handle_friend_request 工具（accept/reject，防越权校验）；auto_respond_friend_request 触发独立事件处理（不建 DM 会话，通过与否由 AI 自主判断）

---

## [v0.3.1] - 2026-08-07

### Added — ✨ 世界商城 MVP + 沙箱加固 + 群 AI 能力闭环

- 🏪 **世界商城（MVP）**：`world_market_items` 表 + `/market` API（发布/列表/搜索/标签过滤/详情/一键导入/下载/下架）；发布 = 世界代码区打包（不含 content/）；导入 = 一键创建新世界 + 安全解压；前端商城页（卡片/搜索/发布弹窗）+ 群视界页顶栏入口 + 设计页「发布」按钮。
- 🛡️ **沙箱加固（v2）**：skill 执行从进程内 harness 升级为**子进程沙箱**——`sandbox_isolate.py`（Landlock 锁文件系统 + seccomp-BPF 禁危险 syscall，ctypes 直调）+ `skill_runner.py`/`skill_sandbox.py`（协议转发 ctx，一切 IO 回宿主校验）；世界代码沙箱同样注入隔离（保留网络/线程，禁 execve/挂载/ptrace 等）；对抗性验证通过（读 /etc 被 Landlock 拒、Popen 被 seccomp EPERM、socket 无模块）。
- 🤖 **群 AI 世界能力闭环**：世界侧 skills 工具化注入群 AI（function calling 直调，effective 版本快照）；`world_command` 文本命令路径；【本群世界】能力清单 + 同名冲突策略——**同名 skill 去重注入（当前群世界优先）+ 工具定义自动带可选 `world_id` 参数**（AI 可指定执行哪个世界的版本，未绑定世界拒绝）。
- 🧠 **世界 AI 能力边界认知**：【能力边界】system prompt 段——造物主（平台工具+设计侧 skills）vs 居民（世界侧 skills+world_command）讲清楚，AI 不再误答"群 AI 没有工具"。
- 💬 **世界 AI 中间轮输出**：工具循环中间轮的正文不再被吞——流式展示 + 落库 note（历史可见、不进上下文），每轮说的话独立气泡。
- 🗄️ **列槽位健康检查**：启动时扫描全表 pg_attribute 槽位（>1100 warning / >1400 ERROR），防历史 ADD/DROP COLUMN 残留触顶 1600 上限；迁移异常改 print+logger 双通道完整打印。

### Fixed

- **groups 表 1600 列上限事故**：历史反复 ADD/DROP is_federated 积累 1584 个 dropped 槽位 → 任何 ALTER ADD COLUMN 失败 → 新代码首次启动崩溃（exit 3）。修复：RENAME 重建法清 dropped（34 行数据零丢失、11 个外键全恢复）；根因是 `--reload-exclude` 隐式开启 reload + 改文件触发并发启动。
- **世界设计页聊天栏挤出屏幕**：窗口 resize 不回收宽度 + 上限未按文件树实际宽度反推 → 渲染层 clamp 双保险（hook 层 resize 回收 + maxWidth 兜底）。
- **AI 建议未在正文阐述**：suggest_questions 工具描述 + 【工具约定】要求"阐述建议或说明生成逻辑"（可概括，不强制逐一）。
- **世界设计页预览只显示编辑区**：预览模式隐藏文件树+手柄，iframe 撑满文件树+编辑区整块。

### Changed

- **页面标题栏统一底座**：新增 `PageHeader` 组件（h-14 + border-b + bg-surface），群视界/商城/我的/用量/存储空间接入；设计页/管理页顶栏高度对齐。
- **群视界列表页主题适配**：硬编码灰色全部替换为 CSS 变量体系（日夜间一致），世界卡片/弹窗/空状态美化，新增删除世界按钮。
- **世界 AI 建议与中间过程**：见上。
## [v0.3.0] - 2026-08-05

### Added — ✨ 群视界（Group World）：群聊即世界（阶段 2 全部完成）

- 🌍 **世界实体体系**：`worlds` / `world_bindings`（群聊/私信/用户绑定入口）/ `world_ais`（世界 AI 专属表）/ `world_chat_messages`（世界级会话）/ `world_ai_memories`（向量记忆）/ `world_llm_usage`（缓存命中统计）。
- 🤖 **群视界机器人**：每世界一个专属世界 AI（独立表，非 agent）；22 个工具（文件/积木/记忆/上网/沙箱/群聊/接口文档分区）；设计页右侧对话，服务器端 worker 全程执行。
- 🧱 **世界代码沙箱（2.1）**：subprocess + resource.rlimit（内存/CPU/FSIZE/CORE/NPROC）+ 超时 killpg 强杀进程组 + env 白名单（不泄漏后端密钥）；配额语义：有人在线 128MB / 无人 64MB（32MB 解释器硬下限）；`POST /worlds/{id}/run`。
- ⚡ **触发文件（2.2）**：世界入口 `main.py` 实现 `handle(event) -> dict`，harness 零框架依赖，`python -I -X utf8` 隔离执行；`POST /worlds/{id}/trigger` + `run_world_code` 工具。
- 🔐 **受控数据 API + 群聊写 API（2.3 + 2.4）**：每世界专属 token（懒生成存 config，零迁移）；数据面（世界/对话/记忆/用量/绑定群）+ 群聊写面（发消息/改角色/踢人，仅绑定群、管理仅群主/管理员）；动态限流（基础 + 每人加成 × 活跃人数，4 字段可配）；复用 world_tools 同一份执行逻辑。
- 🔄 **群消息钩子**：群消息 → 世界程序 `handle(event)` 异步感知（2s 节流合并可配；`source="world"` 防自触发死循环；不影响世界 status）。
- 🏠 **常驻推演（2.5）**：`resident: true` + `tick_interval`——世界程序常驻后台：`handle(event)` 事件处理 + `on_tick()` 定时推演 + `on_stop()` 优雅退出存状态；手动唤醒启动/休眠停止/后端重启自动恢复；默认不限常驻个数。
- 📡 **实时状态通道（2.5）**：世界代码 `POST /world/{id}/api/state` 发布状态 → 页面 `EventSource /world/{id}/events`（SSE）实时接收——零轮询、连接发快照、15s 心跳。
- 🎨 **设计页 + 沉浸界面**：世界文件树/代码编辑/预览/上传/删除/代码高亮日夜适配；沉浸界面 iframe + 世界变量注入（`WORLD_ID`/`WORLD_NAME`/`WORLD_AI_ID`/`GROUP_ID`，零硬编码哲学）。
- 🧩 **积木体系**：`data/world_blocks/` 预制组件（群聊对话窗 v1.1.0；**2D 冒险游戏 v1.0.0 已落地**——示例世界「星野镇」：游戏页面 + 命令输入框 + 群消息关键词语法提取驱动 NPC + SSE 实时状态）；世界 AI 可查/看/应用。
- 🗂️ **API 文档分区**：`data/world_api_docs/` 9 个分区（变量/WorldUI/文件/积木/群聊/页面/通知时间/错误安全/受控 API），AI 按需打开。

### Changed

- **唤醒改手动模式**：世界状态只由手动 wake/sleep 控制（唤醒后保持活跃，不再 10 分钟自动转休眠）；调度器保留开关可恢复。
- **沙箱全局并发排队**：`SANDBOX_MAX_CONCURRENT`（默认 4）信号量限并发，返回 `queued_ms` 可观测排队耗时。
- **世界变量注入增加 `WORLD_NAME`**（页面可显示世界名）。

### Fixed

- **后台配额 24MB 失效 bug**：RLIMIT_AS 虚拟内存口径下解释器 import 需 ≥32MB，24MB 导致后台触发从未真正跑通过 → 默认 64MB + 32MB 硬下限。
- **沙箱中文输出 ascii 崩溃**：`python -I` 忽略 PYTHON* 环境变量 → 统一 `-X utf8` 强制 UTF-8。
- **常驻 harness exec 竞争**：harness 文件删除与子进程 exec 竞争导致 exit 2 → 放 /tmp 不删除。

---

## [v0.2.8] - 2026-07-28 ~ 08-02

### Added

- 🎨 **魔视界（Magic Vision）CSS 滤镜系统**：10 种 CSS 滤镜可视化调节面板（blur/brightness/contrast/drop-shadow/grayscale/hue-rotate/invert/opacity/saturate/sepia），三种作用域（全部/仅图片/仅 UI），`ui_prefs.magic_vision` JSONB 持久化。v1.1 新增 `data-mv-force` 补偿标记：hue-rotate 嵌套叠加导致的状态点颜色不一致通过补偿算法统一；关闭开关立即持久化，不再依赖“应用”按钮。详见 `docs/magic-vision.md`。
- 🖼️ **群聊头像设置**：默认图标 / 成员头像排列（2×2 网格）/ 自定义上传（方形裁剪 + 压缩 + 缩略图），`avatar_mode` 字段持久化，头像缩略图 128×128，跨扩展名清理旧头像。
- 👑 **转让群主**：群设置支持将群主身份转让给其他成员。
- 🎛️ **群聊侧边栏大改**：置顶/折叠/优化，`user_group_preferences` + `user_dm_preferences` 表。
- 🛡️ **注册通道开关**：管理员可关闭公开注册，仅后台手动创建 / CSV 批量导入用户。
- 📝 **审计日志系统升级**：用户行为审计（登录/注册/发消息）+ IP 记录 + IP 地理位置（geoip，可切换后端）+ 保留天数可配置（默认 90 天）+ 消息审计只存 message_id（默认永久）。
- 🧹 **管理后台文件清理**：无引用头像 + 失效映射清理，`last_cleanup_stats` 记录。
- 🖱️ **存储页文件预览**：独立存储页面，文件支持点击预览。
- 🌐 **纯前端演示站**：GitHub Pages 部署（HashRouter + fetch mock + localStorage 数据层），DemoChat 复用主应用完整 UI，支持 API Key 设置 + 消息发 DeepSeek。
- 🏷️ **群名片/大图查看**：群聊头部头像、群名片、头像大图查看。

### Changed

- 🔄 **魔视界 v1.1**：`data-mv-force` 补偿标记 + 关闭立即持久化 + MutationObserver 动态补偿（防抖 120ms，只在新增标记元素时触发）。
- 📡 **在线状态实时推送**：`state_change` 事件广播——AI `switch_state` 后向相关 DM 对方推送，真人上下线（WS 订阅/断开）同样推送，前端 DMChatView 实时更新状态点。
- 🛡️ **工具状态兜底**：`get_allowed_tools()` 对未知/遗留状态（如 offline）兜底到 inactive 工具集（17 个），blocked 保持 0 工具，修复 AI“裸奔”死循环。
- 🔧 **`list_available_skills` 修复**：`seg_tools` 为 dict 列表却与字符串集合比较导致 `unhashable type: 'dict'`。
- 🐞 **滚动系统修复**：`scrollIntoView` 会连带滚动外层 overflow-hidden 容器（main/Layout），把标题栏滚出视口。新增 `utils/scroll.ts`（`scrollToInContainer` 只滚容器本身）+ 聊天页 main 改 `overflow-hidden`，三处调用点统一修复。
- 🧩 **按钮嵌套修复**：ChatArea 群聊标题栏 button 套 button（GroupAvatarHeader 自带 button），改为直接传 onClick。
- 🖌️ **气泡背景拆分**：`bubble-content` 拆为背景层（上色/圆角/边框/阴影，参与魔视界旋转）+ 内容层（文字/图片，图片保持清晰），尺寸由父容器决定。

### Fixed

- 🐛 **AI 不回消息死循环**：agent state 残留 `offline`（7-27 状态归一化迁移未生效）→ 0 工具 → 只能输出文字 → 被 system_reminder 反复弹。数据修复 + 工具兜底 + 重启。
- 🐛 **DM 500 系列**：`_maybe_trigger_dm_ai_reply` 缺 select 导入、`_normalize_attachments` 统一处理 Text/JSONB 列、撤回 DM 广播改动（导致 500）。
- 🐛 **/fs/list 500**：重复代码 copy-paste 导致 `list_files` 收到 list → 500；路径过滤导致文件列表为空。
- 🐛 **审计日志时区 500**：`datetime.now(timezone.utc)` 写入无时区列，改用 `datetime.utcnow()`。
- 🐛 **头像上传 NameError**：`upload_dir` 使用前定义（两处：上传端点 + agents.py）。
- 🐛 **群头像设置丢失**：读取 `avatar_mode` 字段，刷新后不丢失；保存后同步刷新侧边栏。
- 🐛 **ProfileCard/JSX 结构错误**：标签平衡修复（多处）。
- 🐛 **设置页保存按钮错位**、**消息时间跨日判定**（改用日历日比较）、**输入框最小高度 40px 对齐**。

---

## [v0.2.7] - 2026-07-27

### Added

- 🖼️ **Mermaid 错误 SVG 友好降级**：语法错误时不再显示 mermaid 原生错误图标，改为本地渲染的错误信息面板。
- 🗜️ **Mermaid 默认折叠**：聊天气泡中的图表默认折叠为「展开」按钮，点击后才渲染。设置页可关闭此行为。
- 🔔 **Mermaid 错误报告按钮**：渲染出错时「报告错误给AI」按钮一键将错误信息和代码作为用户消息发送给AI。
- ⏱️ **消息时间显示优化**：跨日历日但不足 24h 的消息正确显示为「昨天 HH:MM」而非「今天」。

### Changed

- 🧹 **MermaidBlock 全面重构**：`suppressErrorRendering: true` 替代手工正则/DOMParser 检测错误 SVG。模块级一次性初始化解决并发竞争。
- 💨 **紧凑模式加载无占位**：渲染完成前不占视觉空间，不阻塞滚动。加载态保持按钮可见消除闪动。
- 🎨 **全屏视图修复**：`extractCleanSvg` 从 iframe 提取纯 SVG 用于全屏叠加层，CSS transform（缩放/拖拽）恢复正常。
- 🌙 **表格暗色适配**：`DARK_VARS`/`LIGHT_VARS` CSS 变量从未实际挂载到 DOM，现通过 `useMemo` 转为 inline style 正确应用。
- 🎨 **深色模式气泡微调**：自己的消息气泡背景从纯紫 `#7C3AED` 调为暗灰紫 `#5a3a99`。
- 📝 **CHANGELOG 统一**：合并 v0.2.7 多个草案。

### Fixed

- 🐛 **Mermaid 错误 SVG 漏检**：`suppressErrorRendering` 被后续 `initialize()` 覆盖，改为模块级初始化。
- 🐛 **全屏缩放/拖拽失灵**：`useFullscreenPanZoom` hook 在 StrictMode 下引用异常，回滚到组件内联结构。
- 🐛 **隐藏容器 viewBox 压缩**：`<div hidden>` 的 `display:none` 导致 mermaid 算出 16×16 迷你 viewBox。
- 🐛 **CJK 文字被 foreignObject 裁剪**：mermaid 字体宽度测量对中文不足，注入 `overflow:visible` CSS 覆盖。
- 🐛 **overflow-hidden 裁掉滚动内容**：改用 `clip-path` 替代 `overflow-hidden` 实现圆角，保留子元素滚动。
- 🐛 **Vite Docker volume 缓存**：添加 `server.watch.usePolling: true`。
- 🐛 **handleSend 引用时序**：`const` TDZ 导致初始化前被访问，改为 ref 方案。
- 🐛 **消息时间跨日判定**：`Math.floor(elapsed/h)` 对昨天 21:33→今早 11:23 算出 0 天，改用日历日比较。

## [v0.2.6] - 2026-07-23~26

### Added

- 🟢 **在线状态追踪**：新增 `last_active_at` 列记录用户最后活动时间。WebSocket 连接时置 NULL（在线），断开时写入时间戳。
- 💓 **WebSocket 心跳检测**：30s ping 间隔，首次检测离线即记录时间戳而非等满 3 次超时，断开时写入更准确的时间。90s 无响应才真正关连接。
- 👤 **资料卡片增强**：显示在线/离线状态文本、最近在线时间、简介为空时的占位文字。卡片宽度加大，详情区一行展示注册时间 + 状态（flex-wrap 自适应）。
- 🔔 **窗口闪烁通知**：新消息时标题栏交替闪烁（带发送者昵称）、Favicon 右上角红点、桌面通知弹窗（需权限）。可复用设置页通知开关关闭。
- 🔧 **管理员密码重置**：`PUT /admin/users/{user_id}/reset-password` 端点，bcrypt 加密，记入审计日志。
- 📎 **附件注入 AI 上下文**：DM 消息中的附件名称（`[文件: xxx.png]`）注入 AI 消息内容，视觉模型还走图片 base64 注入。
- 🔔 **好友申请红点**：侧边栏（展开/折叠）和移动端底部导航收到好友申请时右上角红点，仅算收到的申请，30s 轮询。
- 🖼️ **Mermaid 全屏查看**：点击放大后全屏浮层，支持滚轮/按钮缩放（0.25x–10x）、鼠标拖拽平移、0.12s 平滑过渡、下载纯 SVG。
- 📖 **手册 SPA 导航**：Markdown 内部链接自动转为 React Router 导航，标题锚点自动生成 GitHub 风格 ID（递归提取纯文本），`queueMicrotask` 定位。CSS `scroll-behavior: smooth` 全局生效。
- ⚠️ **404 页 + ErrorBoundary**：Not Found 页面居中展示（三语），ErrorBoundary 包裹全局，崩溃时显示友好界面 + 刷新/重试 + 错误详情。
- 🔒 **手动链接 SPA 跳转**：`DocLink` 组件拦截 Markdown 内部链接，匹配路由后 SPA 导航，外部链接新窗口打开。
- 🖼️ **AI 头像上传统一**：AI 头像也走 WebP/GIF 魔数检测跳过裁剪，统一使用 `api.upload()` 获得友好 413 错误提示。
- 📉 **Mermaid 渲染失败友好降级**：显示具体错误信息 + 语法高亮原始代码回退，不再只报「渲染失败」。
- ✅ **Nginx 413 修复**：`copree.<your-domain>.conf` 补上 `client_max_body_size 20m`。
- 🔤 **Svg 中文不乱码**：Mermaid iframe base64 解码改用 `TextDecoder('utf-8')` 替代 `atob`。

### Changed

- 🏷️ **状态值规范化**：`online` → `active`，`offline` → `inactive`（DB 迁移 + 代码清理，前端 `STATE_DOT_COLORS` 移除旧值）。
- ⚡ **私信会话加载顺序**：新增 `?summary=true` 参数跳过消息加载及已读标记，切换会话时先渲染标题栏再加载消息。DMChatView 用 `key={sessionId}` 强制 ChatView 重置状态。
- 🎯 **初始滚位置消除闪烁**：`useLayoutEffect` 替代 `useEffect` + `setTimeout`，在浏览器 paint 前完成滚动定位（scrollIntoView / scrollTop）。
- 🔁 **登录不再管理在线状态**：WebSocket 连接/断开为在线状态的唯一数据源。
- 🕒 **最近在线时间时区回滚检测**：`formatRelativeTime` 用数值分钟比较解决 12h/24h 格式兼容。
- 📝 **翻译 key 新增**：`dm.lastActive`（最近在线）、`profileCard.bioEmpty` 三语支持。
- ⏱️ **回复标记已读**：发私信时自动标记对方未读消息为已读，不再依赖打开会话才标记。
- 🧹 **侧边栏预览去 HTML**：`make_preview` 用正则去除 `/<[^>]+>/g` 标签，不再显示 `<span class="text-gold">`。
- 📄 **空文本 + 附件可发送**：ChatInput 去掉 `if (!v) return` 拦截，由 ChatView `handleSend` 统一判断。
- 🔐 **登录页 401 不刷新**：API 客户端跳过 `/auth/login` 和 `/auth/register` 路径的 401 跳转，错误正常显示。
- 🎨 **手册主题兼容**：`.doc-content` Typography CSS 变量引用主题 `--tw-xxx` 变量，品牌色 `--tw-primary-400` / `--tw-accent-400` 变量化，改色只需改 CSS 变量。
- 🌙 **代码块暗色模式**：`highlight.js` 改用 `github-dark.css`，亮色语法高亮在浅灰和深灰背景上都清晰。

### Fixed

- 🐛 **切换会话标题栏滞后**：DMChatView 切换时先清 partner 再请求，配合 `summary=true` 秒更新。
- 🐛 **初始加载闪现聊天开头**：`useLayoutEffect` paint 前定位，用户无感知。
- 🐛 **资料卡片人类不显示状态**：人类也展示在线/离线文本，不再仅显示「人类」。
- 🐛 **状态入口过多**：标题栏圆点 + 详情区绿点 + 独立状态行 → 合并为详情区一行文字。
- 🐛 **ProfileCard 加载崩溃**：`isActive` 定义在 `profile useState` 之前导致 ReferenceError。
- 🐛 **附件按钮无响应**：`<input type="file" ref={fileInputRef}>` 元素在 ChatView 重构中丢失，补回。
- 🐛 **Favicon 红点被切**：红点圆心超出画布边界，改为与右上角保持一个半径距离。
- 🐛 **Favicon 红点不消失**：`badgeFavicon` Promise 在 stop 后 resolve 覆盖恢复，加 `flashLockRef` 检查。
- 🐛 **断联时消息被吃**：发送按钮加 `connected` 断联禁用 + `handleSend` 加 `if (!connected) return`。
- 🐛 **纯文件消息多一条分割线**：附件区 `border-t` 仅在消息有文字内容时显示。
- 🐛 **413 上传失败裸报错**：`uploadFile` 顶部拦截 413 返回友好提示「文件过大」，不再等 JSON 解析失败。
- 🐛 **Mermaid 下载的是 iframe 而非纯 SVG**：从 iframe `src` 的 base64 中正则提取纯 SVG 下载，不再依赖安全级别重渲染。
- 🐛 **会话标题栏闪旧数据**：ChatView 错过 `setMessages([])` 渲染间隙，用 `key={sessionId}` 强制 remount 根治。

### Added

- 🎨 **消息格式系统**：8 色彩色文字（`[gold]` 标签 + `<span class>` 兼容）、行内代码独立渲染、语法高亮（highlight.js）、行内代码背景连续

- 🔒 **审计日志系统**：企业级操作记录（成功/失败、IP、变更前后对比），SHA256 哈希链防篡改，CSV 导出，180 天自动清理

- 🚨 **中断消息注入**：AI 忙碌时用户新消息直接注入当前 `_tool_call_loop`，不另起 executor。DM 路由层拦截 + worker 群聊/私信统一处理。
- 🔧 **`file_edit` 新增 `delete_lines` 操作**：删除指定 N-M 行（1-indexed）。
- 📖 **`file_read` 新增 `start_line`/`end_line`**：分段读取文件，不传则读全文。
- 🛠️ **JSON 参数自动修复**：`_repair_json` 函数在 `json.loads` 失败时用正则提取 path + content，解决大文件 HTML 引号嵌套问题。
- 📝 **工具执行结果必回传**：有工具结果时强制继续循环让 LLM 看到，不再因 `finish_reason=stop` 提前退出。
- ⚡ **`max_tokens` 2048→16384**：LLM 输出配额提升，大文件不被截断。
- 🧠 **推理不可见提醒**：当前时间段提示 AI 必须调 `send_dm`/`send_gm` 发内容（除非不想发）。
- 🔍 **read_manual 工具**：AI 可查阅用户手册了解消息格式和平台规范，支持关键词搜索，用户询问格式问题时主动查询后回答。

### Changed

- 🪟 **输入框抽为独立 ChatInput 组件**：打字不触发对话界面重渲染。管理自身 value + @mention 状态，草稿自动保存/恢复，高度自动缩放最多 +3 行。拖拽基础高度与自动高度分开存储，可设负值，总高度不足时自动补偿。

- 📏 **群聊消息字数上限 200→5000**：`format_message` 默认截断改为 5000 字符。
- 🗂️ **`file_list` 路径处理**：`.` 和 `/` 视为根目录，不加 LIKE 过滤，查全部文件。
- ✏️ **`file_write` 覆盖更新 owner**：覆盖已有文件时重置 `owner_type`/`owner_id`，避免权限混乱。
- 💬 **工具描述补充**：`file_edit` 增加编辑前先读文件、从后往前的说明；`send_gm`/`send_dm` 增加 8 色彩色文字语法说明。
- 📱 **设置页保存按钮 sticky 底部**：`AgentSettingsModal` 和 `SettingsPage` 的保存/取消按钮固定在容器底部。

### Fixed

- 🐛 **拖拽缩放算法修正**：FilePreviewModal 改为实时鼠标位置算尺寸，不再累加增量导致越拖越歪。居中布局 2x 系数补偿 + 方向性计算过中心不反弹。
- 🐛 **行内代码被代码块分支处理**：react-markdown v10 不传 inline prop，remark 插件用 hName 重定向到独立组件。
- 🐛 **行内代码背景被切成一段段**：inline-block + max-w-full 实现连续圆角背景。
- 🐛 **工具报错不通知 AI**：`_pending_results` 不为空时不退出循环，LLM 收到结果后自主决策。
- 🐛 **JSON 解析失败死循环**：后台自动修复引号转义，AI 不再反复重试同一错误。
- 🐛 **`file_read` 报无权**：`file_write` 覆盖已有文件时未更新 `owner_id`。
- 🐛 **`file_list` 查不到文件**：`path="/"` 时 LIKE 条件 `/` 不匹配相对路径文件。
- 🐛 **设置页保存按钮错位**：`SettingsPage` 布局 flex row 导致按钮在内容区右侧。
- 🐛 **文件系统小文件可读写、大文件不可读**：`max_tokens` 不足导致 JSON 截断。

### Added

- 🎨 **气泡文本动态对比度检测**：运行时读取气泡 `background-color`，用 WCAG 算法自动算链接/代码/表格颜色。深色底→白字，浅色底→默认色。为未来自定义气泡颜色打下基础
- ⚙️ **上下文压缩阈值管理配置**：管理面板 → 对话日志 → 全局设置可调压缩百分比（5%-100%）
- 🕒 **闲置 12 小时自动压缩**：对话最后消息超 12 小时强制内联压缩，避免过期缓存浪费 token
- 🔍 **对话日志 AI 搜索**：AI 选择器支持按名称搜索，不再限于前 20 个
- 🎯 **Alembic 数据库迁移**：`compression_threshold` 列通过 Alembic 管理，`migration.py` 不再新增列
- 📦 **Tauri API 集中封装**：`utils/tauri.ts` 统一 `invoke`/`onKeyboardChange`/`getPlatform`，替代散落在各页面的内联 `__TAURI__` 检查
- 📱 **visualViewport 键盘检测**：移动端键盘弹出时精确滚动输入框，替代 400ms setTimeout hack
- 🦀 **Copree-Client 原生命令**：`io_bridge_call` 按 method 分发（getPlatform/getAppVersion）+ `get_status_bar_height` + `get_safe_area_insets` + `setup()`/`on_navigation()`
- 🧠 **思考状态内存追踪**：`_thinking_state` 字典跟踪所有对话的 AI 思考/输入中状态。新增 `GET /groups/{id}/activity` 和 `GET /dm/{id}/activity` 接口。前端进入对话时自动查询恢复活动指示器，切换页面不再丢失「思考中」显示

### Changed

- 📈 **消息加载上限 20 → 5000**：不再硬截断 AI 上下文，让压缩阈值自然控制保留量
- 📏 **内联压缩保留数 5 → 20**：AI 看到更多最近消息再截断
- ♻️ **SettingsPage/LocalModelPage Tauri 调用集中化**：7 处内联 `__TAURI__` + 动态 import 改为集中 `invoke`

### Fixed

- 🐛 **自家气泡 Markdown 链接/代码/表格深色底不可见**：白字 + 半透明底适配 `bg-primary-500`
- 🐛 **`get_compression_threshold` 未导入**：AI 回复报 `NameError`，不返回思考中状态
- 🐛 **对话日志查看器旧 AI 不可选**：后端加 `search` 参数，前端加搜索框
- 🐛 **context_compressor.py 死代码**：阈值从 6% 提到 60% 后永不触发，现改为从 DB 配置读取

## [v0.2.5] - 2026-07-11~22

### Added

- 🌤️ **Open-Meteo 天气工具**：AI 可查询实时天气和预报，免费且无需 API Key。
- 📺 **B站视频总结工具**：AI 可获取 B 站视频信息，需管理员配置 SESSDATA cookie。
- 🔧 **`file_read` 新增 `start_line`/`end_line` 参数**：支持分段读取文件。
- 🔧 **`file_edit` 行级编辑**：增量修改而非全量重写，节省大文件 token 消耗。

### Changed

- 🗂️ **`file_list` 路径处理**：`.` 和 `/` 视为根目录，不加 LIKE 过滤。
- ⚡ **`max_tokens` 2048→16384**：LLM 输出配额提升，大文件不再截断。

### Fixed

- 🧹 **状态栈去重**：修复 `state_stack_service` 逻辑，防止同一任务被重复 push。
- 🐛 **`file_list` 查不到文件**：`path="/"` 时 LIKE 条件不匹配相对路径文件。
- 🐛 **设置页保存按钮错位**：`SettingsPage` 布局修复。

## [v0.2.4] - 2026-07-11

### Added

- 🧩 **工具自动发现**：`ToolPlugin.__init_subclass__` 自动注册 + `_discover_tools()` 扫描 `tools/` 目录。新增工具只需在对应子目录创建 `.py` 文件，零修改现有代码。
- 🏷️ **技能类型注册表**：`SkillRegistry` 替代数据库 CHECK 约束，新增技能类型无需改数据库。
- 🔌 **服务插件注册中心**：`PluginRegistry` + `ServicePlugin` 替代 `admin.py` 中硬编码的 `PLUGIN_REGISTRY` dict 和 `if id != "browser"` 检查。
- 📡 **事件总线**：`EventBus` 单例发布/订阅架构，支持 `on/off/emit` 接口，错误隔离。预定义 `system.startup/shutdown`、`message.before/after_send`、`ai.before/after_response`、`ai.state_change`、`tool.after_execute` 事件类型。
- 📦 **Alembic 数据库迁移框架**：`alembic revision --autogenerate` 替代手动写 `ALTER TABLE`。启动自动执行 `alembic upgrade head`，迁移文件版本化管理可回滚。
- 🎲 **roll_dice 示例工具**：演示工具自动发现机制。
- 🌐 **Swagger UI 自定义**：语言下拉（中/英） + 快捷登录表单（用户名/密码 → 自动注入 token）。

### Changed

- ♻️ **技能引擎注册式分发**：`skill_engine.py` 的 `if/elif` 硬编码改为 `_ACTION_HANDLERS` / `_INJECT_HANDLERS` 字典调度。新增技能类型只需注册处理器函数。
- ♻️ **后端 Router 自动发现**：`routers/__init__.py` 自动扫描目录，`main.py` 从 16 行手动注册改为 `get_all_routers()` 循环。新增路由模块零修改。
- ♻️ **前端导航统一注册**：`navRegistry.ts` 作为导航项单一数据源，`Sidebar.tsx` 展开/折叠和 `MobileNav.tsx` 共用。新增导航项只需改一个文件。
- ♻️ **前端路由集中注册**：`pageRegistry.tsx` 统一管理所有页面路由定义，`App.tsx` 仅调用 `getPublicRoutes()` + `getProtectedRoutes()`。新增页面只需在 `pageRegistry.tsx` 加一行。

### Fixed

- 🐛 **DM 回复 BUG**：`_build_current_context` 忽略了 `is_dm` 参数，DM 中始终指示 AI 使用 `send_gm`。修复：DM 路径指示用 `send_dm`。
- 🐛 **ChatArea.tsx Group 接口缺字段**：`Group` 缺少 `is_paused` / `concurrent_ai_limit`。
- 🐛 **ChatView.tsx sender_state null 传入**：`state` prop 收到 `string | null` 导致类型错误。
- 🐛 **FilePreviewModal.tsx srcDoc null 传入**：`content` 为 null 时传给 `srcDoc`（需 `string | undefined`）。
- 🐛 **CreateAgentModal.tsx providers prop 缺失**：`DetailSettingsModal` 未接收 `providers` 参数。
- 🐛 **Layout.tsx 自定义事件类型**：`maintenance-mode` 自定义事件监听器类型不匹配。
- 🐛 **MaintenanceMsgEditor.tsx setMsg 缺字段**：预设应用时 `soft_once` 字段缺失。

### Removed

- 🧹 前端 3 处硬编码导航列表合并为 `navRegistry.ts` 单一数据源
- 🧹 `main.py` 16 行手动 router 注册改为自动发现

---

## [v0.2.3] - 2026-07-08

### Added

- 🎯 **@ 优先触发**：被 @ 提及的 AI 走独立并发通道（上限 1），不阻塞普通消息队列
- 🔧 **AI 自修改并发数**：AI 调用 `set_concurrency` 调整群并发上限，多 AI 同时设置取最小值，60s 自动恢复
- ⭐ **特别关心好友**：人类和 AI 均可设置特别关心，好友消息穿透 DND/屏蔽
- ⏸ **群暂停对话**：群管理可一键暂停/恢复 AI 触发
- 📏 **消息折叠展开**：`expand_message` 工具展开被截断的长消息
- 🧠 **提示词文件化管理**：`prompts/*.txt` 直接编辑，无需 Python 转义
- 🌳 **ChatChainManager v2**：红黑树 + 双向链表，O(log N + K) 查询唤醒

### Changed

- ⚡ **压缩阈值**：6% → 60%，接近窗口才触发
- 🔄 **压缩内联**：不再另起 API 调用，直接截断中间消息
- 📡 **上下文机制**：取最近 20 条消息（不按已读过滤），当前时间放在末尾保 cache
- 🏷 **Lazy tag**：AI 自修改暂存 `pending_system_prompt`，压缩时生效

### Fixed

- 🐛 群聊消息 `messages.append` 缺失导致 AI 收不到消息
- 🐛 `build_dm_messages` docstring 缺失闭合引号
- 🐛 暂停按钮 `setGroup` 未定义
- 🐛 @ 预选列表中文字符后不出现
- 🐛 ChatSidebar 事件监听器泄漏
- 🐛 迁移缩进污染、迁移顺序错误

### Removed

- 🔇 **Gate 3.5 系统硬拦**：尺时间判定改为 AI 自主决定（提示词引导 + 意愿分），删除系统强制过滤

## [v0.2.2] - 2026-07-04

### Added

- 🏭 **API 多供应商架构**：`provider_config` 从单对象→数组，支持同时配置多个 LLM 厂商（DeepSeek/OpenAI/Ollama/通义千问/Kimi/智谱/硅基流动），每个供应商独立设置 base_url/模型列表/深度推理支持。管理面板新增供应商增删改、设为默认。
- 🔗 **池 Key 关联供应商**：`api_key_pool` 新增 `provider_name` 列，池 Key 可关联供应商自动获得模型列表和 thinking 支持。未关联时按 api_base_url 自动匹配。
- 🎛️ **模型下拉框按供应商分组**：创建/编辑 AI 时聊天模型和工作模型用 `<optgroup>` 分组，★ 标记默认供应商。
- 🧠 **thinking 判定从全局改为按供应商**：`chat_completion` 新增 `provider_supports_thinking` 参数，非 DeepSeek 供应商也能正确发送 thinking。
- ♻️ **供应商配置纯函数化**：`utils/pure/provider_config.py`——查找/匹配/收集/增删全部零 IO。

### Changed

- 🔧 旧 `provider_config` 单对象自动迁移为数组：`_migrate_multi_provider` 包装为 `[{is_default:true,name,provider,...}]`，幂等。
- 🔧 `/agents/models` 端点新增 `providers` 字段，返回全部供应商配置和能力标记。
- 🔧 `_get_api_config` 返回值追加 `provider_info` 字典。
- 🔧 管理面板 `ProviderPresetSelector` 从单对象编辑重写为多供应商卡片式管理。

### Fixed

- 🐛 `agent_service.py` 遗漏 `from app.utils.result import Result` 导致容器启动 `NameError`。
- 🐛 后端容器无 `curl` 导致 healthcheck 失败——改用 Python `urllib.request`。
- 🐛 Docker 卷挂载触发 uvicorn `--reload` 误重启 + 前端启动早于后端就绪——加 `--reload-delay 3` + 后端 healthcheck + 前端 `condition: service_healthy`。

---

## [v0.2.1] - 2026-06-30

### Added

- 📚 **AI 状态栈**：`agents.state_stack` JSONB 列，4 个工具 push/pop/close/list 追踪跨任务上下文。end_turn 自动兜底 push。
- 🔕 **群 DND 增强**：@mention/@all/@everyone/@全体/群公告穿透免打扰。新增 `cancel_dnd` 工具。
- 🚪 **`enter_group` 工具**：验证成员资格→获取未读数→push 状态帧→返回预览。
- 📋 **TODO/PLAN/JOURNAL 自动联动**：push_state/pop_state 自动写入 workspace。
- ♻️ **Result Monad 集成**：`get_agent()`/`decode_access_token()`/`get_effective_config()` 返回 `Result[T,E]`。TypeScript 端 `safeParse<T>()`、`api.safe.*`。
- 🧩 **统一上下文**：所有会话统一标题格式（「在私信/群聊「名字」(id=X)中」），当前会话标题始终在最后。跨对话消息全部 `role: system`，边界清晰。
- ⚡ **上下文压缩**：旧消息稳定摘要化保 prompt cache 命中率。`context_compressor.py` 控制阈值（~8K tokens 触发）。
- 🏗️ **纯函数架构建立**：`utils/pure/` 目录——意愿评分、提示词构建、消息格式化、预设合并、状态栈全部零 IO 抽取。项目遵循「函数式核心 + 命令式外壳」模式。

### Fixed

- 🐛 **跨对话上下文死循环（历史重播）**：`_build_cross_conversation_context` 永久禁用（返回 `[]`），状态栈替代——经 A/B 对照验证，DeepSeek 在 `system→user/assistant→system` 交替结构中存在注意力失效。
- 🐛 **多群主错误数据**：迁移将非 owner 的 `role='owner'` 记录降为 member。
- 🐛 **纯文件消息被拦截**：发送纯文件（无文字）不再被 `content` 空检查拦截。

### Changed

- 🔧 状态栈摘要注入到系统提示词尾部（最大化 prompt cache 命中）。
- 🔧 闹钟/群聊/DM 三个路径 end_turn 时调用 `persist_last_task_as_state()`。
- 🔧 跨对话上下文生效条件：chat 档 + general/semi_general 不加载，resonance 及 custom 档共振 AI 加载。

---

## [v0.2.0] - 2026-06-28

### Added

- 🔧 **AI 设置界面二级重构**：设置面板拆分为「主设置」（12 个核心参数：名称/档位/AI类型/模型/温度/思考/工具轮次/延迟/他人对话/暂停）和「详细设置」（全部高级参数）。一键切换四个配置档位即时预览预设，所有参数可在此基础上微调。
- 🆕 **4 个新配置参数**：`auto_dnd_threshold`、`auto_dnd_duration`、`conversation_logs_limit`、`user_can_view_logs`。前后端全链路支持，创建和编辑均可设置。
- 🛠️ **tool_help 工具**：AI 可查询工具/CLI 详细用法（含 browser session 协议、17 子命令、典型流程）。`execute_command` 描述指向 `tool_help`。
- 🎨 **滑块日夜模式修复**：`input[type="range"]` 自定义轨道/拇指 CSS，深色模式轨道清晰可见。`ToggleField` 统一使用标准 `Toggle` 组件。
- 📧 **邮箱系统增强**：多 SMTP 配置容灾——支持配置多个发件服务器按优先级自动故障转移，一个不可用自动尝试下一个。管理员可增删改排序，独立测试每个配置的连通性。
- ✉️ **自定义邮件模板**：管理员可编辑验证码邮件的 HTML 模板（分中文/英文/日语，分注册/登录/换绑三种用途），支持 `{code}` `{from_name}` `{username}` 等变量占位符。一键重置为默认模板。
- 🔢 **OTP 验证码输入组件**：全新 `VerificationCodeInput` 组件——6 个独立数字框，自动聚焦跳转、Backspace 回退、粘贴分发。LoginPage / MePage / SettingsPage 三处统一替换，体验一致。
- 🇯🇵 **日语界面支持**：完整日语翻译（1415+ key），前端语言选择器全线支持。设置向导、设置页、管理员面板均可切换日语。时间格式化（相对时间 + 消息时间）日语本地化。
- 📄 **FilePreviewModal 富文本渲染**：`.md` 渲染为格式化 Markdown（react-markdown + GFM/数学公式/Mermaid），`.py/.c/.cpp/.json` 等代码文件语法高亮展示。
- 🧠 **AI 详情页结构化记忆展示**：新增子 Tab（结构化/向量），3 级可折叠目录树，向量记忆卡片支持展开、scope 彩色标签。

### Fixed

- 🐛 **跨对话上下文 role 混淆（DeepSeek 注意力失效）**：经 A/B 对照验证，DeepSeek 在 `system → user/assistant → system` 交替结构中存在注意力失效——API 收到完整数据，但模型会忽略夹在 system 消息之间的 `user`/`assistant` 消息（能见标题、不见内容）。修复方案：跨对话上下文（标题 + 内容）全部改用 `role: system`，仅当前会话保留 `user`/`assistant`。同时引入自我/他人显式区分——AI 自己的发言保留纯名字（如 `逍遥三号: ...`），他人发言附带 id（如 `清风无殇（id=12）: ...`），系统提示词加规则「带 id 的都是别人」。id 是数据库主键不可伪造，杜绝用户名注入冒充 AI 自己的攻击面。设计文档全量同步（AI对话链机制.md / 三空间模型.md / 记忆架构设计.md / 项目全景报告.md §7.5）。
- 🐛 **统一上下文修复**：`config_profile=custom` 的共振 AI 此前被 `_build_cross_conversation_context` 排除，修复后仅跳过 `chat` 档和 `general`/`semi_general` 类型，共振 AI 在任何档位下均加载跨对话上下文。
- 🔧 **set_status 工具注册**：补上 `tools/__init__.py` 中漏掉的 import。
- 🐛 **纯文件消息修复**：发送纯文件消息（无文字）不再被 `content` 空检查拦截。
- 🔒 **文件引用幂等**：`file_references` 表加 UNIQUE 约束 + CI 迁移清理重复记录，彻底解决 `MultipleResultsFound` 500 错误。
- 🧬 **Embedding 模型修正**：`deepseek-embed` 实际不存在，改为 `text-embedding-3-small` + 环境变量 `EMBEDDING_MODEL` 可配。AI 提示词更新，告知 `recall_memory` 可能不可用。

### Changed

- 🔧 **语言配置统一化**：新建 `frontend/src/i18n/languages.ts` 作为语言元数据的单一数据源（`Lang` 类型、`LANGUAGES` 数组、`isValidLang()` 校验、`getLangMeta()` 查询）。`I18nContext`、`SetupPage`、`SettingsPage`、`AdminPage`、`AuthContext`、`time.ts` 全部改为引用此中心文件。新增语言只需在此文件加一条记录。
- ♻️ **SMTP 配置数据迁移**：`system_settings.smtp_config` 从单 JSONB 对象改为 JSONB 数组（`_migrate_smtp_configs_array` 自动包装旧格式）。`get_auth_settings` 返回新增 `smtp_configs` 字段，旧 `smtp_config` 字段保留兼容。
- 📝 **对话链机制文档**：新增统一上下文章节（§2.4——生效条件表、上下文格式、与链交互规则）。
- 📝 **认知架构文档**：统一上下文规则表覆盖 `custom + resonance` 组合。

---

## [v0.1.8] - 2026-06-27

### Added

- 👤 **用户/AI 个人资料系统**：`agents` 表新增 `bio`（TEXT，AI 简介）+ `status_text`（VARCHAR 100，个性状态），`users` 表新增 `bio` + `status_text`。创建/编辑 AI 弹窗主表单直接填写简介和个性状态，无需进详细设置。用户可在「我的」→ 编辑资料弹窗中编辑自己的 bio 和状态文本。AI 可通过新增的 `set_status` 工具自主修改个性状态（中文≤10字，英文≤30字符，active/dnd/offline 均可用）。
- 🪪 **资料卡弹窗 ProfileCard**：全新 `GET /user/profile/{entity_type}/{entity_id}` 端点，返回聚合资料（头像、简介、状态、注册时间、AI 制作者、是否好友）。前端 ProfileCard 组件全面重写——展示真实头像（gradient 兜底）、bio 文字、状态文本（斜体强调色）、制作者信息。支持「加好友」按钮切换附言输入区，发送带验证消息的好友申请。
- 🔍 **搜索结果头像可点 + 加好友附言**：搜索结果中点击头像或名称区域 → 打开 ProfileCard 资料卡。搜索加好友改为内联消息输入（输入附言 → 确认发送），替代原来的一键无附言发送。
- 📱 **移动端添加好友改为内联弹窗**：ChatArea 中点击 `+` → 添加好友 → 弹出内联搜索弹窗（而非跳转到 `/friends` 路由）。
- 🔧 **工具系统新增 `set_status`**：第 30 个工具，AI 可自主设置个性状态文本，位于 self_config 段。
- 📝 **DM 头部头像可点**：私信头部对方头像点击 → 打开 ProfileCard 资料卡。
- 🎨 **状态文字颜色自定义**：用户可为个性状态选择预设颜色（8 种 + 默认无颜色）。WCAG 对比度自动检测——与父容器背景色计算对比度，不足 4.5:1 时自动追加文字辉光保证可读性。`users` 表新增 `status_color` 列。颜色选择器在 `/me` 编辑资料弹窗中。
- 📍 **状态文本多位置显示**：好友列表、私信（DM）列表、`/me` 个人资料卡三处均展示个性状态文本（含自定义颜色）。后端 `dm_service._get_partner_info` 和 `friend_service.list_friends` 同步返回 `status_text` + `status_color`。
- 🔐 **AI 对话权限控制系统**：`agents` 表新增 5 列——`allow_others_chat`（是否允许非主人触发对话）、`others_chat_mode`（允许时子模式：unlimited 始终允许 / quota 限额）、`others_chat_quota`（配额上限，默认 30 次）、`others_chat_used`（当前已使用次数，可重置）、`disallow_mode`（禁止时子模式：strict 严格禁止 / own_key 允许聊天者用自有 Key）。创建/编辑 AI 弹窗新增「对话权限」分区，所有参数始终可见可改。
- 💰 **差异化额度扣减规则**：通用/半通用 AI 在 DM 中由**聊天者**付费（谁用谁付），群聊中由**创建者**付费，共鸣型 AI 始终由创建者付费。扣减优先消耗 `platform_gifted_credit`（平台赠送），再消耗 `api_credit`。1 万 Token = 1 额度。
- ⚡ **DM 触发决策树**：非主人发 DM → 检查 `allow_others_chat` → 允许则检查配额（quota 模式超限自动 flip + 系统 DM 通知主人）→ 检查聊天者余额（不足则 WebSocket 弹窗）→ 禁止则检查 `disallow_mode`（strict 静默跳过 / own_key 用聊天者自有 Key）。全新端点 `POST /dm/continue-with-own-key` 处理用户确认使用自有 Key。
- 🪟 **余额不足弹窗 BalancePromptModal**：聊天者额度不足时，后端通过 WebSocket 推送 `balance_prompt` 消息 → 前端全局弹窗提示「余额不足，是否使用自有 API Key？」→ 同意后续用、不同意取消。`useWebSocket` Hook 通过 CustomEvent 分发。
- 🔄 **配额自动翻转 + 系统通知**：限额模式下 `others_chat_used` 达上限时自动将 `allow_others_chat` 翻转为 False，系统通过 DM 通知 AI 主人配额已用完。新增 `POST /agents/{id}/reset-others-chat-used` 端点供主人重置计数器。
- 📊 **前端对话权限 UI**：CreateAgentModal 和 AgentSettingsModal 新增「对话权限」Section，含 Toggle 开关、子模式单选、配额输入、使用计数 + 重置按钮。左边界颜色编码。17 个新增 i18n 翻译键（中英）。

### Changed

- ♻️ **个人资料编辑迁移到 /me**：bio 和 status_text 从设置页（`/settings`）移至 `/me` 编辑资料弹窗。设置页移除"个人资料"分区。所有个人资料编辑统一在 `/me` 完成。

### Fixed

- 🐛 **搜索 AI 重复显示**：AI 的 `User` 记录（`type="ai"`）也被用户搜索匹配，导致同一 AI 出现两次（一次标为 human，一次标为 AI）。修复：`search_service.py` 和 `friend_service.py` 的用户查询增加 `User.type == "human"` 过滤。
- 🐛 **移动端聊天列表右侧空隙**：`max-w-[90vw]` 约束导致侧边栏未填满屏幕宽度。修复：改用 `inset-0`（四边全填充）。
- 🐛 **AI 卡片底部按钮事件冒泡**：编辑/历史/状态/导出按钮的 `onClick` 事件冒泡到父级卡片 → 触发 `navigate` 跳转详情页，弹窗闪现后消失。修复：4 个按钮全部加 `e.stopPropagation()`。
- 🐛 **/agents 页 AI 卡片不显示已设头像**：头像区域硬编码 Bot 图标。修复：有 `avatar_url` 显示 `<img>`，无则默认图标。
- 🐛 **头像裁剪失效**：`AvatarCropModal` 的 `onMediaLoaded` 接收的是 `MediaSize` 尺寸对象而非 `HTMLImageElement`，`imageRef` 永远为 null 导致裁剪确认时回退发原图。
- 🐛 **人类用户资料卡注册时间旁显示机器人图标**：ProfileCard 注册时间行硬编码 `<Bot>` 图标。修复：`entityType === 'human'` 时显示 `<User>` 图标。

- 📤 **AI 文件发送工具 `send_file`**：AI 可从自己的文件空间（`file_write` 创建的文件）发送到群聊或私信。零拷贝引用已有 `FileMetadata`，三元权限匹配（`path + owner_type + owner_id`），不可跨 AI 读文件。群聊用 `agent_id`，私信用 `agent.user_id`。WebSocket 广播 + 触发其他 AI 回复。`CORE_IDENTITY` 系统提示词已更新告知 AI 有此能力。
- 🖼️ **文件预览弹窗增强**：图片预览支持缩放（+/- 按钮 + Ctrl+滚轮，0.5x-5x）、重置缩放。PDF 使用浏览器原生 `<iframe>` 内嵌预览。DOCX 通过 mammoth.js 客户端转 HTML 渲染（`dangerouslySetInnerHTML` + prose 样式）。文本类文件（txt/json/xml/yaml/sh/js 等）用 `<pre>` 语法高亮预览（≤2MB）。不可预览的自动触发下载。移动端全屏 + ArrowLeft 返回按钮；桌面端居中弹窗 800px max-w / 88vh max-h。所有预览均有下载按钮。
- 📎 **纯附件消息优化**：用户仅发文件不输入文字时不再自动附加 `(附件)` 字符串。侧边栏最后消息预览显示 `[文件]`（1 个文件）或 `[N个文件]`（多个）。`make_preview()` 函数统一处理 content + attachments 预览逻辑。
- 🔗 **文件去重系统**：三级比对（文件名 → 大小 → SHA-256 哈希），上传文件时自动检测。同一用户上传同名同内容文件时直接复用已有 `file_id`，不写物理磁盘。`FileMetadata` 新增 `content_hash` 列。`ai_write_file` 同步写入哈希。
- 📤 **文件转发系统**：新的 `ForwardFileModal` 组件——搜索 + 多选群聊和联系人（DM），一键将已有文件作为附件转发给多个目标，不重复上传。前端文件预览弹窗和"我的"存储页均有转发入口。
- 🔄 **转发引用自动追踪**：`create_message` 和 `send_dm_message` 发送含附件的消息时，若非文件 owner 发送则自动在 `file_references` 创建 `ref_type='forward'` 记录（幂等）。转发即计入转发者存储配额。
- 🏠 **FIFO 文件过户**：文件 owner 删除时，自动查找最早转发该文件的用户并将文件所有权转移过去。过户后原转发引用自动移除（已升级为 owner）。若无转发者接盘则标记为孤儿文件，进入宽限期。
- ⏳ **孤儿文件宽限期**：无人接盘的文件标记为 `owner_type='system'`，宽限期（默认 7 天，管理员可在 `system_settings.orphan_retention_days` 配置）后由后台 worker 物理清理。每小时检查一次。
- 🗑️ **转发文件释放**：转发者可从"我的"存储页点击释放按钮移除转发引用（`DELETE /fs/release/{file_id}`），仅删引用不删物理文件。存储空间即时返还。
- 📊 **存储页文件列表**："我的"存储概览区新增文件列表（含转发文件），自有文件悬停显示转发按钮，转发文件显示 `[转发]` 标签和释放按钮。存储配额计算含转发文件。`GET /fs/list?include_forwarded=true` 合并返回自有+转发文件。`/user/storage` 新增 `forwarded_files` / `forwarded_used` 字段。

### Changed

- 🔧 **群聊消息 REST 端点**：新 `POST /groups/{group_id}/messages` 支持通过 HTTP API 发送群聊消息（含附件），含 WebSocket 广播 + AI 触发器。与 DM 端点对称。
- 🔧 **DM 消息端点支持附件**：`POST /dm/{session_id}/messages` 现接受 `attachments` 字段并透传给 `send_dm_message`。

### Fixed

- 🐛 **`file_references.ref_type` CHECK 约束缺失 `forward`**：模型和数据库约束新增 `'forward'` 类型，init-db.sql 幂等迁移自动修复。

---

## [v0.1.7] - 2026-06-27

### Added

- 🧩 **工具系统插件化重构**：28 个工具从 `tool_registry.py`（~2300 行）迁移到 `backend/app/tools/<segment>/*.py` 独立文件。`ToolPlugin` 基类 + `ToolRegistry` 单例支持自动注册。新增工具只需创建文件并调用 `ToolRegistry.register()`，PR 即文件。
- 🎛️ **管理面板「工具与技能」Tab**：两个子页签——「工具注册表」按段/状态筛选查看全部 28 个工具及参数 Schema；「AI 技能管理」下拉选 AI → 查看/启用/禁用所有思维技能（delay_reply / inject_prompt 等）。3 个新管理 API：`GET /admin/tools`、`GET /admin/skills/agents/{id}`、`PUT /admin/skills/agents/{id}/{skill_id}`。
- ⏹️ **`end_turn` 工具**：AI 可主动结束回复轮次。`system_reminder` 提示「如需结束请调用 end_turn」。支持 `set_state` 参数在结束时切换在线状态。所有状态（active/dnd/offline）均可用。
- 🤝 **AI 合作者系统**：创建者可添加其他用户为合作者，自由选择编辑 / 删除 / 管理合作者三项权限。
- 📝 **系统提示词段顺序可编辑**：DB 持久化存储顺序，前后端上下箭头调整，`get_settings` 补齐遗漏字段。
- 🖼️ **AI 图片识别**：用户发送图片时，`build_messages` / `build_dm_messages` 自动将图片 base64 编码注入到消息的 `image_data` 字段（≤4MB），DeepSeek V4 Pro 多模态模型可直接理解图片内容。群聊和 DM 均支持。
- 🧠 **三空间认知模型**：将 AI 的思维分为三个独立空间——**思考空间**（reasoning_content，完全私有）、**对话空间**（send_message/send_dm，唯一交流通道）、**记忆空间**（store_memory/file_write/file_read，长期存储）。`CORE_IDENTITY` 系统提示词重写，AI 清楚知道"想"和"说"的边界。
- 📋 **JSON intent 轻量协议**：AI 的 `content` 字段设为 JSON 格式 `{"intent": "tool_calls"|"end_turn"|"no_action"}`。后端解析意图分发——`end_turn`/`no_action` 直接干净退出，不走 system_reminder，省一轮 API 调用。不依赖 `response_format` 强约束，跨平台兼容（DeepSeek/Anthropic/Gemini/Bedrock）。
- 📂 **文件系统记忆系统**：新建 `backend/app/services/memory_index.py`。AI 可在 `data/agents/{id}/memories/` 下用 `.md` 文件管理长期记忆。目录结构含 `private/`（所有 AI）、`shared/`（半通用+共振）、`cross/`（共振专属 symlink）。对话开始时自动扫描目录生成索引并注入系统提示词，AI 可看到自己的记忆目录树。`file_write` 写入 `memories/` 时自动返回"记忆已更新"通知。与现有向量记忆（`store_memory`/`recall_memory`）共存——模糊回忆用向量检索，深度查阅用 `file_read`。
- 🎛️ **记忆配置三参数**：Agent 表新增 `memory_load_mode`（index_only / index_plus_recent / index_plus_semantic）、`memory_recent_count`（0-50）、`memory_shared_scope`（private_only / private_plus_shared_by_user / private_plus_shared_all）。三档 config_profile 各有默认值（chat=仅索引、immersive=索引+最近3篇、digital_life=索引+语义），memory_shared_scope 由 ai_type 决定（general=仅私有、semi_general=按用户共享、resonance=全共享）。所有参数在创建/详情页可手动覆盖。
- 🖥️ **前端「文件记忆」高级设置**：创建 AI 详细设置弹窗和 Agent 详情页均新增「文件记忆」分区，含加载模式下拉、最近篇数加减、共享范围下拉。预设值随 config_profile 自动填充。中英文 17 个翻译 key 已添加。
- 📖 **认知架构设计文档**：新建 `docs/AI认知架构三空间模型.md`，含三空间模型、JSON intent 协议流程（Mermaid 图）、文件记忆设计、九宫格配置矩阵、六段提示词布局、API 变更清单。
- 📏 **可拖拽侧边栏 Hook**：新建 `frontend/src/hooks/useResizableSidebar.ts`。200-500px 范围拖拽，localStorage 持久化宽度。ChatArea、SettingsPage 均复用同一 Hook。
- 🏷️ **设置页面桌面端分类导航**：5 个分组（账户/API/偏好/行为/外观），IntersectionObserver 滚动高亮。侧边栏支持拖拽调整宽度。
- 🛡️ **系统用户与错误通知**：迁移创建 `id=0` 的 system 用户，API 报错时通过 DM 通知 AI 持有者。系统消息玫瑰色气泡 + ShieldAlert 图标，引导用户配置全局 API Key。
- 🎨 **管理面板桌面端竖标签布局**：左侧 `w-56` 侧边栏按分类分组（核心管理/运维分析/系统配置），右侧内容区独立滚动。

### Changed

- 🔧 **`system_reminder` 精简**：`every_time` 上限从 999 降至 3，配合 `end_turn` 工具优雅退出，防止绕过 `max_tool_rounds` 无限循环。
- 🎨 **Toggle 开关统一**：18 个开关/滑块统一为 `bg-mint-400` + 白色圆点单一风格。
- 🔧 **system_reminder 降级为兜底**：原逻辑"有文字无 tool_calls → 必定触发提醒"。新逻辑插入 JSON intent 解析——AI 正确返回 `end_turn`/`no_action` 时直接退出，不触发 system_reminder。system_reminder 仅在 JSON 解析失败或无 intent 字段时作为兜底。正常情况下省一轮 API 调用。
- 🔧 **沙箱错误消息增强**：路径穿越错误消息现在告知 AI 其文件空间根目录位置（`/app/data/agents/{id}/`），帮助 AI 自我纠正。
- 🔧 **前端代码质量优化**：`ChatView.tsx` 提取 `isMessageForThisConversation`/`removeFromMap`/`addToMap` 三个模块级 helper，消除 7 处重复的 Map 操作和 3 处重复的 `belongsToHere` 检查。`ChatSidebar.tsx` 用 useRef 持有活跃对话 ID 避免事件监听器随对话切换而重建，500ms debounce 防 chat-refresh 请求风暴。所有硬编码 `'chat-refresh'` 字符串替换为 `CHAT_REFRESH_EVENT` 常量。
- 🔧 **序列化器共享**：`federation_ws.py` 的 DM 消息字典构建改用共享的 `serialize_message()`，消除重复代码。
- 🎨 **头像渐变重设计**：降低饱和度改用 teal 色系，镜像渐变方向（自己↗、他人↘），轻阴影替代球状效果。系统消息头像 rose 色 + Shield 图标。
- 📐 **管理/设置侧边栏间距收紧**：导航项 `py-1.5`、`text-[13px]`，分类标题 `h-8`、`text-[11px]`，纯文字紧凑风格。
- 🧹 **翻译键去重清理**：删除 5 组 10 个从未引用的重复废键（`continueEditing2`/`saveButton`/`perAgentConfig`/`agentApiSave`/`instantApply`），保留语义正确的在用键。

### Fixed

- 🐛 **`data_dir` 路径修正**：`config.data_dir` 改为 `@property` 返回固定值 `/app/data`，防止 pydantic-settings 自动覆盖为宿主机路径导致上传文件写到挂载卷外、容器重建后丢失。兼容所有部署环境（本地/Docker/NAS）。
- 🐛 **附件下载 500**：`file_references.referrer_type` CHECK 约束缺少 `'human'`，用户下载自己上传的文件时报 500。已在模型、`init-db.sql`、migration 三处修复。
- 🐛 **用量图表修复**：Legend 溢出 `flexWrap` 换行、表头 `whitespace-nowrap` 截断、Y 轴标签三级分档 `tickFormatter`。
- 🐛 **ToolsSkillsTab 加载失败**：`api.get` 返回直接 JSON（非 `{data:...}` 包装），修正 `r.data.xxx` → `r.xxx`。
- 🐛 **AI 自修改权限拒绝**：AI 调用 `update_self_config` 时 `operator_id`（agent_id）与 `owner_id`（user_id）永远不匹配，始终被拒绝。修复：检测 `operator_id == agent_id` → 跳过 owner/collaborator 检查，仅检查 `is_ai_editable` 开关。
- 🐛 **好友申请/自动回应开关无效**：后端 `update_agent_config` 接收了 `allow_friend_requests`、`auto_respond_friend_request`、`is_ai_editable` 字段但未处理，前端开关点了无反应。修复：显式赋值到 ORM 对象。
- 🐛 **AI 编辑弹窗设置过少**：编辑 AI 弹窗缺少工具调用轮次、闹钟上限、强制闹钟、允许自修改 4 个设置项。前端 `EditAgentModal` 已补全。
- 🐛 **合作者开关无文本标签**：合作者列表三个 Toggle（编辑/删除/管理合作者）无文字说明。已加标签。
- 🐛 **聊天列表排序乱跳、消息不置顶、未读残留**：新消息到达时列表重新排序导致当前选中项被挤走、最新消息不出现在顶部、标记已读后未读计数未清零。修复了排序逻辑和刷新时机。
- 🐛 **ChatSidebar chat-refresh 请求风暴**：高频 WebSocket 消息触发大量 `loadGroups()` + `loadDMSessions()` API 请求。修复：useRef 持有活跃对话 ID，事件监听器只注册一次（`[]` 依赖），500ms debounce 防抖。
- 🐛 **Storage 空间显示 0 B / 0 文件**：两个独立 bug——DB 查询 `FM.owner_id == ag.user_id` 应为 `FM.owner_id == agent_id`；物理扫描 `data_dir/agents/{agent_id}/` 路径错误（文件存于 `data_dir/` 扁平目录）。已修正为纯 DB 查询。
- 🐛 **useWebSocket 死代码**：移除 `unreadSummary` 状态、`setUnreadSummary` 处理器、`clearSummary` 回调及其返回导出（~15 行），无任何组件消费。
- 🐛 **message_serializer 冗余代码**：移除未使用的 `import logging`、`created_at` 从 `getattr` 改为直接属性访问、补充 `sender_avatar_url` 优先级注释。
- 🐛 **`/user/stats` 500 错误**：`from app.models.friend import Friend` 模块不存在。修正为 `app.models.friendship.Friendship`，移除不存在的 `status` 过滤。
- 🐛 **系统错误通知不触发**：sender FK 约束失败、DM session partner 错误、username 伪装风险三连修。最终方案：硬编码 `SYSTEM_USER_ID=0` + migration 确保 id=0 存在 + `setval` 保留序列。
- 🐛 **Markdown inline code/数学溢出**：块级 code 用 `whitespace-pre overflow-x-auto` 横向滚动；inline code 加 `break-all` 防长 token 溢出；行内 KaTeX 加 `max-w-full overflow-x-auto` 防长公式溢出。补充日夜模式文字颜色和 `[&_.katex]:text-inherit`。
- 🐛 **Markdown 链接白色不可见**：浅色模式下链接无颜色。补 `[&_a]:text-primary-500 dark:[&_a]:text-primary-400 [&_a]:underline`。

---

## [v0.1.6] - 2026-06-24

### Added

- 🔗 **联邦 v0.1.1 架构升级**：双层 ID 体系（`instance_subnet_id` UUID + `instance_public_id` ULID），`FederatedEntity` 统一表替代旧的 `FederationGroupShare` + `FederationDMShare`，支持 group/dm/user/agent 四种实体类型。`federated_id` 格式 `{实例代号}:{类型}:{本地ID}`（如 `datongai:g:42`），接收端根据发送 peer 自动拼接前缀。
- 🔗 **联邦头像同步**：`entity_announce` 消息携带 `avatar_url`，`profile_sync` 机制不再过滤 `avatar_url` 字段，用户/AI 头像上传时自动入队 `PendingProfileUpdate` 推送到联邦对等端。
- 🔗 **实例代号自动采纳**：握手时对方分配的 `assigned_name` 自动设为本机 `instance_config.display_name`，对方不设代号时显示名称留空也不报错。
- 🔗 **实例代号唯一性校验**：设置 `display_name` 时检查是否与已有 `FederationPeer.display_name` 冲突，防止多实例代号重复。
- 🔗 **联邦协议精简**：所有联邦消息只传 `entity_type` + `local_id`（不再传带前缀的 `federated_id`），接收端根据发送 peer 的 `display_name` 自动拼接完整 ID。向下兼容旧格式。
- 🔗 **per-group 联邦共享控制**：群主和 AI 制作者可按群、按对等端控制联邦共享。三个端点：`GET /groups/{id}/federation/peers`（查看状态）、`POST .../share`（共享）、`POST .../unshare`（取消）。`remote_url` 改为可选（只需一端有公网地址即可建立双向通道）。
- 📝 **开发者与管理手册**：新增独立文档，含架构速览、部署指南、排错手册、WebSocket 通信专项。管理员面板头部添加「管理手册」链接。
- 📝 **用户手册精简**：移除管理/部署内容，聚焦终端用户日常使用。由浅入深重写。
- 🎛️ **系统提示词管理**：管理员可在管理面板查看和编辑发给 AI 的 6 段系统提示词（core_identity / personality / protocol / tools / current_context / injected_skills）。支持行内编辑、预览拼接全文、恢复默认。后端 `system_prompt_overrides` JSONB 存储覆盖值，`_load_prompt_overrides` 在构建提示词时注入。
- 🖼️ **联邦头像持久化**：`messages` 表新增 `sender_avatar_url` 列，联邦消息落库时保存头像 URL。历史消息 REST API 加载时优先读 DB 中的 `sender_avatar_url`，避免联邦对等端离线后头像裂图。WebSocket `avatar_updated` 广播修复已渲染的消息头像。
- 📄 **手册双路由**：`/manual`（用户手册）和 `/manual/admin`（管理与开发者手册）使用统一的 `ManualPage` 组件渲染，管理员面板手册链接改为 `<Link to>` 内部导航。
- 📊 **用量图表增强**：UsagePage 新增 `ComposedChart` 时间轴曲线图（Area 总 Token + Line 缓存命中率右轴），按日期展示趋势。图表术语 "Prompt"/"Completion" 已 i18n 化。
- 🔘 **Toggle 开关统一**：新建 `components/Toggle.tsx`，所有 18 个开关/滑块统一为 `bg-mint-400` 轨道 + 白色圆点的单一风格。覆盖 AdminPage、AgentsPage、AgentDetailPage、SettingsPage、GroupSettingsPanel、ConversationLogTab、CreateAgentModal。

### Changed

- 📝 **联邦文档澄清**：明确联邦通信为服务端之间直连（非 P2P），客户端只连自己的实例。
- 💭 **「思考中」状态显示**：返回 token 时显示「思考中」，调用 `send_message` 才切换到「打字中」。思考中最小显示 1.5s 避免闪烁。

### Fixed

- 🐛 **联邦出站覆盖入站**：出站连接失败（或因入站已通而不需要出站）时错误地将 DB 状态覆写为 `failed`/`disconnected`，掩盖了已成功的入站连接。修复：出站失败后再次检查入站状态，不覆盖已通的连接。
- 🐛 **联邦重连循环失效**：`_close_peer_connection` 只清理内存状态不更新 DB，导致 DB 仍为 `connected` → 重连循环永远不触发。修复：添加 DB 更新 + `PeerConnection` 新增 `peer_id` 字段。
- 🐛 **空 URL 自动拼接 `/federation/ws`**：前端 4 处 `onChange` 在 host 为空时仍构造 `wss:///federation/ws`（InvalidURI）。修复：host 为空时整体留空。
- 🐛 **空 URL 点「连接」报 500**：`connect_to_peer` 对 4 种非错误情况（空 URL / 正在连接中 / 已入站连接）都返回 `False`，router 层一刀切抛 500。修复：router 层分类处理，返回 200 + 具体说明。
- 🐛 **群联邦共享开关失效**：share/unshare 的 403/400/422 错误码未区分 + `display_name` 为空导致创建失败。修复：错误分类返回 + 管理员豁免 + 前端 `alert()` 显示错误详情。
- 🐛 **DetailSettingsModal 缺 prop 崩溃**：创建 AI 详细设置弹窗缺少必要 prop 导致 React 崩溃。已修复。
- 🐛 **管理面板英文 Tab 补齐**：部分管理面板 Tab 在英文界面下仍显示中文标签。已补齐 i18n 翻译。
- 🐛 **GroupSettingsPanel 生产构建崩溃**：`FederationShareSection` 接收 `t` prop 与父组件 `useT()` 在 minify 后变量名冲突（`t2 is not a function`）。子组件改用自身 `useT()`。
- 🐛 **preset 键名不匹配**：`preset.digital_lifeName/Desc` 驼峰命名与翻译字典下划线 key 不匹配，导致数字生命档名称/描述不显示。
- 🐛 **缓存命中率公式修正**：`cached/(cached+total)` → `cached/total`。修复 UsagePage 和 MePage 两处。
- 🐛 **存储空间显示 0KB**：`data_dir` 路径修正，个人和 AI 的存储空间计算正确指向 `agents/{id}` 目录。
- 🐛 **用户手册内链 404**：`./管理与开发者手册.md` 相对路径改为绝对路径 `/docs/管理与开发者手册.md`。

---

## [v0.1.5] - 2026-06-21

### Added

- 🔑 **API Key 池管理**：管理员可通过「API 库」tab 管理系统级共享 API Key。支持添加/删除/启用禁用/优先级排序。Key 使用 Fernet 加密存储，添加后仅显示密文后四位（脱敏），管理员无法查看明文。用户兑换 API 池额度后自动从池中分配最优 Key。
- 💰 **额度消耗系统**：实现四层 API Key 解析优先链（Agent 自有 → 池 Key 绑定 → 自动选池 → 用户自有）。使用池 Key 时按 `total_tokens / 10000` 自动扣除 `users.api_credit`（最低 0.01 credit/次），自有 Key 时不扣。扣除通过 `quota_service.py` 的 `deduct_credit()` 完成，含 `SELECT FOR UPDATE` 防并发竞争。
- 📊 **用户额度状态端点**：`GET /user/credit-status` 返回剩余额度、估算 Token 数（api_credit × 10000）、月度消耗、绑定的池 Key 名。`GET /auth/me` 新增 `assigned_pool_key_name` 字段。
- 🏷️ **兑换码增强**：管理员生成兑换码新增「备注」（保密，仅管理员可见）、「单码最大用量」、「API 池额度」字段。兑换码列表显示备注、API 池标记（琥珀色「池」徽章）、创建时间。
- 📋 **API 用量日志**：新增 `api_usage_log` 表，记录每次 LLM 调用的 user_id/agent_id/pool_key_id/tokens_used/credit_spent/model，用于审计和用量统计。
- 🌐 **前端全量 i18n 国际化**：~700 翻译键值（中/英双字典），28 个前端文件全面替换硬编码中文字符串为 `t()` 调用。覆盖登录页、管理员面板（4 个 Tab）、AI 管理、好友、设置、用量、群聊/私信设置、弹窗等全部 UI。翻译键按模块组织（`admin.*`、`agents.*`、`friends.*`、`settings.*` 等）。
- 🌐 **全局默认语言**：`system_settings` 单行表（id=1），`default_language` 默认 `"en"`（英文）。管理员 `/admin?tab=system` 可切换全局默认语言。未登录时登录页自动获取并缓存全局语言偏好。新用户注册时从全局设置继承初始语言。
- 🧙 **新用户初始化向导**：`users.setup_completed` 字段。新用户注册后强制跳转 `/setup` 两步向导（第 1 步选语言 → 第 2 步确认完成）。语言选择时界面即时预览切换。`ProtectedLayout` 路由守卫拦截。现有用户自动标记为已完成。
- 🔧 `translations.ts` 扁平字典结构修复：`getTranslation()` 原用 `path.split('.')` 深度遍历但字典是扁平 key（`'nav.chat'`），导致所有 `t()` 调用返回原始 key。改为直接 `dict[path]` 查找。
- 📖 **用户手册独立页面**：新建 `ManualPage` 组件（`react-markdown` + `remark-gfm` + `@tailwindcss/typography` 渲染），路由 `/manual`。侧边栏改用 `NavLink`（无出站图标），版本与部署代码一致。
- 🛡️ **外部链接安全弹窗**：新建 `ExternalLinkSafe` 组件。点击外部链接弹出确认弹窗（「即将离开本站 → 目标 URL → 确认前往/取消」），防止无意识跳转。FederationTab 的 GitHub 链接已接入。
- 📐 **MePage 标题**：添加页面标题 `{t('me.title')}`（"我的"/"Me"），设置入口从三行简化为单行「设置」链接。
- ⚡ **API Key 池并发管理**：`ApiKeyConcurrencyManager` 单例（纯内存 + `asyncio.Lock`），追踪每个池 Key 的实时飞行中请求数。`acquire()` 检查并发上限（pro=500, flash=2500，可配），超限自动换 Key。`get_least_loaded()` 选负载最低且未冷却的 Key。429 自动 `mark_rate_limited()` 冷却 60s。
- 🔄 **API 可重试错误分类处理**：`llm_service.py` 中 `chat_completion()` 区分四类错误——429 `RateLimitError`（换 Key 重试）、500/503 `ServerError`（同 Key 等待重试，最多 2 次，间隔递增 2s/3s）、402/401 `KeyFatalError`（通知管理员 + 跳过该 Key 换下一个）、400/422 不重试。`_tool_call_loop` 外层 Key 切换重试（最多换 3 次），内层同 Key 重试。失败不计入 token 消耗。
- 📊 **管理员 API Key 池仪表板**：`GET /admin/api-key-pool/{id}/stats?days=30` 返回单 Key 统计（总 tokens、请求数、429/500/503 错误次数、日均消耗、按天聚合时序数据）。`GET /admin/api-key-pool/stats/summary` 返回全部 Key 汇总对比。前端 `KeyStatsModal` 含概览卡片 + recharts 折线图（Token 趋势）+ 饼图（pro/flash 分布）+ 错误统计表。
- 🎁 **平台赠送额度系统**：`system_settings.default_platform_credit` 全局默认值 + `users.platform_gifted_credit` 独立存储（与兑换码额度 `api_credit` 完全分离）。新用户注册自动继承全局默认。管理员修改全局值时计算 delta 批量更新所有用户。扣费优先级：先扣 `platform_gifted_credit`，再扣 `api_credit`。单次超支允许过界（不中断 AI 回复），`api_credit` 变负后前端标红。管理员调低导致的负数前端显示 0。

### Changed

- 🔄 **API Key 解析链升级**：`_get_api_config` 从二层（Agent→User）升级为四层优先链。DM 触发器中内联的 API 解析代码替换为统一调用 `_get_api_config`，消除重复逻辑。
- 🔄 **前端额度展示增强**：Sidebar 非管理员显示「额度 + 余额」双数字；MePage「通用额度」卡片显示估算 Token 数和池 Key 来源；AdminPage 新增「API 库」tab。
- 🔄 **群聊路由对称化**：群聊路由 `/chat/:groupId` → `/chat/gm/:groupId`（GM=Group Message），与私信 `/chat/dm/:sessionId`（DM=Direct Message）形成同级对称结构。涉及 App.tsx、ChatSidebar.tsx、ChatArea.tsx。
- 🔄 **用户手册本地化**：`MANUAL_URL` 从 GitHub 链接改为本地 React 页面 `/manual`（`ManualPage` 组件渲染 `docs/用户手册.md`），版本始终与部署代码匹配。
- 🔄 Sidebar 手册链接从 `<a target="_blank">` 改为 `<NavLink>`，移除出站图标
- 🔄 MePage/UsagePage `fmtTokenNum` 调用全部传入 `lang` 参数，英文界面显示 K/M 而非 万

### Fixed

- 🐛 修复聊天页返回路由不更新（移动端 ArrowLeft 未调用 `navigate('/chat')`）
- 🐛 修复兑换码生成 500 错误（`datetime.now(timezone.utc)` 带时区与 PostgreSQL `TIMESTAMP WITHOUT TIME ZONE` 不兼容，三处加 `.replace(tzinfo=None)`）
- 🐛 修复 i18n 全线失效：`getTranslation()` 深度遍历 bug 导致所有 `t()` 返回原始 key；`I18nProvider` 提到 `main.tsx` 覆盖登录页
- 🐛 修复 `friendship.py` schema 缺少 `avatar_url`/`auto_respond_friend_request` 字段导致前端头像不显示
- 🐛 修复 `ChatSidebar` 缺少 `useT` 导入导致 `t is not defined`
- 🐛 修复 AdminPage 崩溃：i18n 替换时 `useSearchParams()` hook 调用被误删，`searchParams is not defined`
- 🐛 修复时区偏移：后端 `DateTime` 列无 `timezone=True`，Pydantic 序列化为 naive UTC 字符串，前端 `new Date()` 将其误判为本地时间导致消息时间晚 8 小时。`time.ts` 新增 `parseServerDate()` 辅助函数对无时区标记字符串追加 `Z`
- 🐛 修复聊天消息气泡无折行控制：长 URL/长英文单词溢出。`MessageBubble.tsx` 气泡容器加 `break-words`
- 🐛 修复输入框内容丢失：页面刷新/崩溃/掉线后输入内容消失。新增草稿自动缓存（500ms 防抖写 localStorage），切换对话时保存/恢复，发送成功后自动清除
- 🐛 修复 GroupSettingsPanel 生产构建崩溃：`FederationShareSection` 接收 `t` prop 与父组件 `useT()` 产生 minify 变量名冲突（`t2 is not a function`）。子组件改用自身 `useT()` 而非接收 prop
- 🐛 修复 DM Offline 状态显示绿色：私信头部在线状态文字硬编码 `text-mint-400`。改为动态映射 active=绿/dnd=红/offline=灰
- 🐛 修复英文界面仍有多处中文：AdminPage 备份下载失败 `'下载失败'` 改用 i18n；Token 格式化 `fmtTokenNum` 新增 `lang` 参数（zh=万，en=K/M）；AgentsPage `stateLabels` 改用翻译键
- 🐛 修复 Agent 卡片按钮溢出：Edit/History/Status/Export 按钮在小屏上撑出卡片，加 `flex-wrap`
- 🐛 修复消息 Markdown 链接不渲染：用户消息使用纯文本 `<span>`，导致 `[文字](URL)` 不显示为可点击链接。改为统一使用 `<Markdown>` 渲染
- 🐛 修复消息纯文本换行不生效：添加 `remark-breaks` 插件，单回车自动转为 `<br>`
- 🐛 修复公式/代码块/长链接溢出：消息气泡新增 `overflow-x-auto` 规则覆盖 `.katex-display`/`pre`/`table`/`img`/`a`

### Backend API

- `GET /admin/api-key-pool` — 列出所有池 Key（脱敏）
- `POST /admin/api-key-pool` — 添加池 Key（Fernet 加密存储）
- `PUT /admin/api-key-pool/{id}` — 更新池 Key 配置
- `DELETE /admin/api-key-pool/{id}` — 删除池 Key
- `GET /user/credit-status` — 用户额度状态摘要
- `POST /admin/redemption-codes` — 请求体新增 `note`/`max_usage`/`is_api_pool`
- `GET /system/settings` — 获取全局系统设置（公开端点）
- `PUT /admin/system-settings` — 管理员修改全局系统设置
- `POST /auth/setup` — 新用户完成初始化设置（设置语言 + 标记完成）

---

## [v0.1.4] - 2026-06-21

### Added

- 📊 **API 用量仪表盘**：用户端「我的」→ API 用量概览 + 详情页（recharts 堆叠柱状图）。显示总 Token、调用次数、缓存命中率、思考 Token，按 AI 分组明细表，支持 7/30/60/90 天日期范围切换。图表自动适配日夜模式。
- 🛡️ **管理员用量分析面板**：AdminPage 新增「用量分析」tab。全站 token 消耗总览 + 按用户展开查看各 AI 明细 + 每日 AreaChart 趋势图。
- 🧠 **Token 追踪增强**：`token_usage` JSONB 新增 `reasoning_tokens`（DeepSeek 思考 token）和 `cached_tokens`（prompt cache 命中 token）。`llm_service.py` 自动从 API 响应提取，`ai_response_worker.py` 跨轮累积。
- 👤 **「我的」页面**：底部导航「设置」→「我的」。个人资料卡（头像、好友数、上线天数、额度概览）+ 我的 AI 横向卡片 + API 用量概览 + 兑换码输入 + 设置/管理入口 + 退出登录。支持编辑用户名/密码。
- 🎛️ **AgentDetailPage 工具轮次编辑**：「模型配置」新增「工具调用 & 闹钟」子区。`max_tool_rounds`/`alarm_max_tool_rounds`/`max_alarms` 支持 ± 按钮 inline 编辑，`force_alarm_on_end` 开关切换。调用 `PUT /agents/{id}/config` 即时生效。
- 🏷️ **兑换码 4 种类型**：新增 `agent_bundle`（AI 包断额度，创建时一次付清该 AI 全免）和 `file_quota`（文件存储配额 MB）。原有 `ai_quota` 和 `api_credit`（1 余额=1 万 token pay-as-you-go）。`users` 表新增 `agent_bundle_credit` + `file_quota_mb` 列。

### Changed

- 🔄 **导航重构**：底部导航栏和侧边栏「设置」→「我的」（`/me`），原设置页保留可继续访问。`/me` 整合设置入口、管理入口。
- 🔄 **用户信息扩展**：`get_user_info` 返回 `agent_bundle_credit`、`file_quota_mb`、`avatar_url`、`created_at`。AdminPage 用户列表同步展示。

### Fixed

- 🐛 **修复聊天页返回路由不更新**：移动端从 `/chat/2` 点击返回箭头，界面回到列表但 URL 停留在 `/chat/2`。根因：`ChatArea.tsx` 移动端返回按钮只打开侧边栏覆盖层（`setMobileSidebarOpen(true)`），未调用 `navigate('/chat')`。改为导航到 `/chat`，URL 与界面同步。
- 🐛 **修复兑换码生成 500 错误**：`POST /admin/redemption-codes` 报 `Internal Server Error`。根因：`datetime.now(timezone.utc)` 返回带时区的 datetime，但 `redemption_codes.expires_at` 列是 `TIMESTAMP WITHOUT TIME ZONE`，asyncpg 无法将 offset-aware 转换为 offset-naive 导致 `DataError`。修复：三处加上 `.replace(tzinfo=None)`（`admin.py:378` 生成码、`user.py:87` 过期比较、`user.py:112` 标记已使用）。超 60% 的代码已使用 `.replace(tzinfo=None)` 正确模式，此三处为遗留遗漏。

### Backend API

- `GET /conversation-log/usage/overview?days=30` — 用户所有 AI token 汇总
- `GET /conversation-log/usage/agents/{id}/daily?days=30` — 单 AI 每日分布
- `GET /admin/usage/global?days=30` — 全站 token 总览
- `GET /admin/usage/by-user?days=30` — 按用户分组明细
- `GET /admin/usage/agents/{id}/daily?days=30` — 管理员查单 AI 分布
- 聚合查询基于 PostgreSQL JSONB `->>` 操作符，`ai_conversation_logs.token_usage` 字段

---

## [v0.1.3] - 2026-06-20

### Added

- 🗑️ **好友机制完整删除**：`friendships`/`friendship_requests` 表重命名归档（安全回滚），`send_friend_request` 工具定义+handler+白名单全部移除。`search_entities()` 提取为独立的 `search_service.py` + `routers/search.py`。DM 不再需要先加好友——`send_dm` 可直接向任何人发送私信。前端删除 FriendsPage、FriendList、FriendRequestBadge 三个组件，搜索器中「加好友」改为「发私信」直接进入 DM 对话，ProfileCard 重写为 DM 入口，InviteMemberModal 从好友列表改为搜索邀请。移动端底部导航从 4 栏改为 3 栏（聊天 | AI | 设置）。

- 🏗️ **三种 AI 类型架构**：`agents.ai_type` 列（`general`=通用 | `semi_general`=半通用 | `resonance`=共振，默认 `resonance`）。通用 AI 每人独立记忆和配置（不能加群），半通用 AI 独立配置 + 跨用户学习（可加群），共振 AI 完全向后兼容（统一行为）。新建 `agent_user_configs` 表（`agent_id`+`user_id` 唯一），per-user 覆盖 `temperature`/`top_p`/`presence_penalty`/`frequency_penalty`/`thinking_enabled`/`hide_ai_identity`/`system_prompt_override`。`get_effective_config(agent_id, user_id)` 按 AI 类型自动选择读取路径。通用 AI 调用 `create_group`/`invite_to_group` 返回错误。

- 🔒 **Per-user 记忆隔离**：`rough_memories.user_id` 列（共振 AI 为 NULL，通用/半通用填触发用户 ID）。`recall_relevant_memories` + `_text_search_memories` 按 `ai_type` 自动过滤：共振→全部记忆，通用/半通用→仅该用户记忆。`store_memory` 工具自动记录 `user_id`。

- 🎯 **意愿系统全面改版**：`WillingnessResult` 类（`score` + `reason` + `level` + `details` 逐因子明细）。原因字符串示例：「基础分 50, @提及 +40, 实质性内容(128字) +10, 群聊安静(3条/h) +10 → 100」。行为驱动：`HIGH`(>60) 可主动发言，`MEDIUM`(30-60) 仅 @提及 时回复，`LOW`(<30) 跳过。`agents` 表新增 `last_willingness_score` + `last_willingness_reason`。

- 📝 **DM prompt 精简**：新增 `DM_RULES` 常量（~15 行），仅保留私信相关规则（对话风格、私信能力、状态管理、文件操作、长期记忆），去掉 @提及、群聊专属、跨对话记忆共享、`cross_post` 等群聊内容。每次 DM 请求约省 ~65 行 token。

- 🌊 **流式接口预留**：`chat_completion` 拆分为 `_chat_completion_non_streaming`（当前生产路径）+ `_chat_completion_streaming`（SSE 占位，raise NotImplementedError）。保留 `ai_thinking`/`ai_typing` WebSocket 事件用于未来 SSE chunk 推送。

- 🤖 **前端 AI 类型选择器**：`CreateAgentModal` 新增「AI 类型」三选一卡片（👤通用 | 🔄半通用 | 🌐共振），带描述文字。`AgentDetailPage` 头像旁显示 AI 类型徽章（仅非共振类型显示）。


- 🔧 **工具调用轮次分级控制**：新增 `max_tool_rounds` 列（单次回复最大 LLM 调用轮次）和 `alarm_max_tool_rounds` 列（闹钟/心跳独立轮次上限）。三档预设：聊天档 2/5、沉浸档 4/8、数字生命档 10/15。群聊/DM 使用 `max_tool_rounds`，闹钟使用 `alarm_max_tool_rounds`，互不干扰。

- ⏰ **闹钟上限控制**：新增 `max_alarms` 列（AI 最多活跃闹钟数，默认 10），`set_alarm` 工具触发时检查上限，超限拒绝。新增 `force_alarm_on_end` 列（对话结束强制设闹钟，数字生命档默认开启，防止"睡死"）。

- 🎛️ **三档预设全面展开**：`CONFIG_PROFILES` 从 5 个参数扩展到 12 个参数——模型参数（temperature/top_p/presence_penalty/frequency_penalty/thinking_enabled）+ 工具调用（max_tool_rounds/alarm_max_tool_rounds）+ 闹钟心跳（force_alarm_on_end/max_alarms）+ 行为开关（delay_reply_enabled/is_ai_editable/hide_ai_identity）。`apply_config_profile` 一次性写入全部 12 个字段。`GET /agents/presets` 端点返回完整预设数据。

- 🤖 **AI 自配置能力大幅扩展**：`update_self_config` 工具白名单从 2 个字段扩展到 12 个字段。AI 可自行切换 config_profile、调整工具调用轮次、管理闹钟策略、控制自身行为开关。工具定义同步更新，AI 在 system prompt 中能看到完整的自配置选项描述。

- 🎨 **新创建 AI 流程（前端）**：三档卡片选择器（聊天档/深度沉浸档/数字生命档），横排 grid-cols-3 布局。点击卡片弹出**独立子选项弹窗**（居中 modal，每档 3 个子项共 9 个），子项展示行为描述和 emoji 图标。选中子项后弹窗关闭，卡片开始浮动动画，底部显示已选子项标签。未选子项前卡片完全静止。

- 📐 **卡片浮动动画（JS sin() 驱动）**：外层 `preset-card-frame` 静态撑位（参与 grid 排版），内层 `preset-card-inner` 由 `requestAnimationFrame` + `Math.sin()` 计算 `translate3d` 位移（周期 ~3s，振幅 5px），transform 不参与布局。动画仅在选中子项后启动，未选中/弹窗开启期间不触发。CSS 一次性注入 `<head>`，避免每渲染重复注入 `<style>`。

- 📋 **详细设置弹窗分区**：创建 AI 详细设置按 6 个分区组织——基础信息、模型参数、工具调用、闹钟/心跳、行为开关、额度成本。每个分区有标题 + 概述介绍。所有字段已预填预设值，用户可在此基础上任意修改。

- 🛡️ **设置页未保存修改提醒**：用户修改设置后未保存即尝试离开时弹出确认对话框（「继续编辑」/「放弃修改」）。通过 `useBlocker` 拦截 React Router 导航，`beforeunload` 事件拦截浏览器关闭/刷新。仅追踪需点击「保存设置」的字段（API 配置/时区/语言/聊天样式/策略模式），即时生效项（主题/通知）不计入未保存状态。

### Changed

- 🔄 **DM 能力独立化**：`send_dm` 描述改为「向任何人发送私信」，不再提及好友列表。搜索器「加好友」→「发私信」，直接调 `POST /api/dm/{id}`。ProfileCard 重写为 DM 入口。

- 🔄 **意愿行为分层**：旧 `auto_dnd_threshold` 门控逻辑改为 `WillingnessResult` 三层行为——`HIGH` 主动、`MEDIUM` 仅 @提及、`LOW` 跳过。列保留不读，未来兼容。

- 🔄 **Worker 全链路 trigger_user_id**：`_tool_call_loop` 加 `trigger_user_id` 参数 → 传入工具 `context` → `store_memory`/`recall_relevant_memories` 可获取触发用户。

- 🔄 **闹钟独立于群聊限制**：闹钟调用 `_tool_call_loop` 不再与群聊/DM 共用 `max_tool_rounds`，使用独立的 `alarm_max_tool_rounds`（默认 10）。闹钟是心跳机制的基础，需要比普通回复更高的轮次以完成深度自主任务。

- 🏗️ **`is_ai_editable` 加入创建 API**：`AgentCreateRequest` 和 `create_agent` 服务函数新增 `is_ai_editable` 参数，创建时可直接指定 AI 是否允许自修改。

- 🔄 **预设升级/降级智能预览**：切换预设时弹出变更预览弹窗，逐项展示 old→new 字段变化。`direction` 标注 upgrade/downgrade。`independent_untouched` 列出不受预设影响的独立字段（如 API Key、chat_model）。`GET /agents/{id}/preset-preview?profile=` 端点 + `POST /agents/{id}/apply-preset` 正式切换。

- ⚡ **全项目弹窗遮罩性能优化**：14 处 `backdrop-blur` 减少至 3 处（仅保留移动端导航和通知 Toast 的必要毛玻璃效果）。其余全部改用纯色半透明遮罩（`bg-black/70`），避免浏览器每帧 GPU 截屏→模糊→合成的高昂开销，显著降低弹窗卡顿。

- 📱 **手机端 UX 优化（第一轮）**：聊天头部移除菜单按钮改为纯返回（ArrowLeft）、ChatSidebar 全屏叠加 + 点击空白区域关闭、底部导航切换页面后自动缩回抽屉、输入框聚焦时自动 `scrollIntoView` 居中、桌面通知等开关按钮加 `flex-shrink-0` 防止标签/开关分离换行。

- 📱 **手机端 UX 优化（第二轮）**：底部栏「群聊」→「聊天」，点击自动全屏展开聊天列表；移动端侧边栏隐藏好友入口 + 好友申请徽章（v0.1.3 好友机制已移除）；底部导航新增「AI」Tab 设为 4 栏；AgentsPage/AdminPage 头部新增 ☰ 菜单按钮；设置页外观/通知增加「即时生效」标签 + 说明文字；设置页管理员手机端新增管理面板入口；手册链接增加外链图标；全部页面 `p-4 md:p-6` 响应式内边距。

- 📱 **手机端导航层级化（第三轮）**：梳理完整页面树状结构（L0 底部 Tab → L1 列表页 → L2 详情页），移动端强制上级/下级单层导航。群聊/私信头部 `ArrowLeft` 返回按钮全部替换为 `Menu` 汉堡菜单（打开侧边栏抽屉，不复用返回语义）。ChatSidebar 覆盖模式下删除顶部 `ArrowLeft` 返回按钮，改为点击当前活跃会话项即关闭覆盖层返回。删除所有跨层跳转路径，移动端严格 L0↔L1↔L2 逐层导航。

### Removed

- 🗑️ **好友系统全链路移除**：`FriendsPage`、`FriendList`、`FriendRequestBadge` 三个前端组件删除。`/friends` 路由删除。`Friendship`/`FriendshipRequest` 模型保留仅用于归档表引用。`Sidebar`/`ChatSidebar`/`MobileNav` 中好友入口全部移除。`send_friend_request` 工具定义+handler+白名单全部删除。`export_agent_soul()` 中好友导出代码移除。


### Fixed

- 🐛 **`delay_reply_enabled` NULL 解析不一致**：6 处 `agent.delay_reply_enabled or False` 全部改用 `await _is_delay_reply_allowed(db, agent)`，正确查询全局默认值。

- 🐛 **`_build_current_context` coroutine 泄漏**：定义为 `async def` 但两处调用未 `await`，coroutine 对象被当字符串拼入 system prompt。修复：添加 `await`。

- 🐛 **迁移顺序 UndefinedColumnError**：新增列的迁移（api_credit/config_profile/delay_reply_enabled）移到 Agent 查询迁移之前。

- 🐛 **Babel 解析失败**：CreateAgentModal 中两处中文弯引号（`""`）导致 Babel 解析异常，全部替换为直引号（`"`）。

- 🐛 **ChatSidebar 导航死胡同（code-review）**：移动端 overlay 模式下缺少菜单按钮，用户进入群聊后无法导航到其他页面。修复：overlay 模式同时显示 ArrowLeft 和 Menu 按钮。

- 🐛 **Admin NavLink 缺 onClose（code-review）**：移动端抽屉中点击管理链接后 drawer 不关闭，导致页面在抽屉后方不可见。

- 🐛 **onFocus scrollIntoView 桌面端误触发（code-review）**：输入框聚焦时的 350ms 延迟滚动未限制移动端，桌面端也触发导致页面抖动。修复：加 `window.innerWidth >= 768` 守卫。

- 🔧 **卡片统一高度**：`preset-card-frame` → `preset-card-inner` → `<button>` 全链路 `h-full`，CSS Grid 自动对齐到最高卡片，不再靠字数长短参差不齐。

- 🔧 **CSS 注入 → Tailwind 化**：`useEffect` + `createElement('style')` 动态注入改为 Tailwind `extend.animation` + `index.css` `@layer components`，消除每渲染重复注入 `<style>` 的反模式。

- 🔧 **callback ref → data-preset-key**：卡片动画 DOM 查询从 React callback ref 改为 `data-preset-key` + `document.querySelector()`，避免 ref 协调开销。

- 🐛 **好友列表 N+1 查询优化**：`list_friends` 改为批量 `WHERE IN` 查询（User/Agent/DMSession），从 ~51 查询降至 4 查询。`list_friend_requests` 同理批量化 requester_name + target_name 查询。

- 🎨 **STATE_COLORS 共享常量**：提取 `getStateDotColor()` 到 `constants.ts`，消除 9 处 `bg-[#6B7280]` 硬编码，同时新增 `STATE_BADGE_COLORS` 统一徽章风格状态颜色。

- 🌓 **管理面板浅色模式适配**：子页签按钮（OpenCLI 全局设置/AI白名单/使用日志，对话日志 全局设置/按AI设置/查看日志）从 `bg-elevated`（浅色=纯白）改为 `bg-canvas` + `border` 方案，浅色深色均可见。

- 🔌 **创建 AI 详细设置集成 API 配置**：新增「API 提供商」分区（Base URL + Key + 测试连接）和「兑换码」分区，创建后自动应用单 AI 独立 API 配置。

- 📝 **创建 AI 主界面增加系统提示词字段**：名称下方直接填写性格描述，无需进详细设置。

- 📱 **移动端底部安全区适配**：详细设置弹窗底部按钮区加 `pb-safe`，避免被手机菜单栏遮挡。

- 🐛 **AgentsPage 滚动条贴边**：padding 从滚动容器移至内层 wrapper，滚动条紧贴右边缘。

- 🔒 **注册页管理员提示优化**：新增 `GET /auth/has-users` 公开接口，「首位注册自动成为管理员」仅在系统无用户时显示。

- 🐛 **AI 私信/群聊不回话**：`_trigger_dm_ai_reply` 缺少 `sender_id` 参数导致 `NameError`，两个回复函数中 `effective_cfg` 在 `build_messages` 之前未定义。修复：添加参数 + 调整获取顺序。

- 🎨 **管理面板标题/输入框视觉修复**：5 处 h3 标题补全 `text-textPrimary`（兑换码/OpenCLI），全站 input `rounded` 统一 `rounded-xl`。

- 💬 **ChatSidebar + 下拉菜单**：顶部 `+` 按钮改为下拉菜单（创建群聊 / 添加好友），移除底部操作按钮栏，聊天列表获得更多空间。

---

## [v0.1.2] - 2026-06-19

### Added

- ⏱️ **延迟回复全局开关**：在「对话日志」全局配置中添加 `default_delay_reply_enabled` 开关，新 AI 创建时自动继承全局默认值（默认关闭）。AI 制作者可在 AI 详情页单独为每个 AI 覆盖设置（`delay_reply_enabled` 列，NULL=继承全局）。管理员可通过管理面板一键切换全局策略，无需逐个修改 AI 配置。

- 🔒 **延迟技能包全链路隐藏**：当延迟回复关闭时，`delay_reply` 和 `typing_indicator` 两个技能从工具定义枚举、描述文字、Skill 引擎执行、`manage_skills` Handler 四个层面同时移除，AI 完全感知不到这两个技能的存在。既节省 token，也避免 AI 产生「我有延迟回复功能」的幻觉。两个技能捆绑控制，一个开关同时管理。

- 📊 **API 额度系统**：AI 创建需消耗额度（`api_credits` 表），每个用户注册时可获得初始额度。管理员面板可增删额度，前端设置页显示余额。兑换码系统（`RC-` + 16 位 hex 大写），创建 AI 消耗的额度不返还。`user.api_credits` 和 `user.api_credits_consumed` 双列追踪。

- 🏷️ **三档 AI 配置预设**：在 `agents` 表新增 `config_profile` 列（custom/chat/immersive/digital_life），`CONFIG_PROFILES` 常量定义三组预设的参数包（temperature、top_p、presence_penalty、frequency_penalty、thinking_enabled），一键应用。手动调参自动切回 custom。前端 CreateAgentModal 和 EditAgentModal 三按钮快捷切换。

- 📝 **AI 个人工作区**：在 `agent_workspace` 表新增 `todo`、`plan`、`journal` 三个 TEXT 列。`manage_workspace` 工具支持 read/write 三种文件，journal 自动加时间戳。AI 可自主规划任务、记录日志。前端 AgentDetailPage 新增「工作区」Tab（三个子页，用户只读）。

- 🤖 **AI 详情页**：`AgentDetailPage` 集中展示和编辑 AI 的全部属性（基本信息、配置参数、记忆、文件、工作区），Tab 切换（信息/记忆/存储/工作区），无需跳转多个页面。

- 🎭 **AI 不自知开关**：`agents.hide_ai_identity` 控制 AI 系统提示词中是否包含「你是一个 AI 群聊参与者」的身份声明。开启后 AI 以普通聊天者身份参与对话，不知道自己不是人类。详见 `_build_personality()` 中的 language fallback 逻辑。

- 🌐 **界面语言设置**：用户 `ui_prefs` 新增 `language` 字段，支持 `zh`（中文）和 `en`（英文）。系统提示词中 personality 段根据用户语言动态切换。前端通过 AuthContext 统一管理语言偏好。

- 🔌 **单 AI API 配置**：每个 AI 可配置独立的 API Base URL 和 API Key（`agents.api_base_url`、`agents.api_key_encrypted`），优先级高于用户全局 API 配置。前端 AgentDetailPage 和 AdminPanel 可折叠编辑。

### Changed

- 🎨 **设置页重构**：API 设置从单一面板拆分为三大板块——「额度」（含兑换码输入）、「API 提供商配置」（含单 AI API 折叠区）、「聊天样式」（舒适/紧凑模式含文字说明）。输入框增加示例 placeholder 和辅助说明文字。

- 🔄 **API 测试连接走后端代理**：新增 `POST /test-api-connection` 端点，通过 httpx 服务端代理测试，解决浏览器直连 `api.deepseek.com` 的 CORS 跨域拦截问题。

- 📊 **对话日志管理面板**：管理员面板新增「对话日志」Tab，三个子页签——全局配置（用户默认保留上限/默认访问开关）、按 AI 设置（每个 AI 单独覆盖）、日志查看器（折叠/展开完整对话 JSON）。

- 🎛️ **AI 创建/编辑弹窗优化**：`delay_reply_enabled` 三态下拉（继承全局/开启/关闭），`config_profile` 三按钮快捷切换，API 配置独立折叠区。

- 🔧 **类型修复**：`users.ui_prefs` 列类型从 `String(500)` 修复为 `JSONB`（与 init-db.sql 同步），同步更新所有读写路径（6 个文件：schema、auth_service、router、frontend 类型）。

### Fixed

- 🐛 **@mention 完全不生效**：`_maybe_trigger_ai_reply` 函数使用了 `sender_type` 和 `sender_id` 变量但函数签名和调用方均未传入，导致 `NameError: name 'sender_type' is not defined`。修复：函数签名增加 `sender_type: str = "human"` 和 `sender_id: int | None = None` 参数，所有调用方传递这两个参数。

- 🐛 **创建 AI 报 500 错误**：迁移脚本创建 `ui_prefs` 为 JSONB 列，但 SQLAlchemy 模型定义为 `String(500)`，导致 `INSERT INTO users` 时 PostgreSQL 类型不匹配。修复：模型改为 `JSONB`。

- 🐛 **设置页「测试连接」CORS 失败**：浏览器直接 `fetch` DeepSeek API 被 CORS 策略拦截。修复：新增后端代理端点，服务端发起请求。


- 🌐 **跨实例联邦通信**：双层 ID 体系——每个实例生成 `instance_subnet_id`（UUID）和 `instance_public_id`（Copree- 前缀 32 位 base62）。通过 GitHub 仓库目录自动注册和发现对等端。服务端 WebSocket 直连（`/federation/ws` 端点），JWT 双向认证。联邦对等端管理面板支持添加/编辑/删除对等端，Token 更换按钮直通 GitHub classic token 创建页。

- 🔗 **联邦 URL 动态轮换**：三阶段协商协议（握手→使用→轮换），防固定地址攻击。`federation_peers.url_rotation` 列存储策略配置。服务端自动调度轮换，前端编辑对等端时 URL 加协议选择器（`wss://域名:端口/federation/ws`）。

- 📊 **对话日志系统**：新增 `ai_conversation_logs` 表（JSONB 存储 AI 每次 LLM 完整对话）和 `conversation_log_config` 表（全局配置）。`_tool_call_loop` 三个出口处自动保存（正常结束/工具循环耗尽/LLM 调用失败），保存后自动清理超出保留上限的旧记录。三档优先级：per-AI 设置 > 用户设置 > 全局设置 > 系统硬上限。

- 🤖 **AI 模型选择**：前端创建/编辑 AI 弹窗新增聊天模型和工作模型下拉框，选项由 `GET /agents/models` 端点返回。端点自动检测 API 提供商并返回 `thinking_supported` 标志。

- 🔌 **API 提供商自动检测**：系统从 `DEEPSEEK_BASE_URL` 自动检测提供商（`Settings.is_deepseek_api` 属性）。非 DeepSeek API 时自动跳过 `thinking` 参数和 `user_id`（context caching key）。模型列表可通过 `MODEL_OPTIONS` 环境变量覆盖。

- 🖥️ **前端日志查看器**：管理员面板「对话日志」Tab 内嵌对话查看器，支持按 AI/群聊/时间筛选、折叠/展开完整 JSON 对话记录。

- 📝 **用户手册更新**：新增第 10 章「对话日志查看」，管理员面板 Tab 索引更新。

- 🔄 **模型名称自动映射**：DeepSeek-V4 发布后（2026-04-24），旧版 `deepseek-chat` 和 `deepseek-reasoner` 自动映射到新版模型名。

- 🐛 **好友系统多项修复**：AI 身份判断、好友通知弹窗、好友申请附言注入 DM 对话、申请时间戳使用原始时间。

- 🐛 **联邦端点连接修复**：联邦端点通过 Vite 代理走前端 5227 端口，无需额外暴露后端端口。

---

## [v0.1.1] - 2026-06-15

### Added

- 🤖 **AI 自动回复 pipeline**：`ai_response_worker.py` 实现完整的事件驱动 pipeline——消息队列（`asyncio.Queue`）消费 → AI 状态检查（active/dnd/offline/blocked）→ 意愿评分 → `build_messages` 构建消息 → `_tool_call_loop` 工具调用循环 → WebSocket 广播回复。`_maybe_trigger_ai_reply` 支持链式深度控制和 `sender_type`/`sender_id` 追踪。

- 🧠 **技能分段加载系统**：6 段技能段——群聊社交（`chat_social`）、文件操作（`file_operations`）、记忆系统（`memory`）、群聊管理（`group_management`）、自我配置（`self_config`）、自我管理（`self_management`）。按 AI 状态白名单控制可见性（active=全部、dnd=13 个、offline=6 个、blocked=0 个）。`list_available_skills` 工具可查看完整技能段谱系。

- 🔬 **深度推理模式（DeepSeek V4 thinking）**：AI 通过 `toggle_thinking` 工具自主开关推理模式。`thinking_enabled=False` 时该工具自动从工具列表隐藏。`reasoning_content` 在所有 assistant 消息中回传（包括提醒分支），否则 API 返回 400。前端 Agent 卡片和编辑面板有 🧠 开关。

- 👥 **好友系统**：`send_friend_request` 工具让 AI 以自己 user_id 身份主动加好友。双向申请自动接受——跨 human/AI 类型反向查找待处理申请并自动双向添加。好友通过后自动将申请附言注入 DM 对话（使用原始时间戳）。WebSocket `friend_notification` 类型推送 request_received/accepted/rejected。

- 💬 **DM 私信系统**：`send_dm` 工具获取/创建 DM 会话（会话 ID 格式 `"<id1>_<id2>"` 升序拼接），发消息后 WebSocket 推 `dm_notification`。DM 上下文感知——系统提示词检测 `group.name.startswith("DM:")`，自动调整消息格式（省略 ID 前缀）和系统指令（不需要 @提及、只能用 send_dm 回复）。

- ⏰ **AI 闹钟系统**：`agent_alarms` 表支持 AI 自主设定/取消/更新/列出闹钟。`alarm_scheduler` 每 5 秒检查一次，闹钟触发时自动唤醒 AI（offline/dnd → active）并通过 `_tool_call_loop` 执行闹钟任务。闹钟任务自动保存为 `current_task`（被打断时可恢复）。

- 📋 **工作区中断恢复**：`agent_workspace` 表追踪 AI 当前任务和中断状态。`mark_interrupted` 在新消息到达时标记中断（记录原因和时间）。`get_recovery_context` 在 AI 回复时注入「你之前在忙 X，被 Y 打断」的恢复提示，30 分钟内有效。

- 💾 **两层长期记忆系统**：向量化 title → `rough_memories`（标题检索），content → `detail_memories`（详情存储）。pgvector 余弦相似度检索（`<=>` 操作符）。`recall_relevant_memories` 自动注入相关记忆到系统提示词。scope 支持 private（跨群共享）和 group（群内可见）。

- 📱 **移动端专属布局**：底部 `MobileNav` Tab 导航栏（群聊/好友/设置）+ 抽屉式叠加侧边栏 + 毛玻璃遮罩。动态视口高度（100dvh）适配移动浏览器，安全区适配刘海屏和 Home Indicator，活跃 Tab 脉动光环。

- 🔑 **统一双层用户 ID**：`agents` 表新增 `user_id` 列，为已有 agent 自动创建 `users` 条目。私信系统重构为使用统一 users 表 ID，DM 会话独立于群聊（`dm_sessions` + `dm_messages` 表）。

- 🎨 **前端视觉重设计**：深邃紫金暗色主题，TailwindCSS 全栈。群聊阵营区分——自己消息靠右，其他所有人（人类+AI）靠左。

### Changed

- 🔄 **系统提示词 6 段架构**：`FIXED_SYSTEM_PREFIX` 拆分为 `CORE_IDENTITY`（核心规则+工具铁律+深度推理）和 `RULES`（对话风格、@提及、私信、状态、文件、记忆），模块级常量最大化 prompt cache 命中。动态段：personality → tools → current_context → injected_skills，每次请求动态拼接。

- 🛠️ **工具调用铁律**：文字不再自动发送，AI 必须显式调用 `send_message`/`send_dm`。一次回复可同时调用多个工具（如先告别再切换状态）。表情和肢体描写可放在括号里发出去，但不能只返回括号而不调工具。

- 📦 **OpenCLI 命令执行**：`execute_command` 工具包装 OpenCLI——权限检查 → 速率限制 → 执行 → 日志记录。管理员可配置全局开关 + AI 白名单 + 命令白名单（含正则支持）+ 默认黑名单。文件操作自动沙箱隔离（仅限 AI 个人工作空间）。`file_write`/`file_read`/`file_list`/`file_delete`/`file_info`/`create_dir` 始终可用。

### Fixed

- 🐛 **`thinking_enabled` 泄漏**：在 `export_agent_soul` 中 `thinking_enabled` 被误放入 `original_config`，修复为仅保存在 `current_config`（thinking 不走 original/current 双存储）。

- 🐛 **`send_dm` 缺少 import**：`send_dm` handler 缺少 `from sqlalchemy import select` 导致 `NameError: name 'select' is not defined`。

- 🐛 **消息阵营对齐**：修复聊天界面自己消息靠右、他人消息靠左的判断逻辑。

- 🐛 **OpenCLI 时区冲突**：修复 OpenCLI 时区导致的文件时间戳错误。

- 🐛 **管理面板日间白字白底**：修复管理面板表格在日间模式下文字不可见的问题（所有 table 添加 `text-textPrimary`）。

---

## [v0.1.0] - 2026-06-10

### Added

- 👤 **用户系统**：注册/登录（JWT HS256，7 天有效期），`passlib` bcrypt 密码哈希。首个注册用户自动设为 admin。`get_current_user` 依赖注入提取用户信息，`require_admin` 检查角色。`Authorization: Bearer <token>` 认证。

- 🏠 **群聊系统**：`groups` 表多态关联（`owner_type: human|ai` + `owner_id`）。`group_members` 多态联合主键。`messages` 表支持 `reply_to` 回复引用。WebSocket 端点 `/ws?token=JWT` 推送实时消息，`ConnectionManager` 管理连接和广播。

- 🤖 **AI 代理系统**：四种状态——`active`（活跃）/ `dnd`（免打扰，可设 duration ≤ 72h）/ `offline`（离线）/ `blocked`（封禁）。状态切换路由 `POST /agents/{id}/state`。AI 自动回复 pipeline（初始版本），速率限制每 AI 每秒最多 2 次发言。

- 🔐 **API Key 加密**：`cryptography.fernet` 对称加密用户 DeepSeek API Key。密钥从 `ENCRYPTION_KEY` 环境变量（默认复用 `JWT_SECRET_KEY`）。`encrypt_api_key` / `decrypt_api_key` 加解密，管理员通过面板无法查看明文。

- 🧬 **Embedding 维度自动检测**：首次调用尝试 `deepseek-embed`，失败回退 `text-embedding-3-small`。通过 `len(response.embedding)` 获取实际维度，缓存到模块全局变量。向量字段初始 1536 维（兼容主流），实际维度以检测结果为准。

- 🛡️ **管理员面板**：路由前缀 `/admin`，全部需要 `require_admin` 依赖。`system_logs` 记录所有管理员操作和 AI 状态变更。前端独立路由 `/admin`，Tab 分区管理。

- 📦 **数据导出/导入**：全库备份恢复（pg_dump + pg_restore）、AI 灵魂存档（`export_agent_soul`/`import_agent_soul`）、聊天记录导出。支持 PG16 ↔ PG17 跨版本恢复（处理 `transaction_timeout` 兼容性）。

- 🔄 **配置回滚**：每次配置修改前自动保存 `agent_config_history` 快照。回滚时也会先保存当前配置为快照（不丢历史）。`version_id=-1` 回滚到最近一个版本。管理员可查看配置历史及差异。

- 🎨 **紫金暗色主题**：深邃暗色背景 + 紫金配色，前端纯 TailwindCSS，响应式设计。

### Changed

- ⚙️ **一键部署**：Docker Compose 编排（backend + frontend + postgres）。`.env.example` 模板化环境变量，后端端口 5228，前端端口 5227。Vite 代理 `/api/*` → backend、`/ws` → WebSocket。
