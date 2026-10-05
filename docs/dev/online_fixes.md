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

