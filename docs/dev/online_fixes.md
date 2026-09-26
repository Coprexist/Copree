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
