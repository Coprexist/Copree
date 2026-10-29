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
- **同一个会话的状态帧攒成一串**（某 AI 的 `group:59` 三个活帧）：`push_state` 的去重只跟栈顶比身份，
  离开再回来就压新帧，旧的仍留在运行集合，摘要把它们当成几段正在进行的事、帧位也被白占。
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

