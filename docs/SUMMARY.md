# Copree 文档目录

> **版本**: v3.5.0 | **更新**: 2026-09-13

---

## 📚 文档分类

### 一、产品介绍

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [README.md](../README.md) | 所有人 | 项目入口，快速开始、核心能力、技术栈 |
| [ABOUT.md](./ABOUT.md) | 所有人 | 产品理念介绍，适合分享给朋友 |
| [ROADMAP.md](./dev/ROADMAP.md) | 所有人 | 路线图，已实现和规划中的功能 |

### 二、用户指南

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [用户手册.md](./guides/用户手册.md) | 终端用户 | 创建 AI、群聊、私信、记忆、用量等操作指南 |
| [管理与开发者手册.md](./guides/管理与开发者手册.md) | 管理员/开发者 | 部署、架构、排错、WebSocket |
| [创建 AI 流程设计.md](./guides/create_ai_flow_design.md) | 前端开发者 | 三档预设 + 子选项 + 详细设置的交互设计 |
| [故障排查手册.md](./guides/troubleshooting.md) | 管理员/开发者 | 常见问题诊断流程图、错误码速查、一键诊断脚本 |
| [备份与恢复指南.md](./guides/backup_and_recovery.md) | 管理员 | 3-2-1 备份策略、时点恢复、灾难恢复方案 |
| [WebSocket 事件文档.md](./guides/ws_events.md) | 前端/集成开发者 | 所有 WS 事件的 payload 格式、时序图、重连策略 |
| [测试策略文档.md](./guides/test_strategy.md) | 开发者/QA | 当前套件与两种跑法、单/集成测试规范、覆盖率口径与基线、CI/CD；含「写完用例必须证明它会红」与排查踩坑 |
| [安全与权限模型.md](./guides/安全与权限模型.md) | 管理员/开发者 | 三条权限铁律（群成员/角色/匿名文件）、受保护入口速查、生产加固清单、可复制的自查命令、保留取舍与自动化守卫 |
| [部署合规建议书.md](./deployment-compliance.md) | 管理员/部署者 | 中国境内内容标识 / 拟人化互动服务法规对照与操作建议 |

### 三、服务模块设计

#### 3.1 聊天底层服务

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [chat_service_design.md](./chat_service/design/chat_service_design.md) | 开发者 | 消息管道、可达性管理、连接管理、联邦协议、ChatApi |
| [federation_url_rotation_protocol.md](./chat_service/protocol/federation_url_rotation_protocol.md) | 联邦开发者 | 联邦连接 URL 轮换与安全策略 |

#### 3.2 AI 底层服务

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [ai_service_design.md](./ai_service/design/ai_service_design.md) | 开发者 | LLM 调用、工具执行、流式响应、配置管理、额度消耗 |

#### 3.3 AI 薄大脑控制系统

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [brain_controller_design.md](./brain_controller/design/brain_controller_design.md) | 开发者 | 心跳管理、状态机、冲突仲裁、人格锚点、资源调度 |
| [emotion_state_design.md](./brain_controller/design/emotion_state_design.md) | 开发者 | 情感向量（Plutchik 8 轴）、跨状态情感同步、交接体系、工具按状态隔离 |

#### 3.4 AI 记忆系统

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [memory_system_design.md](./memory_system/design/memory_system_design.md) | 开发者 | 双重记忆架构、结构化记忆、记忆分发、遗忘机制 |
| [memory_system_overview.md](./memory_system/design/memory_system_overview.md) | 开发者 | 记忆系统核心设计理念 |
| [vector_search_and_embedding.md](./memory_system/design/vector_search_and_embedding.md) | 部署者/管理员 | Embedding 插件化、维度配置、前端图形化管理、检索策略、索引性能 |

#### 3.5 AI 模块化技能管理系统

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [skill_manager_design.md](./skill_manager/design/skill_manager_design.md) | 开发者 | Skill 分层、声明式依赖、模板引擎、多维触发器、注意力系统 |

#### 3.6 统一插件系统

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [plugin_system_design.md](./plugin_system/design/plugin_system_design.md) | 开发者 / 管理员 | 目录即插件协议、两级开关（管理员全局 + 用户个人）、皮肤插件、技能插件桥接 |
| [plugin-protocol-v2.md](./plugin-protocol-v2.md) | 开发者 / 管理员 | 阶段二设计：语言中立的行为插件协议 |
| [plugin-protocol-v3.md](./plugin-protocol-v3.md) | 开发者 / 管理员 | 阶段三（已实现）：服务类插件（category: service）、插件级加密配置、生命周期收敛 |
| [plugin-market.md](./plugin-market.md) | 开发者 / 管理员 | 总商城与插件商城：三层信任、安装包安全边界、社区索引仓与 CI 通过规则 |
| [agent-channels.md](./agent-channels.md) | 用户 / 管理员 | 给自己的 AI 接 QQ：三种身份、配对制、归属与实例约定、接口与已知限制 |

### 四、子系统专题

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [兑换码系统.md](./兑换码系统.md) | 开发者 | 兑换码系统设计与实现 |
| [文件存储与协作系统.md](./文件存储与协作系统.md) | 开发者 | 文件上传、协作模式、引用追踪、配额管理 |
| [魔视界.md](./magic-vision.md) | 开发者 | 魔视界 —— CSS 滤镜系统 |

### 四·五、群视界（Group World）专题

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [群视界设计文档](./group_world/design/group_world_design.md) | 开发者 | 总设计：世界模型、群视界机器人、阶段规划；运行模式（自动/审阅/计划）与文件安检（§7.13） |
| [群视界实现文档](./group_world/implementation.md) | 开发者 | 实现现状 + 阶段 2 架构决策与踩坑（ADR 风格） |
| [群视界阶段 2 规划](./group_world/plan_phase2.md) | 开发者 | 阶段 2 清单（2.1-2.5 已完成）与估算 |
| [接口文档服务](./group_world/design/api_docs_service.md) | 开发者 / 管理员 | /kb 接口 + docx 导出 + pandoc 安装、路径规则、422 local_kw 坑 |
| [群视界 API 文档](./group_world/api/world_api_docs.md) | 开发者 / 世界 AI | 10 大分区接口手册（变量/文件/积木/群聊/同步限流/受控 API…） |
| [世界 Skill 机制](./group_world/design/world_skill_design.md) | 开发者 | 文件式 skill/tool 机制（world skill runtime） |
| [世界工具插件开发](./group_world/development/world_tools_plugin.md) | 开发者 / 社区 | 一个工具一个文件：契约、注册、展示文案、外部插件目录、性能 |
| [世界决策技能](./group_world/design/world_decision_skill.md) | 开发者 | Decision Skill 与触发模式 |
| [世界能力注入](./group_world/design/world_agent_capabilities.md) | 开发者 | 群 AI / 世界 AI 的能力边界与路径 |

### 五、探索与讨论

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [Copree 基于 Agent 的项目探索与架构探讨.md](./exploration/Copree 基于 Agent 的项目探索与架构探讨.md) | 研究者/开发者 | 项目探索与架构讨论（原始对话） |
| [Copree 重构设计文档.md](./exploration/Copree 重构设计文档.md) | 开发者 | 重构设计总览（精简版） |

### 六、技术参考

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [CODE_WIKI.md](./CODE_WIKI.md) | 开发者/维护者 | **代码 Wiki**：项目架构、模块职责、关键类与函数、全量路由表、i18n 体系、构建产物与插件包、API 端点、数据模型、配置部署、开发指南 |
| [LEARNING_ROADMAP.md](./LEARNING_ROADMAP.md) | 新开发者 | **学习路线图**：5 阶段阶梯式学习，每阶段有目标、必读文件、Mermaid 图表、动手实践任务 |
| [DSH 接入指南.md](./DSH接入指南.md) | 开发者 | 将 Copree 接入 DeepSeek Harness（DSH） |

### 七、项目参考

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [项目全景报告.md](./reference/项目全景报告.md) | AI 智能体/用户/企业 | 产品白皮书（当前 v0.4.0）：核心亮点、能力矩阵、架构全景、用户画像、路线图、文档索引 |
| [项目参考.md](./reference/项目参考.md) | 开发者 | 参考项目架构思路和设计亮点 |

### 八、开发参考（docs/dev）

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [LLM 端点入口](./dev/llm_endpoint.md) | 开发者 | base_url 版本段拼接规则、供应商连接探针、内网地址策略 |
| [能力懒加载](./dev/capability_lazy_loading.md) | 开发者 | skills/tools 版本化与增量变更注入 |
| [Repository 化重构进度](./dev/repository_refactor_progress.md) | 开发者 | 重构进度的唯一权威存档，接续工作前先读 |
| [重构实施开发文档](./dev/重构实施开发文档.md) | 开发者 | 后端重构实施细节 |
| [AI↔AI 私信规则与限额](./dev/ai_ai_dm_quota.md) | 开发者 | 私信规则、配额与限额 |
| [技术规格书](./dev/cpec.md) | 开发者 | AI 群聊社交网络系统技术规格 |
| [自习室插件开发文档](./dev/STUDY_ROOM_DEVLOG.md) | 开发者 | study-room 插件开发记录 |
| [开发待办](./dev/TODO.md) | 开发者 | 待办清单 |
| [写一个通道插件](./dev/channel-plugins.md) | 插件开发者 | 外部身份（QQ/联邦）怎么接：manifest 的 channel 块、类别名规则、运行时接口、信任边界 |
| [前端界面统一规范](./dev/ui_system.md) | 前端开发者 | **单一来源**：尺度令牌 / 语义类 / 组件库 / 照抄模式 / 提交前自检——写界面前先读它 |
| [演示截图流水线](../scripts/screenshot/README.md) | 文档/推广维护者 | 一键生成 README 演示图：演示数据脱敏规则、CDP 截图与新增一张图的做法（中英双语） |

### 九、宣传与文章

| 文档 | 适用人群 | 说明 |
|------|---------|------|
| [v0.3.1 开源介绍](./promotion/aischat-v0.3.1-article.md) | 所有人 | 项目介绍文章 |
| [v0.3.6 前端展示要点](./promo/v0.3.6_frontend_showcase.md) | 所有人 | 前端焕新展示要点 |

### 十、归档文档

| 文档 | 归档位置 | 说明 |
|------|---------|------|
| AI认知架构三空间模型.md | [archive/old_designs/](./archive/old_designs/) | 内容已整合到记忆系统和薄大脑文档 |
| AI对话链机制.md | [archive/old_designs/](./archive/old_designs/) | 内容已整合到聊天服务和薄大脑文档 |
| 记忆架构设计.md | [archive/old_designs/](./archive/old_designs/) | 内容已整合到记忆系统文档 |
| 流式响应系统.md | [archive/old_designs/](./archive/old_designs/) | 内容已整合到 AI 底层服务文档 |
| Skill 的三层设计.md | [archive/old_designs/](./archive/old_designs/) | 内容已整合到技能管理系统文档 |
| AI上下文与状态管理设计.md | [archive/old_designs/](./archive/old_designs/) | 内容已整合到薄大脑文档 |

---

## 🧭 阅读路线

### 快速了解
1. **README.md** → 5 分钟了解项目核心能力
2. **ABOUT.md** → 理解产品理念和价值主张

### 日常使用
1. **用户手册.md** → 完整操作指南

### 部署运维
1. **管理与开发者手册.md** → 从部署到精通
2. **部署合规建议书.md** → 中国境内内容标识 / 拟人化互动服务法规对照与操作建议
3. **故障排查手册.md** → 遇到问题先看这里
4. **备份与恢复指南.md** → 数据安全保障

### 前端开发
1. **前端界面统一规范**（docs/dev/ui_system.md）→ 写任何界面前先读，照抄即可
2. **WebSocket 事件文档.md** → 实时通信完整参考
3. **测试策略文档.md** → 测试规范和 CI/CD 集成

### 代码学习（新开发者推荐）
1. **CODE_WIKI.md** → 代码 Wiki，了解架构、模块、API、数据模型
2. **LEARNING_ROADMAP.md** → 学习路线图，按 5 阶段循序渐进
3. **项目全景报告.md** → 产品全景，理解设计理念和能力边界

### 深入技术
1. **chat_service_design.md** → 聊天底层服务
2. **ai_service_design.md** → AI 底层服务
3. **brain_controller_design.md** → 薄大脑控制系统
4. **memory_system_design.md** → 记忆系统
5. **skill_manager_design.md** → 技能管理系统
6. **plugin_system_design.md** → 统一插件系统（目录即插件、两级开关、皮肤/技能插件）

---

## 📝 文档规范

### 文件命名
- **设计/实现类文档**：使用蛇形命名（snake_case），如 `chat_service_design.md`
- **其他文档**：使用中文标题，如 `用户手册.md`

### 语言与面向读者规范

- **同时面向国内与国际读者**：正文以中文为主，但**操作步骤必须让两种读者都能照做**
- **命令给两套、各自整段可复制**：凡涉及镜像/加速源的地方，给「A. 官方源（国际）」与
  「B. 国内镜像（中国大陆）」两块**完整**命令。读者按自己网络选一块整段复制；
  **不要**写成"一行官方 + 一行注释掉的镜像"——那要求读者手动改，国内读者容易漏、国际读者容易误用
- **镜像只写面向国内的**（清华 `pypi.tuna.tsinghua.edu.cn`、阿里 `mirrors.aliyun.com`、
  npm `registry.npmmirror.com` 等），不罗列国外镜像
- **官方写法是默认**：官方 URL / 官方命令原样给出，不转述成二次包装的说法
- 指南类文档保留中英双语标题与副标题；关键操作段落给英文摘要
- 术语统一，避免歧义
- 代码块使用正确的语言标记
- 图表使用 Mermaid 语法

### 本地私有资料（一律不入库，放 `gitignore-本地docs/`）

**本仓库是公开的。** 任何"能定位到真实部署"或"能拿来攻击"的内容都不许进 git：

- 审计/渗透报告、漏洞复现细节（含状态码、路径、payload）
- 口令、token、密钥、连接串，以及它们的任何片段（包括"已经改过了"的旧值）
- 公网域名 / 入口 IP / 内网 IP 清单、端口映射与隧道配置
- 运维记录里贴的日志片段（常带上述信息）

约定：**统一放仓库根目录的 `gitignore-本地docs/`**（报告类放其中的 `security/`）——
目录名本身就说明用途；该目录已在
`.gitignore` 中整体忽略；`.gitignore` 另加了 `SECURITY-AUDIT-*.md`、`PENTEST-*.md` 兜底，
防止这类文件被随手放在根目录时误提交。需要给协作者看的结论请去敏后写进 `docs/`
（见 [安全与权限模型](./guides/安全与权限模型.md)），**不要**把原始报告搬进 `docs/`。

### 结构规范（设计类文档）
1. 文档标题
2. 元信息（服务定位、版本、日期、文档规范）
3. 目录（带锚点链接）
4. 正文章节（按逻辑顺序组织）
5. 关键文件索引
6. API 端点（如适用）

### 更新流程
- 修改文档时同步更新版本号
- 重要变更记录在 CHANGELOG.md
- 跨文档引用使用相对路径

---

*文档版本: v3.5.0 | 最后更新: 2026-09-13*
