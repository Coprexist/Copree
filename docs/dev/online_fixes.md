# 线上问题修复记录（与账本设计无关）

> 从 `docs/dev/conversation_history.md` §13 移出：那里只放账本设计的落地进度，与账本无关的线上修复混在里面会打断对照。
> 追加规则：新的线上修复按批追加在文末，写清现象、根因、修法与验证；设计相关的内容仍写回各自的 docs。

## 本轮线上问题修复（2026-09-25，与账本设计无关但同批提交）

- **两个 QQ 通道互相顶掉**（`0cc1184`）：出口注册表按**名字**存且「同名覆盖」，两个 qq-channel 实例都用插件类型 id 注册
  → 后注册的 `agent-8` 顶掉 `agent-24` 的出口，群 64 的 AI 回复被分发到「只认群 65」的 sink 并静默 return
  （现象：Copree 镜像有消息、QQ 收不到、日志干净）。改成**句柄制**（`register_sink` 返回句柄、同名不去重）+
  分发 `asyncio.gather` **并发**跑全部出口；插件用 `ServicePlugin.key`（带实例）注册并存句柄，stop 时精确注销。
  测试 `test_outbound_registry.py`：同名两个出口都要发 / 一个坏出口不拖累别的 / key 带实例。
- **向量维度静默写失败**（`7b50515`）：`models/*.py` 的向量列是 `vector_column(settings.embedding_dimension)`，
  **import 时**取静态值（容器无 `EMBEDDING_DIMENSION` → 默认 1536），而 DB 配置（768）、四个 `vector(768)` 列、
  本地 `nomic-embed-text`（768）三者本来一致 → INSERT 生成 `::VECTOR(1536)` 去转 768 向量必然失败，
  记忆一直写失败重排队（DB 覆盖在 bootstrap 之后加载，管不到已冻结的列类型）。
  修法：compose 补 `EMBEDDING_DIMENSION: ${EMBEDDING_DIMENSION:-768}`；
  **收尾**（`a3fb194`）：`check_dimension_consistency` 自检「ORM 列维度 / 生效配置 / 库里实际列维度」三者，
  `prestart.py` 迁移后调用——不一致 stderr 打 `[ERROR]` + 修法，一致打「向量维度自检通过」。
- **删池 Key 500**（`9005235`）：`api_usage_log.pool_key_id` 外键无 ON DELETE 规则，池 Key 一旦有用量记录就删不掉
  （`DELETE /admin/api-key-pool/1` → ForeignKeyViolationError）。迁移 `b8c9d0e1f2a3` 改 `ON DELETE SET NULL`
  （列本就可空）：用量历史保留、引用置空。
- **池 Key 管理补齐**（`30bdf6c`）：① `PUT /admin/api-key-pool/{id}` 之前**不收 `api_key`** → 密文解不开时没有修法
  （前端只能删了重建），现在支持重填明文；② 新增 `POST /admin/api-key-pool/{id}/test` 测通
  （探测策略/文案/脱敏全复用 `api_probe.probe_provider`，不另写一套）；前端 `ApiKeyPoolTab` 加「编辑」「测通」
  ＋ i18n 三语，并修标签键大小写（`admin.apikeyPool` vs 字典里的 `admin.apiKeyPool`）。
  真机验证：key#1 → `decrypt_failed`；临时 key 指本地 Ollama → `ok`「连接成功，8 个模型可用」。
- **用户已重填池 Key #1**：实测解密 OK（明文长度 35）——③ 收工。

## 本轮线上问题修复（2026-10-04 ~ 10-05）

- **决策技能代发的消息没落库**（QQ 那侧收到、Copree 侧缺了好几屏）：命中规则后走的是 flush → 广播 → 通道出口，
  而出口在调用方 commit **之前**跑；可决策分支与"仅工具调用"分支都是直接 return，那一轮的 session 谁都没提交，
  回滚时消息就没了（群 69 的 messages 里 `2310 → 2312 → 2314` 空着 2311/2313，正是「排行/打劫」两条脚本代发）。
  修法：把提交并进「开会话」——后台轮次统一走 `database.work_session()`（与 HTTP 的 `get_db` 同一份实现：
  出块即提交、抛错回滚），散在各文件里的手动提交一并收回。验证：`tests/test_work_session.py`；
  语义（不是 savepoint，嵌套是两笔独立事务）写进 `app/database.py` 的 docstring。
- **AI 主动 compact 完，下一轮又弹回原文**：手动 `compress_context` 只换了当轮内存里的 messages，没走解锁点
  ——账本没重写，下一轮 `build_messages` 又把原文端回来（群 69 实测：当轮 13 条，下一轮 120 条）。
  修法：解锁整套抽到 `services/history/context_unlock.py`（空闲压缩 / 轮内自动压缩 / AI 主动压缩三处共用），
  手动压缩成功后照样重写账本、复位触发状态、对齐前缀版本与环境；`executor` 只转发。
- **群聊消息列表断成两截，中间那批永远翻不到**（16 分钟前的消息上面直接接 2 天前的）：
  `get_gm_messages(after_id=…)` 取的是「按时间倒序的最新 limit 条」，可它是分页游标——贴游标往后挪时每次都跳到末尾，
  中间那段谁也够不着，返回的还是倒序。修法：`after_id` 与 `before_id` 互为镜像（按 id 升序、贴游标一页）；
  要「最近一窗」的地方（AI 补历史）单独走 `get_gm_messages_after_watermark`；前端改成按 id 归并，
  并在进会话、断线重连两处补齐缺口（`CATCH_UP_MAX_PAGES` 页上限）。
- **私信里调 world_command 报「需要群绑定世界」**（AI 进不了世界）：真因是 `group_id is None`（私信），
  不是群没绑世界——群 59 早在 09-10 就绑了世界 45。修法：新增 `enter_world` 把 (世界, 通道群) 记成状态帧，
  命令的目标群按「离他最近的事实」认（显式 → 候选群里第一个绑世界的 → 世界帧的通道群），一个都没有时如实说
  「你手上没有绑了世界的群」。文档见 [世界 AI 能力](../group_world/design/world_agent_capabilities.md)。
- **同一个会话的状态帧攒成一串**（某 AI 的 `group:59` 三个活帧）：`push_state` 的去重只跟当前帧比身份，
  离开再回来就压新帧，旧的仍留在状态链，摘要把它们当成几段正在进行的事、帧位也被白占。
  修法：认帧认 `context_ref`（同会话切回原帧，**帧 id 稳定**）；不变量兜在唯一写入点 `_save`（重复帧归并）。
  规则表见 [帧生命周期](../dev/frame_lifecycle.md)「同一段会话至多一帧」；测试 `tests/test_frame_lifecycle.py`。

## 本轮线上问题修复（2026-10-27 ~ 10-28）

- **iPad / iPhone（Safari < 16.4）打开任何对话都整页报错**：`Unexpected Application Error! Invalid regular
  expression: invalid group specifier name`。根因不在页面：`remark-gfm` 的依赖
  `mdast-util-gfm-autolink-literal@2.0.1` 用 lookbehind 正则匹配纯文本里的邮箱，而 WebKit 16.4（2023-03）之前
  不支持 lookbehind，正则字面量在构造时就抛 —— 这段转换每次渲染 markdown 都跑，所以是「一打开就白屏」，
  桌面 Chrome 完全正常，很容易被误判成设备问题。修法：删掉那段**冗余**的 lookbehind（同文件 `findEmail()`
  本来就有等价的 `previous()` 逐字符校验，判据相同），由 `frontend/scripts/patch-mdast-autolink.mjs`
  在 postinstall 里幂等打补丁（发布流水线走 `npm ci`，npm 没有 patchedDependencies）。

- **AI 写出来的 SVG 显示成文件卡片，人上传的 SVG 只占 2×2 像素**：① `ai_write_file` 建文件记录时把
  `mime_type` 硬编码成 `text/plain`，而前端只按 `mime_type.startsWith("image/")` 判附件渲染，于是一律走卡片；
  ② 只有 `viewBox` 没有 `width/height` 的 SVG 没有固有尺寸，`max-width:100%` 在内容自适应容器里会绕回 0，
  再叠一层 `block` 包裹就塌成 2×2。修法：新增 `utils/mime.py`（`guess_mime` / `effective_mime`）在写文件与发文件
  两处统一口径，迁移 `0095` 回填历史记录与消息里的附件快照；前端把「是不是 SVG」与尺寸策略收进
  `useAttachmentUpload.isSvgAttachment / attachmentImageSizing`（SVG 最小 120×120、其余 64×64、`object-contain`）。
  验证：线上 `/api/fs/download/79` 返回 `image/svg+xml`；真实私信里 img 盒子 2×2 → 120×120（`naturalWidth` 150）。

- **「你的 API 余额不足」是误报：那把 Key 里存的是占位符**：owner 的全局 Key 实际是 8 位 `Aa123456`，
  接口直接回 401 `Authentication Fails, Your api key: ****3456 is invalid`；而执行器对自身 Key 的失败
  一律写「余额不足」，还同时发「按优先级降级」与「全部失败」两条同义私信——用户查余额、钱一分没少。
  修法：401（钥匙不对）/ 402（钱不够）分开说，通知带上**这把** Key 的末尾四位（短到不像 Key 的占位符不点名），
  文案组装收成一个纯函数 `key_fatal_notice()`（四种收件场景各一条断言），出错那一级发过之后收尾不再补发。
  验证：`tests/test_key_fatal_notice.py`；Key 由用户自行重填，代码不替他补。

- **某个 AI 对某个人一律不回，日志里只剩一行 `Session 已回滚`**：好友关系 6 月就建好了，而那条好友申请
  一直挂着 pending，AI 每轮回私信都顺手 accept 一遍 → 第二次插入撞 `uq_friendship` → IntegrityError 抛在 flush 上
  → 会话进入「必须先 rollback」→ 失败记账再抛一次 → 异常窜到最外层，整轮连 `send_dm` 都没执行
  （`ai_conversation_logs` 里一条记录都没有，所以从现象上完全看不出是好友申请干的）。
  修法：① 好友关系只留**一扇门**（`friend_repo.add_friendship`，创建 AI 那条路也从它走——事故的根就是两扇门
  各判各的），门内用 Postgres 的 `ON CONFLICT DO NOTHING` 让插入本身可重复；② `handle_friend_request`
  失败后自行回滚；③ `ToolRegistry.dispatch` 兜底 `_recover_session`（坏事务就地复原 + 补刷 agent），
  世界侧技能兜底那条路同样兜住；④ 工具错误形状统一成 `{"error": True, "message": …}`——`{"success": False}`
  这种形状会被 `dispatch` 记成成功，工具用量统计一直不准。
  规则写进 [AI 底层服务设计](../ai_service/design/ai_service_design.md) §4.3。
  验证：`tests/test_friend_request_idempotent.py`、`tests/test_friendship_single_door.py`；
  线上当场收到该 AI 的私信回复，那条挂了一季度的申请同时被正确接受。

- **同一处剩下的口子：发给 AI 的好友申请，谁传都能接受**：`accept_friend_request` 只对
  `target_type == "human"` 校验归属，AI 那种目标完全不判——传谁的 id 都行（工具层原本自己补了一道，
  所以没出过事，但服务层这道门是虚的）。判据收敛到 `_may_resolve_request`：收件方本人能接受/拒绝，
  收件方是 AI 时认它自己的账号与它的主人；发起者只能拒绝/撤回，**不能替对方答应**。
  验证：`tests/test_friend_request_ownership.py`（路人拒之、申请者不能自批、AI 与主人可处理）。

- **一次域名解析能把整个 worker 停住十几秒**：`url_guard.is_private_target()` 对域名做真实解析
  （防 SSRF 的既定口径），而它被直接调在 async 的探针路径上；本机解析不到 `example.com` 要
  16.01 秒（实测），于是用户点一次"测试连接"，所有人在等的请求一起卡住；全量测试也跟着慢出 30 秒，
  且随这台机器的 DNS 状况漂移（同一份代码 90 秒 / 124 秒）。
  修法分两步。① 止血：解析拆成 `resolve_host_addresses()`（60 秒缓存，失败也缓存）+ 两个入口——
  `is_private_target` 给同步上下文、`is_private_target_async` 给 async 路径（解析走线程）；
  "已登记私网地址"的收集改用 async 版；测试侧 `conftest.py` 把**公网域名**解析成常量，用例不再
  依赖本机 DNS（124.63s → 71.95s）。② 收口：探针原来"先判一次、再让 httpx 自己拨"，判定与连接
  是两次解析（DNS rebinding 的窗口，也正是安全文档早就写明不许的形状）——把网页取数那套"自己挑地址、
  钉 IP、逐跳复检"提成 `app/utils/outbound_http.py::request_pinned`，取数与探针共用一扇门，
  探针只多传一个"已登记内网可放行"的政策。
  验证：`tests/test_url_guard_resolution.py`（两入口同口径、缓存只问一次、解析期间事件循环仍在转）、
  `tests/test_api_probe.py` 与 `tests/test_mirror_fallback.py` 全绿（探针与取数共用同一条出站管线）。

- **规则用 `{"字段": {"matches": …}}` 写的：校验通过、一条都不中**：反馈是"matches / starts_with / gt
  整批失效，等值和 `_contains` 正常"。查下来运算符都是好的——坏的是叶子形状：`{"content_clean":
  {"matches": …}}` 这种写法从来没进过求值分支（规范形式是 `{field, op, value}`，历史兼容是
  `字段_contains` 这类下划线后缀），它落进"字段等于"的简写，拿字符串和对象比等值，恒假；而校验期只校验
  正则形状、不认这个形状，于是放行。写成死规则的代价比报错大得多：规则看着生效、实际零命中，写规则的人
  只能退化成 `_contains`，正常聊天提一句也会被治掉。
  修法：对象值一律按"一个字段挂多个运算"读（多个为 and）；认不出来的对象值、后缀后接对象、显式 `eq` 比
  对象，三种都在**校验期**拒并给出规范写法；`matches` / `similar` 的入参闸（长度、灾难形状、阈值）
  抽成 `_validate_op_arg`，两条叶子路径共用。写法契约见 [触发组合规则](../dev/trigger_rules.md)「叶子的三种写法」。
  验证：`tests/test_trigger_rules.py`（含对象值求值与三种拒法）、`tests/test_decision_layer.py`
  里"带参运算真命中、正常聊天不被吃掉"这条；库里那条规则不动一个字符就活了。

## 本轮线上问题修复（2026-10-29）

- **AI 存储页打开文件一律 HTTP 500，预览只剩「直接下载」**：`GET /fs/download/{id}` 每次 500，
  日志 `sqlalchemy.exc.MultipleResultsFound`，落在 `file_service.check_file_access` 查归属 AI 那一步。
  原查询是 `select(Agent).where((Agent.id == owner_id) | (Agent.user_id == owner_id))`：`agents.user_id`
  是 AI 自己的虚拟账号，数值会与另一个 agent.id 撞上（线上实例：agent 29 的虚拟账号是 user 49，而文件
  归属的 agent 49 的 id 就是 49），一次命中两行，`scalar_one_or_none` 直接抛异常——权限还没判就先炸了。
  修法：归属解析收成一个入口 `_owner_agent()`——先按主键 `db.get(Agent, owner_id)`，查不到再按虚拟账号
  兜一次并 `limit(1)`；协作者匹配改用 `agent.id`（兜底路径下 owner_id 已经不是 agent.id）。
  顺带查清了兜底查询的到底是什么：存量 15 条记录的 owner_id 是虚拟账号 id（16 → agent 10、41 → agent 25），
  path 与物理文件也落在 `agents/16/`、`agents/41/`，这些文件连 AI 自己都读不到；新增
  `scripts/repair_ai_file_owner_ids.py`（默认 dry-run）一次性归正记录与物理文件，归正后这批记录不再走兜底。
  验证：`tests/test_file_access.py` 四个用例（id 撞车仍可访问、无关用户判拒绝而不是抛异常、旧数据兜底、
  协作者按 agent.id 命中）；把查询改回旧写法时三例失败；`/fs/download/112`、`/fs/download/113` 从 500 变 200。

## 本轮线上问题修复（2026-11-02）

- **AI 改了个名，整条 QQ 通道静默不启动**：用户在 QQ 里 @「鲸鱼娘 Deepseek」得不到任何回应，平台回了
  一句「机器人鲸鱼娘 Deepseek的服务异常，暂时无法响应」；同一时间另一个 AI 的通道照常收发。
  日志只有一行：`QQ 通道[agent-49] 启动中止：Copree 群 #74里找不到名叫「鲸鱼娘 Deepseek」的 AI`。
  根因是通道认 AI 认的是**名字**——`channel.save()` 把 AI 名字写进 `target_agent`，插件启动时
  `_bind_target()` 拿它在目标群里查 `Agent.name`。名字一改（「鲸鱼娘 Deepseek」→「Deepseek 大肥鱼」），
  这行配置就成了死引用；那次后端重启按新名字重绑，`start()` 直接返回 False，**而且失败后没有任何重试**，
  通道从此静默：既没有长连接收消息，也没有注册出口——进站请求根本没发生，所以日志里"干净"。
  修法：**只按 id 认**。`target_agent_id` 由平台保存通道时写入（`managed`，用户看不到也改不了），
  `target_agent`（名字）降级为标签——只写不读，每次启动按 id 把当前名字刷一遍，免得标签过期误导排查。
  存量配置由迁移 `0106` 确定性补齐：实例名一直是 `agent-<id>`（`channel.instance_of`），
  不需要读那份过期的名字；运行时因此没有"按名字找一次"的兼容分支，这类故障从根上没了。
  规则收成一个入口 `app/services/plugin/target_agent.py`（`bind()` / `remember()`），
  qq-channel 与 qq-napcat 共用；平台只在插件声明了 `target_agent_id` 时才写这个字段
  （自带 schema 的三方通道照旧，不会被当成未知配置项）。
  验证：`tests/test_channel_target_binding.py`（改名后按 id 仍认得上、按过时名字认不上、
  AI 不在群里时如实报错）；线上 10 条通道重启后全部「已鉴权，开始收事件」，`agent-49` 的显示名
  在启动时被自动改写成「Deepseek 大肥鱼」，`target_agent_id=49`。
  遗留：`start()` 失败仍然不会自动重试（本次故障的直接原因之一是它）——要不要给通道加"起不来就退避重试"，
  另开设计，不塞进这次修复。

## 本轮线上问题修复（2026-11-05）

- **探针脚本连到生产库，一次按外键闭包清掉 53 张表**（本线自伤，非攻击）：现象是群成员全变成
  `human:90` / `ai:120`（名字解析不到，回落到 `<类型>:<id>`）、管理页打不开。
  根因：为复现 `test_restore_frame_rebuilds_with_the_right_type` 写了个探针脚本，只设了
  `TEST_DATABASE_URL`，而 `app.database` 读的是 `DATABASE_URL`（没设 → 读容器默认值 = 生产库）；
  脚本首行是测试助手 `db_reset.clear("users", "agents", "agent_history_entries", "agent_alarms")`，
  它的语义是「删 roots 及其**外键闭包**」——users / agents / 账本 / 闹钟 / 私信 / 世界聊天 / 兑换码等
  53 张表被清空；`messages` / `groups` / `group_members` 不在闭包里，所以群和成员 id 还在、名字没了。
  恢复：当日 11:00 全量备份 `data/backups/copree_20261105_030012_455382.sql.gz` 恢复到新库，
  核对行数（users 128 / agents 48 / 账本 6332 / dm_messages 3798 / worlds 26 / messages 3833）后
  整库改名切换，旧库留作 `ai_group_dead`；后端重启 healthy。丢的只是 11:00 之后落在被清表里的增量
  （该时段平台几乎无活动，`messages` 与备份计数一致）。
  加固：`backend/tests/db_reset.py` 的 `clear()` 先查 `current_database()`，不以 `_test` 结尾
  直接抛错（`_assert_test_db`）——闸放在助手内部而不是调用点，探针脚本也绕不过去。
  同时确认：`tests/conftest.py` 早就把 `DATABASE_URL` 指到测试库并断言 `_test` 后缀，走测试运行器
  的路径本来是安全的；出事的是绕过 conftest 的裸脚本。

## 本轮线上问题修复（2026-11-03）

- **AI 说"看不到私信"，指的是 B 站私信——它确实回不了**：我先按站内私信查了一轮，还把
  「📂 你的私信会话」清单塞进了群聊路径（每轮约 8 行 ≈ 100 token）；用户指出对象错了，已撤回，
  群聊路径恢复原样（净变化为零）。
  B 站这边的事实：进「刷 B 站」帧时 `on_frame_enter` 会把站内的回复 / @ / 未读私信念一遍，
  但工具白名单里没有任何能往 B 站送字的路（`bili_comment` 只能评视频）。
  修法：新增 `bili_dm` 一个工具（`list` 列会话 / `read` 读最近几条 / `send` 真发），
  帧白名单只加这一个名字；`send` 走 `web_im/v1/web_im/send_msg`（`api.vc.bilibili.com`，
  表单是 `msg[...]` 那一组，CSRF 仍用 `bili_jct`，`dev_id` 取浏览器身份的 `_uuid`）。
  认人先认**已有会话里的昵称**，全站搜人只认完全一致——按昵称把私信发给陌生人是收不回的错。
  三道闸：`allow_interact` 没开不发、不给自己发、正文 ≤ 500 字。
  验证：`tests/test_bilibili_dm.py` 5/5（全部打桩，不往真人发）；真发送等用户自己发一条后看日志。
  见 [B 站通道](bilibili_channel.md) §3.4。

## 本轮线上问题修复（2026-11-07 夜：重启慢，根因在"停止"）

- **重启要 15 秒才回来，而且每次都没有优雅关闭**：`docker restart` 调用本身 15.08s，其中 start→就绪
  只有 4.9s，剩下 **≈10s 纯粹在等停止**。日志上最后一条业务行之后 9.8 秒直接是新容器的 prestart，
  中间**没有任何** `[STOP]` / `Shutting down` —— 那是被 SIGKILL 硬杀的。
  根因：`docker-compose.yml` 的启动命令是 `sh -c "python prestart.py && uvicorn ..."`，**没有 `exec`**，
  所以容器 PID 1 是 `sh`；`docker stop` 的 SIGTERM 打在 sh 上，而 `sh -c` 在等前台子进程时不转发信号，
  uvicorn 全程没收到关闭信号，Docker 等满默认 10 秒超时后 SIGKILL 整个容器。
  代价不只是慢：每次重启都在硬杀正在跑的回合（账本/会话缓冲可能不落盘）。
  修法三处：
  1. compose 加 `init: true` + 命令里加 `exec`：tini 当 PID 1（回收 chromium/常驻世界的子进程、
     转发信号），uvicorn 取代 sh 成为它的直接子进程，SIGTERM 直达；
  2. `prestart.py` 加**迁移指纹快速路径**：迁移集合（文件名+大小+mtime）没变过就跳过
     `alembic upgrade head`（原来每次都空跑 1.5~2s）；指纹存 `data/.alembic_applied`，读不出/写不进
     就退回每次都跑。顺带每行带"时刻 + 相对耗时"并 `flush`——stdout 是块缓冲，"执行迁移"与"迁移完成"
     原来只差 29 微秒，那是假的（多行一起 flush），docker 的外部时间戳不可信；
  3. 向量维度自检从 prestart 挪到 bootstrap 的**后台任务**（失败也不阻塞启动），启动耗时改成无条件打
     `[OK] 服务就绪（启动耗时 X.Xs）`（以前只在 maintenance 状态切换时打，正常重启看不到）；
  4. 关闭流程拆成 `_shutdown_step(name, factory, budget)`：每步限时（总预算 ≤ uvicorn 的
     `--timeout-graceful-shutdown 5`）、计时、失败只告警——某一步卡住不再把整体拖到被硬杀。
  验证（同一台机，重启前后对比）：

  | | 修复前 | 修复后 |
  |---|---|---|
  | `docker restart` 返回 | 15.08 s | **6.18 s** |
  | stop 阶段 | ≈10 s（SIGKILL，无日志） | **1.0 s（优雅）** |
  | 关闭日志 | 无 | `Shutting down` + 6 步逐步计时（停插件 0.68s，其余 0.00~0.02s） |
  | 启动到 HTTP 可用 | 4.9 s | 4.9 s（导入 3s + bootstrap 1.7s；prestart 从 2.1s → 0.05s） |



## 本轮线上问题修复（2026-11-08 夜：QQ 登录卡上的二维码永远是"已过期"）

- **现象**：qq-napcat 通道卡片给出的二维码，手机一扫就提示「二维码已过期」，
  扫多快都一样；换手机、换网络也没用。用户 2026-11-08 23:36 报障。
- **根因（三层叠加，任一层都足以让人扫不成）**：
  1. **图本身是死的**：`/app/napcat/cache/qrcode.png` 的 mtime 停在 `2026-10-30 11:19:06`
     （600 字节），到报障时已 9 天 12 小时没更新。QQ 的扫码授权码只有**约 2 分钟**寿命
     （协议端日志里每出一张码，正好 2 分钟后一条 `ErrCode: 3`，那就是到期换码）。
  2. **插件无条件转发这张死图**：`backend/plugins/qq-napcat/plugin.py:204-208` 只要
     `qrcode.png` 存在就 base64 塞进 `qr_png`，顺手把 mtime 写进 `qr_at` 就完事；
     卡片上那条可复制的授权链接走 `_qr_url_from_log()`（同文件 :172），抓的是
     `napcat.log` 里**最后一条** `https://txz.qq.com/p?k=...`——同源，同样过期。
  3. **前端没有过期判断**：`frontend/src/components/channels/ChannelCard.tsx:844-863`
     只判 `qr_png` 存不存在，存在就渲染成"请扫码登录"；`qr_at` 在 :512 声明了类型，
     **全文件再没用过**。所以 2 分钟前的码和 9 天前的码，界面上一模一样。
- **为什么出码会停**：日志里 10-30 的 11:10:59 / 11:13:02 / 11:15:02 / 11:17:04 / 11:19:06
  是正常的 2 分钟轮转（`ErrCode 3` = 到期换新码）；`10-30 20:45:31` 出现
  `[Core] [Login] Login Error,ErrType: 1 ErrCode: 1`——**登录会话本身异常终止**，
  之后出码任务再没跑过。`/opt/QQ/qq`（PID 148）进程还活着，但停在登录界面不动；
  容器 uptime `10-16:58`，确认中途没重启过，与"最后一张码 10-30 11:19"对得上。
- **现场处置**：`docker restart napcat`。23:42:48 出第一张新码，23:44:50 轮转第二张
  （先一条 `ErrCode 3`，再一条"二维码已保存"），轮转恢复。
- **验证**：`docker exec napcat stat -c '%y' /app/napcat/cache/qrcode.png` 的 mtime
  `2026-10-30 11:19:06` → `2026-11-08 23:42:48` → `23:44:50`；日志里重新出现
  2 分钟一轮的 `ErrCode 3` + 新码。注意这只是救活了轮转，**没改一行代码**，
  下次 `ErrCode 1` 时还会冻住，卡片还会把旧图当新图给人扫。
- **根治（2026-11-08 当日做完）**：方向是用户提的「不要让卡片默认就把码摆出来，
  改成用户点一下再显示刷新过的码」。判定只留一处，两边都只是"照判定画"：
  1. `plugin.py` 新增 `_qr_state(mtime, now)`，只产出三种判定，阈值全是常量
     （`QR_TTL=125` / `QR_MIN_SCAN=45` / `QR_STALL=300`）：`ready`（够新鲜，
     剩 ≥ 45 秒才肯给）、`waiting`（这一张废了，或在等第一张）、`stalled`
     （年龄超过 `QR_STALL`，说明出码轮转本身停了——正常轮转永远到不了这一步）。
     `_hosted_login` 不再无条件端图：`qr_state` / `qr_expires_in` 每轮都报，
     **只有 ready 才带 `qr_png` / `qr_url` / `qr_at`**；正好卡在 stat 与 read 之间
     被轮转替换掉的那一次降级成 `waiting`，既不抛错也不端旧字节。`qr_url` 仍取自
     日志最后一条，与 png 同源、同一有效期，所以跟着同一个判定走。
  2. `ChannelCard.tsx` 默认只画「获取登录二维码」按钮，点了才展示；展示时带剩余秒数
     倒计时（≤ 15 秒转 amber），`stalled` 时给一行可复制的 `docker restart napcat`
     和「重启好了，重新取码」。**不用新接口、不加长轮询**：卡片本来每 5 秒重拉一次，
     判定翻成 `ready` 的那一轮图自然出现——等待上限就是协议端的一次轮转（约 2 分钟），
     而平台始终不用碰 docker。三种语言文案同步。
  顺带纠正一句旧文案：`hostedScan` 原来写"码会自动轮换，过期不用管"，那是把故障说成
  特性；现在只写首扫必须在手机上确认，寿命交给倒计时显示。
- **验证**：`backend/tests/test_qq_napcat_qr_freshness.py` **6/6**（纯逻辑边界 + 假缓存
  目录验证 payload：ready 才带图、stalled 一个字节都不给、已登录直接短路）；
  容器内实跑 `_hosted_login("", "")` 返回
  `{"qr_state": "ready", "qr_expires_in": 102, "qr_png": ...}`；
  前端 `tsc --noEmit` 无错，vite HMR 已热更 ChannelCard 与 i18n。


## 本轮线上问题修复（2026-11-09：点了转发、界面说成功、对方那儿没有这条消息）

- **现象**：用户在私信里点「转发」把一份 md 发给两个联系人，弹窗显示"已发送"，
  被转发方的聊天记录里既没有文件、也没有这条消息。2026-11-09 19:47 报障。
- **根因（两处，一处让消息消失，一处让界面撒谎）**：
  1. **后端 500，整个发送事务回滚**。日志：
     `2026-11-09 07:33:57 POST /dm/1_67/messages → 500` 与 `/dm/1_12` 同一毫秒，
     异常为 `IntegrityError`：`Key (file_id, referrer_type, referrer_id)=(183, human, 1)
     already exists`。设计文档《文件存储与协作系统》§5.3 定的是**同一引用方对同一文件
     只留一条记录、重复引用只更新 ref_type**，所以 `file_references` 上是
     (file_id, referrer_type, referrer_id) 唯一约束。但 `track_forward_reference` 只查
     "有没有 forward 行"，查不到就自己 INSERT：用户 07:33:25 预览过这份文件
     （下载/预览接口会落一行 read），07:33:57 再转发就撞唯一键。而 `dm.py` 是先
     `db.add(msg); await db.flush()` 再记引用，事务一脏连消息一起回滚——"显示发成功"
     与"没有这条消息"于是同时成立。
  2. **前端只报成功**。`ForwardFileModal` 只有一个 `done` 布尔，`setDone(true)` 无条件执行，
     底部文案直接 `forward:sent`；`succeeded/failed` 算出来只用于决定要不要自动关闭。
     后端 500 → `failed=2` → 弹窗停在那里显示"已发送"。
- **修法（回到文档的幂等规则，不再各写一套）**：
  - `track_forward_reference` 不再自己 INSERT，委托给 `track_file_reference(..., 'forward')`
    ——那条函数本来就按 §5.3 实现"查一行 → 有就更新 ref_type，没有才建"。
  - 顺带补上文档没提到的反向情况：新增 `_REF_RANK`（read < import < write/share < forward），
    `track_file_reference` 只在**更强**时才改 ref_type。否则转发之后再预览一次
    （预览写 read）会把 forward 降级成 read，等于把已占用的配额悄悄还回去。
  - 前端把 `done` 换成 `result = {ok, fail}`：全成功才说"已发送"，有失败就标红报
    「{ok} 个已发出，{fail} 个失败——失败的那边不会有这条消息」，且不自动关闭。三语同步。
- **验证**：`backend/tests/test_forward_reference_idempotent.py` **4/4**（预览后转发不再炸且
  就地升级 / 转发后预览不降级 / 重复转发幂等 / 自己的文件不记引用）；
  `test_sandbox_layers` 14/14、`test_qq_napcat_plugin` 24/24 无回归；前端 `tsc --noEmit` 无错。
- **现场无需清理**：失败的那条消息本来就没落库（事务回滚），不存在"半条"记录；
  修好后重新转发一次即可（用户 1 对文件 183 的那行引用会从 read 就地升级成 forward）。


## 本轮线上问题修复（2026-11-09 夜：二维码卡片一直停在"正在申请"）

- **现象**：点了「获取登录二维码」之后卡片一直显示"正在向 QQ 申请新码…"，不变成图。
- **根因（我自己定的判停窗口太松）**：NapCat 的轮转是健康的（22:48:47 / 22:50:50 /
  22:52:52 / 22:54:54 / 22:56:56，每 122 秒一张），22:57:40 用户扫码，
  22:57:42 出现 `Login Error, ErrType: 1 ErrCode: 5`——**扫码确认没通过，出码轮转也就此停了**，
  `qrcode.png` 冻在 22:56:56。此时文件年龄一路涨：
  - 年龄 ≤ 80 秒：`ready`（能给码）
  - 80～125 秒：`waiting`（这张扫不完了，等下一张）——合理
  - **125～300 秒：还是 `waiting`**——这就是用户看到的"一直在申请"
  - > 300 秒：才判 `stalled`
  而健康的轮转每 122 秒必换一张，**年龄根本不可能正常涨到 300**：那 175 秒是完全多余的
  空转窗口。用户正好落在里面，界面就一直转圈。
- **修法**：
  1. `QR_STALL` 300 → **150**（QR_TTL + 25 秒抖动余量）。判停依据没有变——
     仍然是"文件年龄"，因为健康轮转下年龄上限就是一张码的寿命；只是窗口不再长过两轮。
  2. 等待态显示已等秒数（`已等 {s} 秒`），把"在等"和"卡住"区分开：
     转圈不动和秒数在走是两件事，后者看完也知道该去重启了。三语同步。
- **验证**：`test_qq_napcat_qr_freshness` **6/6**（新增断言：`QR_STALL` 必须小于两轮，
  且 `QR_STALL-1` 仍算 waiting、`QR_STALL+1` 判 stalled）；`test_qq_napcat_plugin` 24/24；
  前端 `tsc --noEmit` 无错；`docker restart napcat` 后 6 秒出码（23:02:01），轮转恢复。
- **顺带记下 `ErrCode` 的含义（多次线上观测）**：`ErrCode 3` = 这一张到期，正常换码；
  `ErrCode 1` = 登录会话异常终止；`ErrCode 5` = 扫码后被拒/取消（确认失败），
  后两者都会让出码轮转停掉，只能重启协议端。


## 本轮线上问题修复（2026-11-09 深夜：二维码交互定稿）

用户对二维码的要求最终收敛成四条，前两版实现（"无条件端图"、"点了再等一张"）都不对：

1. **默认不摆码**，只有一个「获取登录二维码」按钮；
2. **点一下才向协议端要**一张，拿到后出图 + 倒计时；
3. **过期不自动续**：已经显示的那张不会因为协议端换了新票就被顶掉，不点就一直是那张；
4. **刷新按钮常驻、想刷就刷**，但一分钟最多 3 次——平台万一算错，用户自己也能拉一次。

外加：过期**不收起**那张码（用户可能正扫着，收起会让人以为要重来），只压暗并标「已过期」。

- **后端**（`plugins/qq-napcat/plugin.py`）：判定从"够不够新鲜"改成"这张还能不能扫"，
  `ready/waiting` 改名 `live/expired`，语义与前端一致；`live` 才带 `qr_png`/`qr_url`，
  并把剩余秒数 `qr_expires_in` 一起给（前端倒计时用）。`expired` 表示这一张到期、等下一张，
  `stalled`（年龄 > `QR_STALL`=150）才是出码轮转停了、只能重启协议端。
- **前端**（`ChannelCard.tsx`）：码存成快照 `{png, url, expiresAt}`，
  **只在点击时写入**；只有"这次点击触发的等待"会自动采纳新码，已经在显示的那张不会被轮换顶掉。
  倒计时按快照自己的 `expiresAt` 算，不看轮询。刷新限流用 60 秒滑动窗口（最多 3 次），
  冷却期间按钮显示"{n} 秒后可再刷新"。等待态显示"已等 {n} 秒"，
  停摆时直接给可复制的 `docker restart napcat`。三语文案同步。
- **验证**：`test_qq_napcat_qr_freshness` **6/6**（live 给剩余秒数、过期判 expired 不判 stalled、
  越过 `QR_STALL` 才 stalled、缺文件是 expired 不是转圈、payload 只在 live 时带图、已登录短路）；
  容器内实跑 `_hosted_login("", "")` → `state=live expires_in=27 has_png=True`；
  前端 `tsc --noEmit` 无错，`i18n:check` 退出码 0（2274 个调用点全部可解析）。

## 2026-11-10 深夜 · NapCat 登录改走接口，协议端按实例拆开

**症状**：卡片上的二维码剩余时间与实际不符；协议端一重启，码要么不刷新要么显示死图；
两个 AI 想各接一个 QQ 时，谁也不知道该读哪个目录。

**根因**：整个登录链路靠读协议端的磁盘（`qrcode.png` 的 mtime、`napcat.log` 里最后一条
`二维码解码URL`），而这些路径来自三个**部署级**环境变量——天然只够服务一个协议端。
文件年龄也不等于码的年龄：协议端轮换后旧文件还在，读出来的剩余时间是假的。

**改法**：
- 插件内置 `NapCatWebUI` 客户端：`sha256(token + ".napcat")` 换 Credential，之后 Bearer；
  凭证失效只重换一次。用它做三件事——问状态、要一张新码、认当前登录的号。
- `_login_progress()` 成为登录状态的唯一出口：未登录只报 `login_phase`，**不给码**；
  用户点过之后才报 `qr_state` / `qr_expires_in` / `qr_url` / `qr_png`（由 `app/utils/pure/qr.py`
  本地渲染）。码的时间戳记在内存（`_QR_ISSUED`，按协议端地址分开），换码即重新计时。
- 新增 `POST /me/agents/{id}/channels/{plugin_id}/login-qr`：实例存在就用实例自己的协议端，
  不存在就用部署默认的那一个——卡片在"还没保存配置"时也能扫码。
- 实例字段 `webui_url` / `webui_token` 上线；`ws_url` / `access_token` 去掉 `managed`；
  三个部署级 env 降级为默认值。
- compose：napcat 去掉 `container_name`，数据目录改按 `NAPCAT_SLOT` 分槽，控制台端口
  只绑 `127.0.0.1:6099`（`NAPCAT_WEBUI_PORT` 可改）。

**验证**：`test_qq_napcat_qr_freshness` **9/9**（未点不给码、点后 122 秒、第二次点重置时钟、
已登录不出码且认号、控制面错误变成卡片上的错误而不是 500、未配置返回 None）；
容器内实跑 `refresh_login_qr()` → `qr_png` 1980 字节、`qr_expires_in` 122，再查状态为 `live` 121；
`POST .../login-qr` 未带鉴权返回 401（路由已注册）；前端 `tsc --noEmit` 与 `i18n:check` 均通过。

## 2026-11-10 深夜 · 协议端控制台反代（/qq-napcat/<实例>/）

**需求**：用户要能在平台里看到自己那台协议端的控制台并操作，但那个面板能改协议端的一切
（换 QQ、改 WS、装插件），不该暴露到公网，也不该让每个人手里有一份 webui token。

**做法**（`app/routers/qq_napcat_panel.py`）：
- `POST /me/agents/{id}/channels/{plugin_id}/panel` 发一张一次性票据（HMAC，绑实例 + 用户 + 1 小时）。
- `GET/POST /qq-napcat/{instance}/{rest:path}` 反代：票据换成 Path 限定在该实例下的 HttpOnly
  cookie，之后的 js/css/SSE 请求浏览器自动带上——iframe 里的子资源带不了 Bearer，这是唯一干净的解法。
- 静态资源在反代时改写 `/webui/` 前缀；`/api` 是运行时拼的字符串，改不了，注入一小段 shim
  在浏览器侧统一加前缀（升级换了 chunk 名也不用跟着改）。
- 平台侧自己补 `Authorization`（webui token 换 Credential），用户全程看不到 token。

**验证**：票据签名 / 实例绑定 / 过期三条各自为真；把真实 `/webui/` 的 HTML 过一遍 `_rewrite` →
8 处资源前缀正确、shim 已注入；未带鉴权 `POST .../panel` → 401，坏票据 → 403；
前端 `tsc --noEmit` 与 `i18n:check` 通过。卡片上「打开协议端控制台」走新标签页。

## 2026-11-10 深夜 · 「我的」页的账号区块 + 一键拉起

**需求**：记录哪个 AI 是哪个号；之前扫过码的号掉线（别处登录被顶）能点一下就拉起来；
用户在自己的页面上看到登录的账户，并能打开它的面板。

**做法**：
- 插件：登录成功时把 uin/nick/avatar 记到 <数据根>/plugin_data/qq-napcat/account-<实例>.json
  （值不变不写盘）；被顶下线后协议端问不到 uin，就靠这份记录继续认号。
  新增 `login_account()`（平台词汇 uin/nick/avatar/logged_in/online）与 `quick_login()`
  （`SetQuickLogin` → 轮询 3 秒 → 还不回来就发新码）。
- 控制面客户端收敛到一个 `_webui()`：运行中用 `start()` 读到的地址，没跑起来读已保存的配置
  （卡片扫码与我的页看账号都发生在实例没跑起来的时候）。`panel_client()` 因此改成 async。
- 平台：`GET /me/channel-accounts`（`routers/channel_accounts.py`）只问"我名下且配过通道"的实例；
  `POST /me/agents/{id}/channels/{plugin_id}/relogin` 走 `channel.relogin()`。
- 前端：「我的」页新增账号区块（头像 / 昵称 / QQ 号 / 在线、重新登录、打开面板），
  扫码弹窗只在快登拉不回来时出现，文案不写倒计时数字（过期就"换一张"，不猜剩余秒数）。

**验证**：`test_qq_napcat_qr_freshness` **13/13**（新增：被顶下线仍报出记住的号、值不变不重写、
两个实例各记各的、没有号时不落盘）；容器内实跑 `GET /me/channel-accounts` → 200 `{"accounts":[]}`
（本部署还没有 qq-napcat 实例，空列表即正确），未带鉴权 → 401；`POST .../relogin` 对未配置的
AI → 400「这个 AI 还没配置通道」；前端 `tsc --noEmit` 0 错、`i18n:check` ✓ 2286 个调用点全部可解析。

**没做**：平台内"退出 QQ 登录"。NapCat 控制面没有退出接口（把它的前端包翻了一遍，
与登录相关的只有 CheckLoginStatus / RefreshQRcode / GetQQLoginInfo / SetQuickLogin / RestartNapCat，
GetAllUsers 前端在调但服务端是 404），硬做只能重启协议端再赌 QQ 客户端不自动恢复会话——
不是可靠语义，所以先不提供这个按钮。

---

## 2026-11-11 · 缓存命中率的主漏点：环境快照按帧存，换帧就重写上下文头部

**现象**：10-20～11-10 共 287 轮，全站命中 90.3%。按「message 0 是否与上一轮同会话逐字节相同」分组：
相同 97.2%（占总 miss 17%）、不同 78.6%（占 64%）、新会话首轮 83.7%。轮内多轮工具调用反而是好的
（6～8 次调用那些轮次落在 88%～98%），说明「轮次读数只往末尾追加」那次修复有效，miss 的大头在别处。

**根因**：写进锁定前缀的环境快照（`env_locked`）存在状态帧上，而环境是**这段会话**的事实。
同一段会话会被反复开新帧——agent 1 有 174 个帧，其中 167 个都是 `group:66`，这 167 帧的
`member_num` 有 11 种取值、`full_mode` 两种。新帧没有基线时 `environment_turn` 走
`if not established` 分支，用当前值重建基线：头部变字节，整段前缀作废，一轮白赔一次全量 prompt。
实测 14375→14383 相隔 17 秒，账本里 8 条 `【环境变化】` 一字不差、452 条消息中 437 条逐字节相同，
只有 message 0 从「13 人 仅 @ 唤醒」变成「21 人」；那一轮 51,515 token 全按未命中计费。

**修法**：`read_frame_env` 在当前帧没有基线时，沿用同会话（同 `context_ref`）最近一帧的那一份——
与 `frame_roster` 早就采用的规矩一致（「换了个帧实例不该把这块字节改写一遍」）。
写入仍只落当前帧；解锁点（`apply_environment`）照旧把锁定态对齐现值；环境真变了仍然只追加一条
账本通知，通知是账本条目、只付一次。

**验证**：新增 `test_a_new_frame_instance_inherits_the_locked_environment`（关帧 → 同会话重开 →
读回旧锁定值、变更只追加通知）；`test_plugin_environment_sync` **7/7**，环境与状态帧相关 7 个套件
**78/78**；后端重启后 `/health` ok，容器内确认新代码已加载。

**遗留**：qq-channel 的 `environment()` 仍会时有时无地少报 `full_mode` / `member_num` 键
（`if num:` / `if mode is not None:`），账本里会多出交替的「环境变化」条目。它已不影响头部，
但要根治得让键集合稳定（缺值按默认渲染），或让 `canonical()` 把「缺键」与「默认值」视作同一件事。

**后续（2026-11-11 18:00）**：真正让帧每轮重建的不是环境，而是 ensure_active_frame 只认链上帧。
这个会话的帧一旦被弹出链（他调 pop_state，实测每个群帧只活约 5 秒），下一条消息进来时找不到链上的帧，
就走新建分支——新帧没有锁，才轮到"拿当前环境重建基线"。见下一节。

---

## 2026-11-11 晚 · 出链的会话帧被唤醒时拉起原来那条

**现象**：同一段群会话每来一次消息就攒一条帧（agent 1 的 group:66 攒了 167 条），每条帧都没有环境锁，
于是每轮都重建基线、头部字节变化、整段前缀按未命中计价。

**根因**：ensure_active_frame 只认**链上**的帧（running() = active/paused/suspended），帧被 pop_state
弹成 ended 之后就不再被认，于是走 make_state_frame 新建一条。而这条路径恰恰是大多数唤醒的来源：
私信、群聊都是外界消息触发的。

**修法**：没有链上帧时，先按 context_ref 去存储里找那条帧（出链的、待交接的都算），把它拉起
（status=active，_save 对账上链并归位到链尾），帧 id、环境锁、帧清单、便签副本、原文尾巴原样还在；
只有这段会话从没建过帧才新建。触发来由写进 why：「由于外界环境改变，替他把帧拉起」。

**表述与措辞**：删掉「已结束的帧不复活（那段上下文已经重新开始了）」这类说法——出链只是它这会儿没在跑，
不等于这个状态没了；消息账本按会话连续、不随帧结束重开。全仓「运行集合」统一成「状态链」
（代码 65 处、文档 21 处），逐句改过句读：链本身仍叫状态链，running() 的措辞改成「链上」，
不出现「状态链 = 状态链」这种同义反复。

**验证**：新增 test_frame_lifecycle::test_the_same_conversation_revives_its_own_frame（关掉会话 → 消息再来 →
帧 id 不变、why 记外界环境改变、环境锁仍在）；八个相关套件 **101/101**。

**没做**：对话日志按帧 id 归堆。日志表现在只存 state_key（type + label 身份），要按 id 得加列，
并把帧 id 一路传到 executor 的七个保存点（ai/executor.py 的 _save_conversation_log_safe 调用）——
那属于主链路上的另一次改动，留到下一轮。

---

## 2026-11-11 深夜 · 帧身份落到 context_ref：认帧一个口，存量重复帧由迁移归并

**现象**：上一节的修法让唤醒不再新建帧，但"一段会话至多一帧"仍靠三处各写一遍的挑选规则维持：
`ensure_active_frame` 里两段 `next(...)`、`read_frame_env` 里一段 `reversed` 扫描、插件
`enter_frame` 自己的 `_frame_with_ref`。三处规则一致时看不出问题，要改（比如将来一个环境多个帧）
就得记住改三处。存储里那 167 条 `group:66` 也一条没消——每次读帧数组都要扫过历史区，而"这段会话
是哪一帧"只能靠读的时候挑。

**修法**：

- 认帧收成一个口：`pure.frame_of(stack, context_ref)`——在跑的优先，都结束了取最近激活的那条。
  切会话、`push_state`、插件代建、进世界全走它；只有这段会话从没有过帧才新建。
- `read_frame_env` 退回只读当前帧：环境挂在会话那一帧上，缺就是缺。旧版本"锁在更早那条帧上"的
  存量形状由迁移一次搬到现帧，读路径不再为它留分支（兜底留在读路径上，等于每次读都为一段已经
  修好的历史付费）。
- 迁移 `0109_one_frame_per_context`：同一 `context_ref` 只留一帧（规则同运行时归并表），被并帧上
  还有内容的字段（环境锁、便签副本、待告知、原文尾巴、调用计数）并进幸存者；指向被并帧的长期指针
  改到幸存者（`agent_alarms.frame_id`、`agent_state_chain_node`），再删掉那几条帧。归并规则
  内联在迁移里、不 import 应用代码——已经发布的迁移不能跟着以后的重构改行为。
- 待交接（`retired`）的帧也能被拉起：它是"平台让他收尾"，不是"这段会话没了"，复活不动记录，
  后事照旧由 `pending_notices` 投递。

**未来**：一个环境多个帧（代理决策挑醒哪一帧以提高专注度）会放开这条不变式，身份键要重新设计
（`context_ref` + 焦点/用途）。认帧仍走一个口，迁移没有往库里加唯一约束，放开时不必先拆。
记在 [计划与闹钟](plan_and_alarm.md) §七。

**验证**：`test_frame_lifecycle` 新增 `push_state` 复活用例；`test_frame_storage_migration`
4 条（归并/挑幸存者/可重跑/不串会话）；`test_plugin_environment_sync` 旧的"继承"用例改成
"只认当前帧"的契约用例。九个套件 **106/106**。

**另**：`test_schema_contract` 的时区列守卫仍红——`agent_experiences` 的 `created_at` /
`started_at` / `ended_at` 声明成带时区（未提交的 `0103` 那一批带的），与本次改动无关，未动。

## 2026-11-13 · NapCat 的账号记录落进插件自己的表（`plugin_qq_napcat__accounts`）

**现象**：上一轮（2026-11-10 那条）把"这个实例挂过的号"写成
`<数据根>/plugin_data/qq-napcat/account-<实例>.json`。文件能读能写，但它是平台看不见的一摊数据：
卸载插件时"保留数据"只能留一个目录，管理台盘点不到行数，备份也不含它。插件产出的数据本来就该
归插件自己的表——这是插件存储约定（[插件存储](../plugin-dev/plugin-storage.md) §5）的验收点。

**修法**：

- 迁移 `plugins/qq-napcat/migrations/0001_accounts.py`：一实例一行（`instance` 唯一键），
  `uin/nick/avatar/updated_at`。**不搬老文件**：写文件的那版代码从未上线（线上没有该目录、
  仓库里是未提交的中间版本），没有数据要搬；为一摊不存在的数据让迁移长期依赖平台目录布局，
  比"真遇到了重新扫一次码"更贵。迁移自包含、不 import 应用代码，这条也写进了存储约定的规则。
- 分层：`tables.py` 只管列声明（`PluginTables("qq-napcat")`），`accounts.py` 只管读写
  （`load_account`/`save_account`），`plugin.py` 只剩协议与视图，不再直接碰表。
- "值没变就不写"从"读—比—写"改成一条 `INSERT … ON CONFLICT … DO UPDATE … WHERE 有列不同`：
  一次往返、原子，同值重存零写入（空 UPDATE 只会添死元组）。条件用 `!=`，两种方言都认。
- 平台补 `PluginTables.insert(table)`：按当前库给 insert 构造器（PG / SQLite 的 upsert 语法同形、
  构造器不同）。插件写 `T.insert(...)` 就不会被钉在 PostgreSQL 上——安装包是 SQLite。
- 静态守卫的口径沿用：插件目录不在 `test_sql_dialect_guard` 的扫描范围内，所以"方言只出现在
  平台层"这条靠 `T.insert` 这类入口保证，而不是靠每个插件自己记得。

**顺手修的**：`storage.tables_of` 用 `LIKE 'plugin_<id>__%'` 找表，而 LIKE 的 `_` 是单字符
通配——id 前缀相同的兄弟插件（如 `qq-napcat` 与 `qq-napcat-probe`）的表会被算成自己的，
`purge` 时连它们一起 DROP。改成先取候选、在 Python 里比精确前缀，加了一条用例。

**验证**：`test_qq_napcat_tables` 1/1（tables.py 列集合 == 库里实际列集合）、
`test_qq_napcat_qr_freshness` 13/13（新增"同值重存不动 updated_at"）、
`test_qq_napcat_plugin` 18/18、`test_plugin_storage` 7/7、`test_plugin_tables` 4/4；
另有 `storage.sync_all()` 走生产启动路径的冒烟（迁移到位、盘点能认出该表）。

## 2026-11-13 · 通道身份目录归平台：一个模块、一张表、插件不直接查

**结论**：`external_identities` 不搬进插件表，留在平台，并且明确成"平台给所有通道的一张表"。
理由记在这里，免得下次又按"谁写的归谁"想回去：读它的是平台自己（通道卡片要列待批 / 已放行 /
拉黑名单，批准、拉黑、解除配对都在平台侧），每行的 `user_id` 指向 `users` 里的锚点账号，
而消息、群成员、私信会话都挂 `users.id` —— 表跟着插件走就是把"谁是谁"切成两半：卸载清数据之后
消息仍指向那个锚点，重装时同一个人会在群里变成两个"同一个人"（2026-10-18 真机那次事故的另一种
走法）。判据改成"丢了之后会不会破坏平台的不变式"：NapCat 的账号 / 二维码丢了只是重扫，是插件
自己的表；身份目录丢了会分家，归平台。

**改法**：

- 模块归位：`services/plugin/pairing.py` → `services/channel/identity.py`，
  `services/plugin/channel_user.py` → `services/channel/anchor.py`。放在 `plugin/` 下读起来像
  "插件的私有数据"，实际是通道域的平台能力；行为不变，只换归属与命名。
- 收口插件直连：qq-channel 原来自己 `select(ExternalIdentity)` 四回（有没有这一行、绑的账号、
  跨应用标识、@ 翻译），全换成 `identity.has / user_id_of / union_id_of / contacts`；插件里
  不再出现身份模型。NapCat 一直走服务，这条只是把两边拉齐。
- 契约测试 `test_channel_identity.py`：四个只读口径各答各的（缺行给 `False/0/""/{}`，不返回
  别的实例的行）；插件目录里出现 `ExternalIdentity` 或裸 SQL 查 `external_identities` 直接红。
- 文档同步：`agent-channels.md`、`plugin-dev/channel-plugins.md`（§0 写明表归平台、插件只经
  服务读写）、`dev/mention_ids.md`、`dev/conversation_history.md`、`dev/channel_identity.md`
  里的旧模块路径。

**验证**：`test_channel_identity` 2/2、`test_channel_pairing` 18/18、`test_qq_channel_plugin`
77/77、`test_qq_napcat_plugin` 18/18（合计 **115/115**）；容器内 `import app.main` 通过（无循环
依赖）。仍是工作区改动，未提交、未重启。

## 2026-11-13 · 协议端控制台反代改成通用契约（核心不再认识 qq-napcat）

**现象**：`app/routers/qq_napcat_panel.py` 里 4 处硬编码（`_PREFIX="/qq-napcat"`、
`_PANEL_PLUGIN="qq-napcat"`、`_SHIM`、`_rewrite` 的 `/webui` 口径）。票据接口其实早就按
`plugin_id` 走、前端也一直用 `plugin_id` 拼，只有反代那一段是"平台替某个插件写的"——
再加一条带控制台的通道就得把 190 行抄一遍。

**修法**：

- 路由换成通用的 `/channels/{plugin_id}/panel/{instance}/{rest:path}`；票据按
  `(plugin_id, instance)` 签（原来只签 instance，同实例下一条通道的票在另一条通道上也认），
  验完换成 Path 限定在基路径下的 HttpOnly cookie。
- 平台只做四件事：发票据、转发（基地址与鉴权来自 `panel_client()`）、文本改写回调、入口回调。
  NapCat 的两个钩子搬进 `plugins/qq-napcat/panel.py`（`entry` / `rewrite` + 运行时 shim），
  插件实例上只是转发一层。
- 没有 `panel_client` / `panel_entry` 的通道在这两个接口上一律 404——卡片上也就没有按钮。
- 顺手两条硬化：`..` 段直接 400（反代只该看见面板根之下的路径）；插件方法先 `getattr` 拿到
  再调，不先 `hasattr` 后直接调用。
- 前端零改动：它只 post 票据接口、然后打开后端返回的 url。旧路径 `/qq-napcat/<实例>/...`
  下线，已经开着的控制台标签页会 404，回卡片重新点一次即可（票据本来就只活一小时）。

**验证**：`test_channel_panel` 4/4（票据按通道与实例限定、核心路由里不含具体通道名、越界路径
与无票据 403、NapCat 适配器只改自己的 `/webui/` 前缀）；五个套件 **119/119**；容器内确认两条
新路由已注册到 app。仍是工作区改动，未提交、未重启。

## 2026-11-13 · qq-channel 拆成四个 mixin（plugin.py 2630 → 418 行）

**现象**：`plugins/qq-channel/plugin.py` 2630 行、80 个方法，收发、身份、群落点、引用与撤回
全挤在一个类里。改入站要在一屏里跳来跳去，审查时也看不出"哪一段属于哪一侧"。

**改法**（纯搬运，行为零改动）：

- `common.py`：限流窗口、去重容量、召回周期等常量，`_serialize_same_text`（同一句排队过），
  群消息模式文案/类型判定这类纯函数。
- `inbound.py`：事件分发、群消息与私聊入口、全量/点名两种观测、落点群与群资料、@ 物化与引用解析。
- `outbound.py`：载体选举、回复投递、分片与超长重发、失败上报、引用/撤回记账、@ 翻译。
- `identity.py`：建锚点、认亲（union_openid 与同名成员）、影子自愈、union 回填。
- `plugin.py` 只剩装配与生命周期（`__init__` / `get_status` / `start` / `stop` / `environment` /
  `channel_kind` / `self_test`），类由 `IdentityMixin, InboundMixin, OutboundMixin, GatewayMixin,
  ServicePlugin` 组成——`GatewayMixin` 是早先抽连接层时留下的先例，这次把剩下三类照同一形状补齐。
- 静态方法跨模块引用（`_quoted_of` 调 `_readable_content`）改成模块内 `_plugin_class()` 到
  调用期再取组合类：`plugin.py` 先导入 mixin、再定义类，顶层互相 import 会成环。
- 测试里三处"从插件包取常量/纯函数"改到各自归属的文件（`format` / `common`），加了个
  `_plugin_module("…")` 小助手。

**边界怎么定的**：不按行数切，按"谁能独立改"——入站只需要知道"落库 + 唤醒"，出站只需要知道
"落库后的那条消息与路由"，身份只回答"这条地址对应哪个账号"。三块之间只通过 `self` 上的状态与
对方的公开方法往来，不需要互相 import。

**验证**：`test_qq_channel_plugin` 77/77、五个通道套件 **119/119**；容器内按生产同一条加载路径
（`packages.load`）导入通过。仍是工作区改动，未提交、未重启。

> 2026-11-14 复审更正：上面那层 `_plugin_class()` 懒取是多余的——`_readable_content` /
> `_scene_ext` 与调用方同在 `inbound.py`，直接 `InboundMixin.…` 调即可，已去掉这个间接层。

## 2026-11-14 · QQ 侧图片双向：入站落站内附件、出站发图片消息

**现象**：QQ 侧收发不了图片/表情。入站 `_readable_content` 把所有附件压成 `[图片]`/`[语音]`/
`[文件]` 占位，图片内容完全丢失；出站只有正文（Markdown 或纯文本），没有任何上传或发图能力。
主人还点了另一条约束：**有的 QQ 客户端不渲染 Markdown**，所以"把图嵌进 MD"这条路不能指望。

**改法**：

- 入站（`inbound.py` → `outbound._deliver_to_group / _deliver_to_dm`）：`media.event_attachments(d)`
  挑出**带 url 的图片**，下载后 `file_service.upload_file` 存到 `attachments/qq/`，把
  `{file_id, path, name, size, mime_type}` 塞进 `send_gm_message / send_dm_message(attachments=…)`。
  多模态上下文只认 `messages.attachments`（`app/utils/multimodal.py`）：图不落库，AI 就永远只
  看得到 `[图片]`。下载只放行 `multimedia.nt.qq.com.cn` / `gchat.qpic.cn`，20 秒超时、15MB 上限，
  失败就降级（占位还在，消息照落）。
- 出站（`gateway.py`）：`upload_media` → `POST /v2/{groups|users}/{target}/files`
  （`file_type=1`、`srv_send_msg=false`、`file_data` 为 base64）拿 `file_info`；`send_media` →
  `{msg_type: 7, media: {file_info}}`。图片消息是**普通消息类型**，与正文格式无关，纯文本模式
  照样发、对面照样看得见——这是选它而不选 Markdown 内嵌图的全部理由。
- 顺序与额度：**文本先、图片后**（主人定）。图片同样占被动回复额度（群 5 次 / 私聊 4 次），
  `msg_seq` 顺延；额度不够或发失败只记日志并计入 `media_skipped`，不把整条回复判失败。
- 复用：同一份内容在同一条会话里按 sha1 复用 `file_info`（官方 ttl），不重复上传；上传前按
  15MB 卡一道（官方 20MB，base64 后 ×4/3）。
- 出错要**告诉 AI 自己**：原来只有 `_report_unreachable` 往主人私信里写系统通知，AI 的上下文
  只有账本，它读不到——于是它以为已经发了，下一轮接着聊。现在同一个入口再往 AI 的账本落一条
  `kind="note"/actor="system"` 便签（`_note_ai`，群用 `group:{id}`、私信用 session_id），
  并按"整条没发出去"与"文字到了图没到"分别措辞。
- 边界：语音（要转 silk）、视频（要转码）、普通文件（要带 `file_name`）没做，正文里仍是占位；
  NapCat 侧这轮不动，留给 `CQ:image` 那条路。
- 拆文件：`media.py` 只管 QQ 的媒体口径（mime → file_type、取图、大小上限），`gateway.py` 管两个
  协议调用，`outbound.py` 管"什么时候发、按什么顺序"。

**验证**：`test_qq_channel_plugin` **83/83**（新增 6 个：mime/挑图口径、`upload_media` 复用 +
`msg_type=7`、先文本后图片与 seq 顺延、入站图落成附件、整条失败落 AI 账本、图片没到落 AI 账本）；
五个通道套件 **125/125**。
真机未验（要一条真 QQ 图片消息 + 一次 AI 发图），下次上线后补看 `上传 QQ 媒体` /
`发 QQ 媒体消息` 两行错误码。仍是工作区改动，未提交。

## 2026-11-14 · QQ 图"看得见却看不见内容"、AI 收不进自己空间

**现象**：群里先发一张图、紧接着一句「发了，收到了吗」，AI 回「还是只有[图片]，我这边没收到内容」。

**排查**（真机 + 库 + 日志）：**入站是成功的**。群 81 的消息 5140 有
`attachments=[{file_id:190, path:attachments/qq/5F4AD139….png, size:279373}]`，物理文件也在；
07:42 那次请求日志里有 `已注入图片: …png (275KB)`。真正的坑有两层，第一层是**决策代理没让 AI 醒**：

```
23:45:04 decision=none, priority=55,
         reason=AI Deepseek 大肥鱼 代理判断不打扰：只是发图，没叫我或派活
23:46:13 decision=reply, priority=60,
         reason=代理判断该醒：书爱在确认我能否收到图片，接话即可
```

代理只读文本，而正文里图片早被压成 `[图片]`——它看到的是一条"没有内容"的消息，判"不用打扰"
完全合理。第二层是注入时机：`_attach_attachments_to_message` 只给"最后一条用户消息"注入真图
字节，AI 被后面那句文字唤醒时最新一条是纯文字，图退化成历史里的 `[图片]` 标记——07:45/07:46
两次请求都没有 `已注入图片` 行。另外同一条消息里 `[图片]` 会出现两次（正文占位 + 附件标记）。
教训：先看"有没有开轮"，再看"开了轮给了什么"——只看请求日志会漏掉根本没发生的请求。

**改法**：

- 代理判定先拿到附件事实：`_attachment_note`（`app/ai/decider.py`）从消息行算出「（这条带了
  1 张图片、1 个文件（稿子.pdf））」塞进判定上下文，判定准则里补一条"同伴发来图片或文件
  （哪怕没配文字）通常是给你看东西，值得起身看一眼"。
- **代理终于看得懂自己说的话**：原来 `build_context` 直接渲染账本 actor，AI 自己那几行标成
  `- self：…`——模型读成一个叫 self 的群成员，于是"你随便发一张，我当场念给你听"这句等于没
  说过，下一张图自然判"没叫我"。现在映射成人话（`self → 你`、`user → 群成员`），并在准则里
  写明「你：」开头的是自己说过的话、新消息在接你的话通常该起身。
- 去重按 **消息 id** 而不是正文：`recent_ledger` 原来用"待判正文出现在最后一条里"来去重，
  图片那条正文就是 `[图片]`，很容易误删上一条真历史。现在 `message_id` 对齐 `ref` 才删。
- 注入范围从"最后一条用户消息"扩成"**本轮**（最后一条 AI 消息之后）里最近的一张带图消息：
  `_turn_image_of` 往回找，仍只注入一张、不跨轮。先图后文、连发两张都覆盖；token 与 prompt
  cache 的账没变（还是一条图）。
- 顺手补 AI 的"收进自己空间"：平台工具 `file_fetch`（`app/tools/file_operations/file_fetch.py`）
  + `file_service.ai_fetch_attachment`。群聊/私信都能收、任何文件类型都收；**硬链接**到
  `agents/{id}/inbox/`，不复制字节，原件仍归发送者。不带 `file_id` 时取当前会话最近一条带附件的消息。
- 修了一个权限换算 bug：`_file_attached_to_visible_message` 用 `agents.id` 去查群成员表/私信参与方，
  而那里记的是 AI 的虚拟账号 `users.id`——于是"附在它自己群里的附件"被判无权（`file_fetch` 第一次
  跑就撞上）。现在两层身份都认。

**验证**：新增 `test_attachment_fetch`（群聊收纳、私信收纳、别的 AI 收不到）3 条 +
`test_attachments_in_context` 的"先图后文"回归 1 条 + `test_proxy_judge` 的附件事实 2 条；
`test_proxy_judge / test_attachment_fetch / test_attachments_in_context / test_file_access /
test_qq_channel_plugin` 合计 **114/114**。仍是工作区改动，未提交。

## 2026-11-14 · 附件与图片链路的重复实现收口（行为不变）

表情包要按"资源插件 + 平台使用"做，先把底层会被三套写法复制的点收成一处，否则新功能会再长出一版。

- **路径拼法**：`file_service.disk_path(rel)` 成为唯一拼法。之前 `multimodal._read_data_url`、
  `routers/files.py`（创建目录）、QQ 出站取图各写一遍 `data_dir + 相对路径`。
  （重命名时踩了一个真坑：原想叫 `physical_path`，但多处函数里已有同名局部变量，
  `physical_path = physical_path(...)` 直接变成 UnboundLocalError。换了 `disk_path` 才干净。）
- **附件引用记账**：`file_service.track_attachments(...)` 收口，`chat/gm.py` 与 `chat/dm.py` 不再各抄一段
  `track_forward_reference` 循环——两处一旦漂离，配额和去重就算到不同人头上。
- **AI 文件查找**：`file_service.ai_file(db, agent_id, ref)` 收口"前缀 + 查元数据"，`send_file` 不再自己
  拼 `ai_stored_path` 再查；"归不归它所有"仍由调用方按 metadata 判（读共享文件的路径不受影响）。
- **`[图片]` 不再叠两次**：`attachment_marks(attachments, said=正文)`——正文已经写了占位就不再补标记。
  真机上 AI 收到「[图片] [图片]」两个标记，回"只有[图片]两个字"。
- **图片 mime 一套**：`app/utils/mime.py:IMAGE_MIMES`，多模态（`VISION_MIMES` 沿用旧名）与 QQ 媒体层
  都从这里取，不再各写一份同样的集合。

**验证**：`test_attachments_in_context / test_multimodal / test_file_access / test_channel_files /
test_qq_channel_plugin / test_attachment_fetch` 合计 **121/121**（新增标记去重 1 条）。
仍是工作区改动，未提交。

## 2026-11-14 · 图片表情：入口归一成附件，QQ 发图片消息

按"资源归插件、使用归平台"的分层落地第二步：让**纯图片表情**真的能发出去。

- **入口归一**（`catalog`）：`take_image_faces` 把正文里的 `:id:` / `[表情:名]`（仅图片的那一类）
  摘掉并收集；`take_image_emoji_attachments` 把它们变成 attachments。`chat/gm.py` 与 `chat/dm.py`
  各调一次——**唯一入口**，下游（站内渲染、QQ 图片消息、历史标记、file_fetch）只认附件。
- **资源落成平台文件**（`emoji_asset_file`）：插件里的图**硬链接**到 `data/emoji/<pack>/<file>`
  并登记 `file_metadata(owner_type="system", owner_id=0, collaboration="open")`；重复调用复用同一行。
  这样"表情"在平台看来就是一张普通图片，不需要任何新消息类型。
- **通道做减法**：QQ `_emoji_to_chars` 不再负责"图片表情怎么办"（上游已变附件），只留字符表情
  与历史兼容写法的清理；顺手把那句"官方 API 不回传富媒体"的过期注释改掉。
  不在本步：NapCat 的 `CQ:image`（下一步）。
- **缓存**：`sync_plugins_to_db` 变更后 `invalidate_caches()`；测试里直接改 DB 开关的地方补
  `_enable_emoji_pack()` 手动失效——两个表情用例以前靠 30 秒缓存空窗侥幸通过，换顺序就红。

**验证**：新增 `test_emoji_image_send`（纯函数摘标记、硬链接+登记幂等、群消息带附件）3 条；
`test_emoji_image_send / test_qq_channel_plugin / test_attachments_in_context / test_multimodal /
test_file_access / test_channel_files / test_attachment_fetch` 合计 **124/124**。仍是工作区改动，未提交。

## 2026-11-14 · 表情包成为"平台资源插件"：大肥鱼导入 + AI 级白名单 + 商城类别

按"资源归插件、使用归平台"补上第 3 步：装一个包，AI 在正文里写 `:id:` 就能发图。

- **导库脚本**（`scripts/import_dsh_meme_pack.py`，只用标准库）：读 dsh-meme 的
  `manifest.json` + `index.db`，把 caption/keywords/tag 原样搬成 `emoji.json` 的 face 字段，
  图复制进 `<data>/plugins/<pack-id>/`。重跑即更新（图按来源覆盖、旧图清掉）。
  本次已导入 24 张 → `data/plugins/emoji-dafeiyu`（`license=personal`，不进仓库、不进商城）。
- **face 多了三段给 AI 读**：`caption` / `keywords` / `mood`（`catalog._emoji_face`），
  `list_emoji_faces` 一并列出——AI 靠这三段挑"这一句该发哪张图"。
- **AI 级开关**：新表 `agent_emoji_packs`（migration `0111`）+ `settings.emoji_pack_mode`
  （默认 `whitelist`）。`catalog.packs_for_agent / packs_for_sender / set_agent_pack` 是唯一入口：
  入口归一按"谁在说"过滤（AI 只能用被允许的包），`list_emoji_faces` 只列它可用的。
  人类/系统消息不受 AI 偏好限制——他们写下的表情是平台资源。
- **接口**：`GET /agents/{id}/emoji-packs`（列表 + 当前开关）与 `PUT …/{pack_id}`（只有主人能改）。
- **商城类别**：市场 schema 早就支持 `emojipack`，缺的是界面——商城列表加类别筛选（表情包单独一类）、
  已安装插件的筛选补上 `emojipack`。装包/卸载沿用原有 zip 导入与 `invalidateEmojiPacks()`。
- **未做**（下一步）：创建者自己上传/制作表情包（需要用户级上传入口与审核口径）、NapCat 的 `CQ:image`、
  前端 AI 设置页的勾选界面（接口已就绪）。

**验证**：新增 `test_emoji_packs`（白名单默认关、黑名单默认开、AI 发言只带被允许的包）3 条；
`test_emoji_packs / test_emoji_image_send / test_qq_channel_plugin / test_attachments_in_context /
test_multimodal / test_file_access / test_channel_files / test_attachment_fetch / test_proxy_judge`
合计 **142/142**。前端 `tsc -b` 通过。仍是工作区改动，未提交。

> 迁移号踩坑：一开始随手起名 `0105`，与已有的 `0105_proxy_judgements` 撞了 revision id——
> Alembic 报 "Revision 0105 is present more than once" 并降级成多 head，prestart 迁移失败，
> 容器进入重启循环。改名 `0111`（down_revision=`0110`）后一次通过。以后加迁移先查 head。

## 2026-11-14 · 表情包落到用户自己手里：AI 设置页勾选 + 创建者自制

前两步把"平台装包、AI 用包"打通，这一步补上两件用户看得见的事。

- **AI 设置页的表情包页签**（`AgentDetailPage` + `EmojiPacksPanel`）：上半是平台装好的包与开关
  （只有主人能改，非主人只读），下半是"我的表情包"。默认白名单下没勾 = 这个 AI 不用；勾选走
  已有的 `PUT /agents/{id}/emoji-packs/{pack_id}`，脚本侧仍由 `catalog.packs_for_agent` 一处算。
- **创建者自制表情包**：`app/services/plugin/emoji_packs.py` + `app/routers/emoji_packs.py`。
  写出来的就是一个 emojipack 插件（`plugins/<id>/plugin.json + emoji.json + 图`），归属记在
  manifest 的 `owner_user_id`；id 形如 `emoji-u<用户>-<包名>`，表情短码从包 id 派生
  （`u12-cat_1`），在全局命名空间里不会撞。接口：`GET /emoji-packs/mine`、`POST /emoji-packs`、
  `POST /{id}/faces`、`DELETE /{id}/faces/{face_id}`、`DELETE /{id}`。
- **caption 必填**：一个只有名字的表情，AI 拿不出"什么时候该发"的判断，等于没装——所以上传时
  必须写一句给 AI 看的说明（关键词/情绪可选）。图走与头像同一条校验
  （新增 `app/utils/image_validate.py`：类型白名单 + Pillow 解码，谎报成 image/png 的 SVG 拒收），
  单张 ≤ 4MB，单包 ≤ 256 个表情，每人 ≤ 20 个包。
- **删包 = 插件卸载**：目录仍走 `store.uninstall_plugin`（拒绝内置包、拒绝越界路径），再清掉
  `agent_emoji_packs` 里这个包的开关记录——否则同名包重建会"凭空打开"。
- **顺手收口**：`catalog._emoji_face` → `emoji_face`、`store._slug_ok` → `slug_ok`（写入与扫描
  共用同一份校验）；"这是不是一张真图"的判定从头像上传里搬进 `utils/image_validate`。
- **用例顺序坑**：`test_file_access` 显式写 file_metadata id 却不推进序列，套件连着跑时 QQ 入站
  落图会撞 `file_metadata_pkey`（单跑绿、连着跑红）——补 `setval` 到 100。

**验证**：新增 `test_emoji_pack_upload` 6 条（写盘形状、caption/真图校验、短码→附件、归属、
删除清理、路由 multipart+权限）；`test_channel_pairing / test_plugin_frames / test_plugin_protocol_v3 /
test_channel_files / test_file_access / test_attachments_in_context / test_qq_channel_plugin /
test_emoji_pack* / test_attachment_fetch` 合计 **159/159**；前端 `tsc -b` 与 `i18n:check` 通过；
容器里实跑一遍建包 → 传图 → 列表 → 静态图 200 → 删包（目录与插件行都无残留）。仍是工作区改动，未提交。

## 2026-11-14 · SVG 文件预览 + 下载格式可选（矢量原图 / PNG）

表情包那条链只收光栅图（同源免鉴权地址不收可执行 XML，见上一条），所以缺的不是「让 SVG 进表情包」，
而是把用户手上的 SVG **变成** PNG 的那一步。补在文件预览里，两个入口都走同一个弹窗（聊天里的图、
存储页的文件都从这里打开）。

- **预览本来就支持 SVG**（image/svg+xml 走 img）；这把补了两个真实缺口：只有 viewBox、没有
  width/height 的 SVG 在弹窗里可能塌成一条线（补 min-w/min-h 120px），以及这种图没有 naturalWidth
  时缩放会失效（useImageZoom 退到量一次的渲染尺寸当基准）。
- **下载格式二选一**：FilePreviewModal 的下载按钮在 SVG 上变成菜单——「SVG 原图（矢量）」走原地址，
  「PNG 图片（尺寸可选）」开一个小弹窗：快捷档位 256/512/1024/2048、原尺寸，或直接填长边像素
  （16–4096），弹窗里实时显示输出尺寸；上次选过的长边记在 localStorage，常用尺寸一键复用。
- **为什么在前端转**：后端要光栅化得引入 cairosvg/librsvg 这类重依赖，而预览本来就是浏览器画的；
  同一个渲染器出图，导出的样子才跟预览一致（PNG 保留透明；默认按 2× 出图、夹在 512–2048，
  可改成任意长边像素）。
  不是直接 drawImage 一张 0×0 的 SVG：先按 width/height → viewBox → 512 兜底定尺寸，再改写根元素
  的 width/height 后重画，矢量才不会糊、也不会得到空白画布。
- **失败不挡路**：导出炸了在弹窗里报一句，让人改下 SVG 原图——那条永远能用。
- **「？」帮助统一成一个组件**（components/ui/HelpTip.tsx）：点开小浮层、点外面或 ESC 关；
  全站原来只有 IconButton 的 title（只能悬浮看，手机上出不来）。已换用：预览弹窗的两处说明、
  群视界输入栏的计费说明。

### 补记：导出的 PNG "没有透明"是怎么回事

headless Chrome 实测确认导出本身**是保留 alpha 的**（纯透明 SVG 导出后角落 alpha=0、65536 像素里 32238 个透明），
问题出在文件自己身上：Figma / Illustrator / 图标站导出时经常顺手加一层满幅底色。站内已上传的两张就是：
coprexist-icon.svg 是 32×32 的 #0d0a17 圆角底，pixel_ruomeng.svg 是 16×16 viewBox 里的 #fdf6ec 满幅底。
浅色主题下这层白/浅色底与透明分不出来，只会在导出后才暴露。

- **导出面板会检测并给出开关**：直接子节点里「铺满画布 ≥99%、实色填充、不透明、无 transform/filter/mask/clip-path」
  的矩形算背景层；检测到就在面板**最底下**给「去掉 SVG 自带的满幅底色（#xxxxxx）」，**默认不勾**、标红「不推荐」——
  这层底往往就是图标本身（coprexist 图标就是深色圆角底），勾了会整块消失、只剩上层图形（用户当场指出了这次误删）。
- **预览加棋盘格**（.bg-checker）：SVG 预览铺 16px 棋盘格，文件自带的底色与真正的透明一眼可辨。
- **坐标单位坑**：判定必须用 viewBox 的用户单位比较，不能用渲染像素——pixel_ruomeng.svg 是
  width=256 height=256 viewBox="0 0 16 16"，按像素比会把 16×16 的满幅底当成「小块」漏掉（实测漏过一次）。

**验证**：headless Chrome 跑真实两张图——icon 默认导出 3300/65536 透明 → 勾掉背景 55790/65536；
pixel 默认 0 透明 → 勾掉背景 31232/65536（角落 alpha=0）。
- i18n：filePreview 新增 downloadFormat / downloadAsSvg / downloadAsPng / exportPngTitle /
  exportPngNatural / exportPngOriginal / exportPngLongestSide / exportPngOutput / exportPngSave /
  exportPngFailed 三语。

**验证**：前端 tsc -b 与 i18n:check 通过；Vite dev server 对 svgRaster.ts 与 FilePreviewModal.tsx 的
转译都正常（HMR 已更新）。纯前端改动，没有新增后端接口，仍是工作区改动、未提交。








## QQ 语音消息与平台侧语音（TTS）能力（2026-11-14）

**先纠正一个假设：QQ 不需要转 silk。** 实测（2026-11-14 10:49，agent-49 机器人私聊用户）：
mp3 原文件直接 POST /v2/users/{openid}/files（file_type=3、srv_send_msg=false）拿
file_info，再 msg_type=7 发消息，客户端**能正常播放**。接口层与客户端都验过了，
所以平台侧不必背 silk 编码器；silk-wasm（MIT）只 vendor 在插件里当兜底，默认不用。

**降级链（一次性写好顺序，不是每轮试探）**：mp3 当语音（file_type=3）→ 失败退成
文件（file_type=4，带文件名，对面能下载听）→ 再失败只发文字并在 AI 账本留一条
「这次没发出去」。注意：客户端不收时接口**仍可能回 200**，这类静默失败只能人耳验证，
验过的结论要记下来，别反复盲试。

**qq-channel**：media.py 新增 VOICE_MIMES（只认实测过的 mp3）与 is_sendable_voice；
outbound.py 的媒体循环按图片/语音分流，每次尝试各占一次回复额度（msg_seq 递推），
两次都失败才计入「没发出去」。用例：test_reply_sends_mp3_as_a_voice_message、
test_voice_message_rejected_falls_back_to_file（85 条全绿）。

**预设清单**（provider_presets.py）：引入 kind = llm | voice，新增 breeze /
minimax-cn / minimax-intl 三条语音预设（MiniMax 国内站与国际站互不相通，拆两条让用户选，
不做自动猜站）。get_all_presets() 默认只返回 LLM —— 遍历预设去取 chat_model 的老代码
（bootstrap / model_upgrade / world 回退）全部按 kind 过滤，否则语音预设会让它们 KeyError。

**平台侧**：agent_voice_configs（1:1 挂 AI，key 走 Fernet，检测结论一并落库，任何字段
一改就作废旧结论）；voice_probe.py 按家判定（Breeze 走 GET /v1/api-keys/current，
200 即合法、额度 0 也算合法；MiniMax 先 /models、404 退成 max_tokens=1 的极小 chat，
**同时解析 base_resp**，HTTP 200 也可能是 1008/2049）；voice_tts.py 统一产 mp3
（MiniMax 的 data.audio 是 hex，要解码）；send_voice 工具发**独立一条**语音消息
（与 send_file 同路：先落站内附件再发）；门控只有一处——没配或没测通，工具不进工具表，
模型不知道有这功能。
接口：GET /agents/voice-presets、GET|PUT /agents/{id}/voice、
POST /agents/{id}/voice/test、POST /agents/{id}/voice/preview（试听，唯一花钱的动作）。

**验证**：后端 131 条相关用例全绿（voice_presets / voice_probe / voice_tts / voice_config /
api_probe / qq_channel / channel_files）；迁移 0112 已在线上库建表；前端 tsc -b 与
i18n 闸门（42 命名空间 / 2363 调用点）通过。改动仍全部留在工作区、未提交。

## 本轮线上问题修复（2026-11-14，聊天滚动与「首条未读」定位）

- **往上翻，加载出来的是几周前的零散旧消息**（群 81 真机）：`get_gm_messages()` 的 `before_id` 分支
  只加了 `Message.id < :before_id`、**没有 ORDER BY**，SQL 给的是物理顺序（≈最早那一批），
  前端排完序就是「旧消息夹在最新几条上面」。同一函数里 `after_id` 早就按 id 升序，这个镜像分支漏了。
  复现：`where group_id=81 and id < 5184 limit 20` → `4081 / 3723 / 3537 / 4606 …`；
  补 `order by id desc` 后 → `5183 … 5154`。
  修法：`before_id` 补 `desc(Message.id)`；首屏（else 分支）也从 `desc(created_at)` 改成
  `desc(Message.id)`——三种取法同一口径，同秒插入的两条（AI 回复和人类消息常挤在同一秒）顺序才稳。
  验证：首屏 20 条 `5198..5217`、往前两页 `5154..5183` / `5144..5163`，三页都严格递增。
- **刷新后视口落到一周前，中间一大段够不着**：两处叠加。① `loadInitialMessages` 无条件
  `catchUpFrom(lastReadId)`，把中间那段最多 50 页一口气回填，回填到的第一条又被当成「首条未读」
  钉到视口——首页就停在一周前；② `loadMessages` 把 `newestIdRef` 写成「这一页的最后一条」，
  于是每翻一页，下一次追新都从那一页的位置再往前追，存下的已读位置也被一起污染。
  修法：游标 ref 收敛成**一个**镜像 effect（`messages` 一变才写 oldest/newest，任何加载路径都不再
  自己写）；首屏不再自动补中间段；`lastRead` 取 `messages` 末条（镜像 effect 在 useLayoutEffect
  之后才跑，此刻 ref 还是 null）；定位门槛 `AUTO_LOCATE_RECENT = 10`——「首条未读」离末尾超过
  10 条就不抢视口，直接落到最新。顺带修掉一个老问题：拿不到未读那行元素时以前会停在
  `scrollTop = 0`（视口在最上面）。
- **未读为 0 却画出「新消息」分割线**：首屏拿不到 `lastRead` 时，满页分支会凭空
  `setFirstUnreadId(fetched[0].id)`，把这一页第一条当成未读边界。该分支删掉——没有 `lastRead`
  就是没有分割线。「跳到首条未读」仍是往前一页 + 200ms 后重定位，要真正跳到更早的未读、
  并按新行高重算视口，还没做。
- **同一类缺口（未修，先记下）**：私信分页 `_get_messages()` 两个游标仍按 `created_at` 排序
  （`backend/app/chat/dm.py`），与群聊修前是同一个病。影响面小（私信天然单线、同秒两条少见），
  但口径不一致迟早显形；要改就连首屏一起改成 id。
- **代理把「@ 别人」读成「点名让我」，起身替人接话**（群 77 化学老师，2026-11-14 17:10）：
  书爱 `<@!120>`（大肥鱼）让他发条语音，化学老师（users 40）跟着回了一句。查下去：
  开关全开着（`proxy_wake_enabled` / `proxy_wake_enforce` 为真，留白名单只有群 66），
  这条确实走了代理——`proxy_judgements #464`：wake=t、urgency=normal、fallback=f、
  deepseek-flash、448ms，理由「点名让我发语音，接我的话」。根因是喂给代理的正文是**裸令牌**
  （`<@!120> 那你在这里发条语音吧……`，响应日志 `content_preview` 同形），没有任何"这 @ 的是谁"，
  模型看到一个 @ 就把自己代入；同一套裸输入在 09:05 对另一只 AI 判的却是「叫的是大肥鱼，与我无关」
  ——不是规则问题，是它在猜。
  修法：交给代理的正文里把 @ 就地注成 `<@!id>（名字）（不是你）`——代理只会在「没点名本人」
  时才被问到，所以"不是你"是事实不是推断；名字由 `decider._mention_annotated` 现算
  （`message_serializer.mention_names` → `display_names`，与账本同一份），渲染复用
  `render_mention_with_names(extra=…)`，不新增上下文块、也不新增 `judge()` 参数。
  花销：**+19 字**，只落在带 @ 的消息上（最近 7 天 43/592 = 7.3%）。
  验证：同一条消息重跑代理——修前 `wake=True why=点名让我发语音，接我的话`，修后
  `wake=False why=话是对别人说的，没叫我`。用例
  `test_proxy_judge::test_mention_annotated_marks_who_and_not_you`（把 `extra` 去掉即转红）。
- **记忆表被写进两种 id 口径，已造成跨用户泄漏**（2026-11-14 排查文件归属时撞见）：
  `rough_memories.owner_id` 在 AI 侧一律记 `agents.id`（8 处读取都按它过滤、281/283 行也是），
  但一条遗留的 HTTP 端点（`POST /memories/rough`）让人类/虚拟账号直接往同一张表写、用的是
  `users.id`，`owner_type` 还标着 `'ai'`。后果两头都坏：AI 写进去的记忆 HTTP 读不到；
  编号一撞就串门——实测 `agent 3 梦希` ↔ `user 3`、`agent 25 浮生` ↔ `user 25` 都能互读。
  修法（选的是「这张表只属于 AI」）：整组删掉 `routers/memories.py` 与只给它用的
  `schemas/memory.py`（全仓 0 调用方，只有历史规格书 `cpec.md` 提过）；存量由迁移 `0115`
  归正——只改「不是任何 agents.id、但等于某个 agent 虚拟账号」的行，实测 2 行
  （id 89/90 → `owner_id=25` 浮生），281 行 agents.id 一个字节没动。
  验证：`/memories/*` 路由已不在 `app.routes`；记忆相关用例 13 条全绿
  （`test_focus_tools` / `test_memory_anchors` / `test_group_disband`）+ `test_file_access` 5 条。
- **AI 发自己的文件也被记成「转发」，自己的配额被算掉一份**（2026-11-14 语音批暴露）：判「是不是
  他的文件」的代码全是 `meta.owner_id == requester_id` 直接比数字，而 `file_metadata.owner_id`
  记 `agents.id`、消息的 `sender_id` 是虚拟账号 `users.id`——两个号段永不相等（见
  [id 语义](./id_semantics.md)）。证据：file 214 是 ai:120（agent 49）自己的语音，发送时落了一条
  `forward ai:120`。修法：判据收敛成一处 `file_service.is_file_owner()`（两种写法都认），
  `check_file_access` / `delete_file` / `track_forward_reference` / 协作模式与协作者管理
  五处统一改用它；迁移 `0114` 清存量。实测：`forward` 行 43 → human 27 / ai 3
  （AI 那 13 条是它发自己文件的误记）。用例
  `test_file_access::test_ai_sending_its_own_file_is_not_a_forward`（放回旧判据即转红）。
- **语音不能被 AI 自己转发**（能力缺口，不是故障）：`send_voice` 落在公共 `attachments/voice/`，
  `send_file` 只认 `agents/{id}/…`，所以要它转发自己的语音必然落空。绕法与改法写在
  [文件存储与协作系统 §12.5](../文件存储与协作系统.md)。
- **会话列表的预览不认撤回、不脱彩色标签**（2026-11-14 用户实测：撤回后侧栏还显示原话）：
  侧栏那一行与站内浮窗都在拼「给人看的一版」，但各走各的——列表直接把原文截断，于是
  ① 撤回后预览仍挂着原话（刷新也甩不掉，因为 `last_message_preview` 根本不看 `revoked_at`）；
  ② `[blue]123[/blue]` 显示成标签字面量（`make_preview` 只脱 HTML 尖括号，站内彩色文法没脱）；
  ③ 纯语音显示成 `voice_20261114104640_1.mp3`。
  修法：这条口径收成一份 `message_serializer.text_preview()`（@ 换名字 → `plainify_markdown`
  脱 Markdown 与彩色标签 → 截断 + 附件占位），会话语义再包一层 `preview_of()`（撤回 →
  「撤回了一条消息」），`gm.list_groups` / `dm.list_dm_sessions` / 浮窗 `message_toast`
  三处统一改用它；附件占位补 `audio/*` → `[语音]`。前端：撤回成功后刷一次会话列表
  （私信广播按 `exclude_user_id` 排除发起者，会话连接不会替它改），别人撤你的随
  `message_revoked` 一起刷。
  验证：库里 5087/5075（已撤回）→「撤回了一条消息」、5088（`[blue]123[/blue]`）→「123」、
  1_4 语音 →「[语音]」；`test_message_preview` 4 条新用例 +
  `test_message_revoke` / `test_list_query_count` / `test_dm_unread` / `test_gm_dm_symmetry` 全绿。
- **语音在列表里没有时长**（同一天的追问：为什么不是 `[语音 1'42"]`）：attachments 快照里
  只有文件名与大小。时长是**文件的不可变属性**，于是加 `file_metadata.duration_ms`（迁移 `0116`）：
  上传音频时 `utils/audio_probe.audio_duration_ms()` 探一次（mp3 读帧头——CBR 按码率、
  VBR 读首帧 Xing/Info 的帧数；wav 按 byte_rate；都不引 ffmpeg/mutagen），探测失败留空。
  判据沿用 transcript 那次的结论但**方向相反**：会变的（文稿）只存一处、读时按 id 取；
  不会变的（时长）可以随快照走一份，列表预览因此不必为每条会话再查库——`send_voice`
  写快照时带上 `duration_ms`，`make_preview` 的语音分支用 `voice_label()` 拼成
  `[语音 42"]` / `[语音 1'42]`，没时长就退回 `[语音]`（不写 0'00"）。
  历史那批（12 个音频、9 条 AI 语音）元数据与快照都没有时长，用
  `scripts/backfill_voice_duration.py` 一次性按磁盘文件补齐（幂等，迁移之后跑）。
  验证：`test_audio_probe` 5 条（手工构造的合法 CBR/VBR 帧、ID3 跳过、wav）+ 预览 5 条全绿；
  真机 12 个 mp3 探出的时长与逐帧走查一致（`voice_20261114033204_49.mp3` = 269 帧 × 576 / 24000
  = 6.456s，探出 6456ms）。
- **列表里那条撤回说不清是谁撤的**（同日追问，方案改了两次）：先是想按"你/对方/名字"三分，
  用户随后定了更省事的口径——**一律写名字**，完整名字不截前 5 字，也不特判"你/对方"，
  免得胶囊与侧栏两处口径对不齐。修法：`revoked_preview(name)` 一处拼串；列表多给一个
  `last_message_revoked_name`（撤回时才有），前端把名字与「撤回了一条消息」分成两段渲染——
  宽度不够时先缩名字（`truncate`）、后半句 `shrink-0` 不动。胶囊（`chat:revokedSuffix`）与
  侧栏 `PreviewText` 同一套做法。
- **AI 合成语音的那几秒界面上没动静**：活动条只有「思考中 / 输入中」。现在
  `ai/executor.py` 的 `_TOOL_ACTIVITY_STATE` 把 `send_voice` 映射成 `speaking`，随同一个
  `ai_typing` 事件多带 `state` 发出去；前端 `ChatView` 分到独立的 `speakingAgents`，
  `ActivityBar` 优先级 说话 > 输入 > 思考，文案 `chat:speaking`（录音中…／Recording…／録音中…）。
  清除：新一轮思考、消息落地、轮末各清一次，外加 30 秒兜底。

