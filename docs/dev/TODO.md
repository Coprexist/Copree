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

## 🧾 帧清单与快照块的结构债（2026-11-01 记，未做）

帧清单（`## 🗂 你手上的状态帧`）已按「快照 + 增量条目」落地，能跑、有测试，但有几处是折中：

- [ ] **快照设施没有抽出来**：记忆索引走 `capability_versions` 版本链，帧清单是帧上的
      `roster_locked` / `roster_notified` 手写差集——同一形状两套实现。应抽一个「快照块」设施
      （渲染 / 差集 / 解锁点重置 / 条目投递），两边都接它。这条最值钱，也最容易在改记忆索引时炸。
- [ ] **`dormant` 是 status 之外的并列字段**：沉睡本该是状态枚举的一员
      （`active` / `suspended` / `ended` / `dormant` / `retired`），现在每个读 status 的地方
      都得记得还有 `dormant` 这个位。要改得连 `is_running` / `normalize_order` / 链对账一起动，
      并给存量帧做一次补齐，属于单独一档活。
- [ ] **清单仍在 `build_messages` 里写库**：`frame_roster` 一次调用做四件事（读栈、渲染、
      算差集、落条目）。功能是对的，但副作用藏在"读请求体"里，不便测也不便推理。干净的做法是
      开轮前显式 sync 一次、构建时只读——但要覆盖群聊 / 私信 / 闹钟 / 世界四条开轮路径，
      漏一条那一轮就没清单。
- [ ] **段日志靠"执行前后比帧 id"侦测换帧**：真正干净的是在 `_save`（唯一写入点）埋钩子，
      但"当时的 messages"只有 executor 手里有。现状可接受，理由写在 `segment_log` 模块头。
- [ ] **切换行与真实调用共用桶配额**：一个桶只留 N 条，切得勤的那段会把真实调用挤掉。

---

## 🔲 代理 LLM 门卫（2026-11-02，骨架已进工作区，未提交）

> 设计稿与全部口径：[decision_proxy_llm.md](./decision_proxy_llm.md)。

- [x] 极简上下文：槽位定为**派生上下文**（不进状态栈），`services/brain/proxy_judge.py::build_context`
- [x] 替换 `ai/decider.py` Gate 4/5：前置 Gate 0–3 与 @/公告/特别关心不动；默认关
- [x] 自动停用：单一旋钮 `PROXY_MIN_SAVING_RATIO`（0.06，94% 由它推出）+ 样本 10 / 窗口 50，全走配置
- [x] 同一段上下文内被触发 >3 次 → 递一句建议：`services/brain/frame_reminder.py`，投递标记在帧上 `delivered`，解锁复位
- [x] **最近对话**：5 分钟内、最多 10 条，取小；只取 `kind=message`；去掉账本里已有的触发消息
- [ ] **§5 状态帧递规则**：帧给代理递个性化规则、投递回执、主体能看全文、可改可撤回
- [ ] 实测前缀缓存：同一 AI 换群触发时 `cached_tokens` 占比是否随拼装顺序命中
- [ ] 影子已开（`.env` 里 `ENABLED=1`、`ENFORCE=0`），攒够样本后统计正确拦截 / 误杀，再开 enforce
- [ ] 判定表旁路：`proxy_judgements` 目前只有写入与统计，出一份按帧/按天的查询（看真实拦截率）

## 🧊 前缀快照的下一步（2026-11-14 记，未做）

- [ ] **命中时跳过 tools 段渲染**：现在每次唤醒都照跑各段构建（探测与渲染一条路径，零漂移），
  命中快照时这些结果被丢弃，净增约 1 次查询（表情包源登记）。若峰值 Postgres 吃紧，
  可只跳最贵的 tools 段（`_is_delay_reply_allowed` + `frame_tool_gate` + 表情包 latest，约 3 次），
  届时需把表情包源的版本登记挪到包启停处（否则加包不再发通知）。权衡见
  `docs/dev/capability_lazy_loading.md`「锁的是发出去的字节」。
- [ ] **持久化前的首轮**：`cap_effective_prefix` 要等某个会话下一次真实调用才落第一条，
  部署后 24 小时内看一次填充率（`select count(*) from agents where cap_effective_prefix <> '{}'::jsonb`）。

## 🎙 语音（2026-11-14 起）

- [x] **AI 语音自带文稿**（2026-11-14 已落地）：文稿写进 **`file_metadata.transcript`**（唯一来源，
  迁移 `0113`；AI 语音 = 它念的 text），消息快照只留 `file_id`——文稿会变，塞进 N 条消息就会
  N 份不一致、转发一次还会丢。读取按 id 补：账本（`context_sync._transcripts_of` +
  `with_transcripts`，渲染成 `[语音] 那句话`，截 200 字）、前端语音条（`GET /fs/transcripts`，
  只回读得到的、拿到就缓存）、转发（按元数据重建后同样按 id 补）。写入只有一处
  `file_service.set_transcript()`——ASR 将来也走它。用例
  `test_with_transcripts_fills_attachment_text` / `test_attachment_marks_show_voice_transcript`。
  人的语音仍需要下面的 ASR。
- [x] **语音配置走「厂商 + 配置档」**（2026-11-22 已落地）：与 LLM 同构，迁移 `0125`
  把 `agent_voice_configs` 的老数据转成语音厂商 + 配置档并绑定 `voice_profile_id`；
  解析收口到 `voice_config.voice_of()`，`send_voice` / 能力门控 / 检测 / 试听共用同一个入口；
  行上 provider / base_url / key / voice_id / model 暂留作"没选档"的兼容来源。
- [ ] **语音转文字（ASR）——第二步**：口径已定，待实现。
  - **配置**：`provider_presets` 增 `kind="asr"`，与 `llm` / `voice` 同一份清单、同一套
    Fernet Key、「检测通过才启用」的门控（照 `voice_probe` 的模式）。**默认关**，开关文案要写明
    「语音会上传到第三方」。候选：OpenAI `whisper-1` / `gpt-4o-transcribe`、阿里 Paraformer、
    腾讯一句话识别、SiliconFlow 托管的 whisper。
  - **产物**：`attachments[i].transcript`，**不建新表**——附件快照本来就是消息的一部分，
    老消息没有这个字段就是没有，不需要迁移。
  - **时机**：后端**异步转写**（上传完、或 QQ 入站后入队），完成后经现有 WS 广播把 transcript
    补到那条消息上，前端就地更新。站内录音与 QQ 语音走**同一条通路**，不写两份。
  - **消费方**：① 前端语音条下面显示可折叠小字；② `utils/multimodal.attachment_marks()` 在音频
    标记里带上它——只做 UI 等于人看得见、AI 仍听不见。
  - **不做**：浏览器 `SpeechRecognition`（只在 Chrome/Edge，音频发到 Google 服务器，国内不可用）。
  - **备选**：本地 faster-whisper / whisper.cpp 作为「绝不出网」开关；代价是模型 145MB~480MB，
    CPU 上大约 5 倍实时。
  - **已知误差**：中文标点与专有名词会有错，文案按「转写仅供参考」写，不要伪装成逐字稿。

## 🔧 待收尾：卡片 / 表单的平台化（2026-11-14）

- [x] 同档位卡片已收口（2026-11-14）：按"bg-surface + rounded-card + 单色描边"扫掉 67 处 + 我的 AI 小卡 + 集市条目卡。
      剩下的 `bg-surface border border-border` 是 8px 小面板与带 `shadow-2xl` 的弹窗壳，**故意不动**：
      弹窗自己已有投影，再挂 `.card` 会把 `shadow-2xl` 压掉（组件层在后，同属性后者胜）。
      真要继续，先给 8px 面板补一个 `.card-sm`（只换圆角、不换投影）。
- [ ] **约 30 处手写输入框**（`bg-elevated border … px-3 py-2`）→ `.field`，逐处替换。
- [ ] **两套状态配色并存**：`constants.ts` 里 `STATE_BADGE_COLORS` / `STATE_TAG_COLORS` 与新的
      `STATE_CHIP` 三套语义重复；确认没人用旧的之后删掉。

## 🎨 待办：内置更多开源表情素材（2026-11-15）

- [ ] **内置 Twemoji 表情包**（CC-BY 4.0，需署名）：沿用 \`scripts/render_emoji.py\` 的渲染管线
      （128×128 透明底 PNG），打包成第二个内置包（暂名「表情·Twemoji」），包信息里写来源与许可链接。
- [ ] **不内置初音未来等角色表情**：Crypton 的 Piapro 角色许可只覆盖个人非商用创作，
      把素材打进对外分发的产品需另行取得授权；官方 LINE 表情禁止再分发，第三方面板站
      （emoji.gg 等）的图是用户上传、上传者不拥有版权。角色表情一律走「用户自制包」。
- [ ] 备选素材（都要署名）：Fluent Emoji（MIT，质感偏微软）；OpenMoji（CC BY-SA 4.0，
      与我们的包分发方式有冲突，暂不接）。

## 🔌 QQ NapCat 通道：能力扩展（2026-11-17）

> 调研与判据已归档：`docs/dev/napcat_capabilities.md`（接什么、怎么分层）、
> `docs/dev/napcat_account_risk.md`（被踢怎么判、怎么救、养号规则）。

- [x] **出站 CQ 码转义**（安全修复）：模型原文里的 `[CQ:…]` 一律转成字面量。
      出站 `message` 是普通字符串，协议端会把 `[CQ:at,qq=all]` 当指令执行——
      这是提示词注入直达外部动作的口子。平台自己的 @ 在转义之后才由 `_translate_mentions` 拼上。
- [ ] **入站感知**（纯读，零副作用）：`notice` 全量（撤回/戳一戳/表情回应/入退群/群文件/自己被踢）
      + `reply` 段还原引用（现在直接跳过）+ `get_group_member_info` / `get_msg` / `get_forward_msg` 只读工具。
      **notice 落库但不唤醒**（否则群成员进进出出就烧完额度）。
- [ ] **表达力**：引用回复、图片/表情出站、`set_msg_emoji_like` 表情回应、`send_poke` **只回戳不主动戳**、
      `delete_msg` 撤回自己的错话（限自己 + 2 分钟窗，需先接住 `_send_reply` 丢掉的 message_id）。
- [ ] **群管理整层先不接**：禁言/踢人/公告/头衔/设精/加好友审批都是「改外部世界状态」，
      平台现成的 `request_approval()` 只在 world 作用域，群聊回合没有确认通道。
      没有闸门就放行，AI 迟早把别人的群搞乱。
- [x] **不接（已定）**：`send_like` 点赞（刷赞是最高危行为）、`@全体`、伪造 `node`（可冒充他人发言）、
      改头像资料、`set_group_leave(is_dismiss)` 解散群。

## 🗂 消息存储与附件清理（2026-11-18）

设计与全部决策见 [message_storage_and_cleanup.md](./message_storage_and_cleanup.md)。要点：

- [x] 名词统一 + 误会点清理（`file_quota_mb` 显示口径、`STORAGE_QUOTA_PER_AI_MB` 落字段、`_REF_RANK` 过时注释等 8 处）
- [x] 四个字段：`mutable`、`archived_to_message_at`、`last_referenced_at`、`physical_key`（迁移 0120 + 存量回填）
- [ ] 计量按 `physical_key` 去重（修硬链接重复计量 + 清原附件不释放磁盘）
      └ 口径已定：只算「有键＝字节还在」的行。开发库实测虚高主因不是硬链接（只剩 1 组），而是同路径历史版本 29 行 8.0MB，见设计 §5.7.1
- [ ] `/fs/list` 去掉 AI 文件追加、排除已归档；`/user/storage` 收窄为「我的文件用量」
- [ ] 存储页三块并列（我的文件 / 我在对话里产生的 / 浏览过的）+ 墓碑渲染
- [ ] 日写入限额 + 撤回回收附件
- [ ] 清理梯度 L1a-L6 + 占用者排序 + 审计保留下限约束
- [ ] 消息三点菜单：转发、收进我的文件
- [ ] 性能落地：索引清单 + 计量单条去重 + 候选一次分桶 + 批处理（文档 §14）

前置待决：群聊缺 `last_message_at`；磁盘警戒线读法；L6 通知措辞；撤回回收的原子性；内容寻址的引入成本。

## 📌 备注

- 后端遗留 bug 已修复（2026-09-13）：`/chat/messages` 的 dm 分支硬编码 `user_id=0`、`/chat/message` 签名缺 `dm_session_id` —— 两项都随**删除重复入口**一并消失。群消息现在只有一个入口 `GET/POST /gm/{group_id}/messages`，与 `/dm/{session_id}/messages` 同形（带认证、游标分页、返回列表）。插件已改用它，不再需要绕开。
