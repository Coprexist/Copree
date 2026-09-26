# Copree 后端 Repository 化重构 — 进度存档（2026-08-23）

> 本文件是重构进度的**唯一权威存档**。compact / 新会话续做前先读本文件。
> 项目根：F:\Zhang\Copree（后端在 backend/，包根 backend/app）

## 一、目标与原则

把业务服务层与 `AsyncSession` 解耦：服务依赖 `<Module>Repository`（Protocol）而非直接碰会话。
- **模式 A（彻底解耦，占总量 ≥70%）**：签名 `db: AsyncSession` → `repo: XRepository`，函数体 `db.execute/get/add/flush/commit/delete/refresh` 改 `repo.xxx`；路由用 `Depends(get_<module>_repo)` 注入；删 `AsyncSession` 导入。
- **模式 B（大模块/调用面含 AI 运行时，≤30%）**：签名保持 `db: AsyncSession`，文件顶部加 `_ensure_repo` helper，每个用 db 的函数体首行 `db = _ensure_repo(db)`，之后 `db.xxx` 走 repo。
- **关键洞察**：`AsyncSession` 与通用 repo 接口**鸭子类型兼容**（session 本身有 execute/get/add/flush/commit），旧调用方传 session 也能跑——Mode A 迁移有安全垫。
- **需要真 session 的跨模块调用**（chat.message、ToolPlugin.execute 等）：repo 暴露 `.session` 属性桥接（`repo.session`）。
- **验证**：无 PostgreSQL、venv 无依赖，只能用 `.venv\Scripts\python.exe -m py_compile <files>` 语法检查 + 人工 review。**勿装依赖**（用户环境差，此前装过一次被终止）。

## 二、Repository 层（backend/app/repositories/）

| 文件 | 用途 |
|---|---|
| user_repo.py / friend_repo.py / world_repo.py / group_type_repo.py / agent_repo.py / system_settings_repo.py / verification_repo.py / api_key_pool_repo.py | 已完成模块（历史） |
| invitation_repo.py | 群邀请（通用 execute/get/add/flush/refresh/commit/rollback + session 属性） |
| search_repo.py | 搜索（execute） |
| memory_repo.py | 记忆域通用（execute 带 params/get/add/delete/flush/refresh/commit/rollback） |
| skill_repo.py | Skill 域通用 |
| infra_repo.py | 基础设施通用（含 session 属性） |
| content_repo.py | 内容域通用（含 session 属性） |
| export_repo.py | 导出（含 session 属性） |

通用 repo 模板（memory_repo.py 为准）：`execute(stmt, params=None)`、`get(model, pk)`、`add(obj)`、`delete(obj)`、`flush()`、`refresh(obj)`、`commit()`、`rollback()`，可选 `.session` 属性（`_session` 私有存储，property 返回）。

## 三、已完成（6/8 round，全部 py_compile 通过）

### Round 1 — social 模块（**模式 A**）
- `repositories/invitation_repo.py`、`repositories/search_repo.py`（新建）
- `services/social/invitation_service.py`：4 函数签名 `invitation_repo: InvitationRepository`；chat 桥接用 `invitation_repo.session`（get_or_create_dm_session/send_dm_message/add_member）
- `services/social/search_service.py`：1 函数 `search_repo`
- `routers/deps.py`：新增 `get_invitation_repo`、`get_search_repo`
- `routers/invitations.py`、`routers/search.py`、`routers/groups.py`（两个 send_group_invitation 调用点注入 invitation_repo）

### Round 2 — memory 模块（模式 B）
- `repositories/memory_repo.py`（新建）
- `services/memory/memory_service.py`（4）、`structured_memory_service.py`（9）、`summary_cache_service.py`（3）、`vector_memory_service.py`（5）、`forgetting_mechanism.py`（3）、`context_config_parser.py`（1）——括号内为 `db = _ensure_repo(db)` 数
- `services/memory/memory_distribution.py` **未改**：无直接 db 操作（仅转发），且其内部调用的 `structured_memory_service.get_categories` 不存在（死代码，无调用者），保持原样

### Round 3 — skill 模块（模式 B）
- `repositories/skill_repo.py`（新建）
- `attention_system.py`（3）、`skill_service.py`（5）、`skill_engine.py`（5：_handle_inject_prompt/_load_enabled_skills/_is_delay_reply_allowed/evaluate_action_skills/evaluate_inject_skills）、`trigger_engine.py`（6）

### Round 4 — agent 配套（模式 B，复用 agent_repo.py）
- `services/agent/workspace_service.py`（10）、`services/agent/state_stack_service.py`（16）

### Round 5 — world 配套（模式 B，复用 world_repo.py + 新增 session 属性）
- `repositories/world_repo.py`：加了 `session` 属性（property 返回 `_session`，构造存 `_session`——**注意别写递归**）
- `services/world/world_chat_service.py`（11：build_memory_map/_record_usage/ensure_session_lifecycle/get_chat_history/_save_note_separated/_save_ai_reply/_resolve_world_credentials/_inject_pending_user_messages/_execute_tool_round/_prepare_world_chat/stream_world_chat）
- `services/world/world_tools.py`（2：_do_execute/_execute_world_tool）+ **10 处 session 桥接**（chat.message 的 _get_group/_get_recent_messages/_get_group_members/_create_message/_change_member_role/_remove_member、WebSearch/WebFetch.execute、ensure_world_api_token、execute_skill → `db.session`）
- `services/world/world_suggestions.py`（2）、`services/world/world_chat_commands.py`（1）

### Round 6 — infrastructure 模块（模式 B，复用 infra_repo.py）
- `repositories/infra_repo.py`（新建，含 session 属性）
- `system_settings_service.py`（4）、`verification_service.py`（3）、`api_key_pool_service.py`（3）、`credit_service.py`（3）、`embedding_config_service.py`（4）、`app_config_service.py`（5）、`quota_service.py`（5）、`email_service.py`（4）

### Round 7 部分 — content/export（**模式 A**，已完成）
- `repositories/export_repo.py`（新建）
- `services/content/export_service.py`：4 函数 `export_repo: ExportRepository`；get_group 桥接 `export_repo.session`
- `routers/deps.py`：新增 `get_export_repo`
- `routers/dm.py`（export_dm_chat 注入）、`routers/groups.py`（export_chat 注入；保留 db 用于校验）
- `repositories/content_repo.py`（新建，供下面 3 文件用）

## 四、剩余工作

### Round 7 剩余 — content 3 文件（模式 B，复用 content_repo.py）
调用方（import 数）：conversation_log_service→6（ai/executor、ai/response_worker、routers/admin、conversation_log、dm、world_chat_service）；file_service→12（bootstrap、chat/dm、chat/message、routers/admin、files、user、tools/file_operations/*6）；opencli_service→3（routers/admin、agents、tools/file_operations/execute_command）。
需 wrap 的函数（分析器已定位，行号基于当前文件）：
- `conversation_log_service.py` 18 个：save_conversation_log@17、_trim_old_logs@61、_get_agent_log_limit@82、_get_config@100、get_config_dict@111、update_config@123、get_user_log_limit@162、update_user_log_limit@183、get_agent_logs@205、get_log_detail@241、get_agent_log_stats@262、get_user_agents_token_summary@279、get_agent_token_daily@336、get_admin_global_token_stats@377、get_admin_users_token_summary@427、_user_can_view_agent_logs@471、get_agent_log_settings@501、update_agent_log_settings@526、get_session_token_usage@622
- `opencli_service.py` 12 个：_get_config@30、check_permission@41、check_rate_limit@118、_log_usage@435、update_opencli_config@473、list_agent_whitelist@494、update_agent_whitelist@525、list_command_whitelist@554、add_command_whitelist@574、toggle_command_whitelist@610、delete_command_whitelist@627、get_usage_logs@639
- `file_service.py` 26 个：check_file_access@77、_file_attached_to_visible_message@164、upload_file@222、list_files@294、get_file@337、delete_file@359、_find_first_forwarder@427、track_forward_reference@439、_remove_forward_reference@480、_orphan_file@502、cleanup_orphaned_files@513、get_user_forwarded_file_ids@549、orphan_cleanup_worker@561（**无 db 参数**——函数内 `async with async_session() as db`，wrap 也适用，但检查）、track_file_reference@585、get_file_referrers@620、get_ai_referenced_files@636、set_collaboration_mode@663、add_file_collaborator@692、remove_file_collaborator@745、get_file_collaborators@769、notify_file_changed@789、ai_read_file@836、ai_write_file@872、ai_delete_file@936、ai_share_file@951
做法：每文件加 import + `_ensure_repo` helper（模板见下），在每个函数体首行插 `    db = _ensure_repo(db)`。注意函数签名可能跨行，插入点=签名结束行(`:`)之后第一个缩进代码行；docstring 前插入也合法。

### Round 8 — 剩余 4 组
- `capability_versioning.py`（332 行，11 db-refs）：调用方 ai/、world 域 → **模式 B**（新建 repo 或复用？建议单独 repo 或复用通用型）
- `federation/federation_service.py`（1207 行，36 db-refs）：调用方 routers/federation_ws.py 等 → **模式 B**（超大文件）
- `audit_service.py`（105 行）+ `audit/postgres_backend.py`（172 行）+ `audit/__init__.py`（44 行）：调用方 routers/admin.py 等 → **模式 A**（拉高比例）
- `brain/brain_controller.py`（150 行，2 db-refs）：→ 评估后 A 或 B

## 五、Mode B 模板（照抄）

```python
# 服务文件顶部 import 区（保留 from sqlalchemy.ext.asyncio import AsyncSession）
from app.repositories.<xxx>_repo import <Xxx>Repository, SQLAlchemy<Xxx>Repository

# logger 定义之后加：
def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemy<Xxx>Repository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemy<Xxx>Repository(db_or_repo)
    return db_or_repo
```
每个用 db 的函数体首行：`db = _ensure_repo(db)`（幂等：传 repo 直接返回）。需要真 session 时用 `db.session`。

## 六、进度比例（目标 Mode A ≥70% 修改量）

已完成：A = social(约100行diff) + export(约40行diff) ≈ 140；B = memory/skill/agent/world/infra ≈ 290 行 diff。
后续：audit 用 A 拉高；file_service/opencli/conversation_log/federation/capability_versioning 用 B。
**注意**：若最终 A 占比不足 70%，可把 brain_controller、conversation_log_service（调用方仅 2 个 ai 运行时文件）升级为 Mode A 补足。

## 七、验证清单（每个 round 后）

1. `F:\Zhang\Copree\.venv\Scripts\python.exe -m py_compile <改动文件>`（无依赖，纯语法）
2. grep 确认：`db = _ensure_repo(db)` 数、残留 `db: AsyncSession` 签名、残留 `db.execute`（Mode A 文件应为 0）
3. 检查调用方：grep 服务名，确认所有调用点兼容（Mode A 必须全改；Mode B 不动）
4. **勿在容器内安装依赖**（2026-09-13 修订：禁止的是容器内 `pip`/`npm` 安装，宿主机与 CI 不受限。原表述为"勿执行 `pip install`"，字面过宽）；**勿动 ~/.dsh**


---

## 八、Round 7-8 完成情况（2026-08-23 续跑）

### Round 7 剩余 — content 3 文件（模式 B，content_repo.py）
- `conversation_log_service.py`（19 wraps）、`opencli_service.py`（12 wraps）、`file_service.py`（24 wraps，**排除** orphan_cleanup_worker——无 db 参数、函数内自建 session）
- 全部 py_compile 通过

### Round 8 — audit（模式 A）+ brain（模式 A）+ capability（模式 A）+ federation（模式 B）
- `repositories/audit_repo.py`、`capability_repo.py`、`federation_repo.py`（新建）
- `audit_service.py`：5 函数签名 `audit_repo: AuditRepository`；`audit/postgres_backend.py`：4 方法内部 `_ensure_repo` 兼容（session→db 变量替换）
- audit 调用方（调用点包装 `SQLAlchemyAuditRepository(db)`）：bootstrap.py、routers/admin.py（_log_admin_action 内部包装一次，其调用方无需改）、auth.py×2、ws.py、utils/error_handler.py
- `brain/brain_controller.py`：2 方法签名 `repo: AgentRepository`（复用 agent_repo）；调用方 ai/llm.py、routers/brain.py 包装 `SQLAlchemyAgentRepository(db)`
- `capability_versioning.py`：11 函数签名 `cap_repo: CapabilityRepository`；调用方 6 文件 13 处包装（bootstrap/executor/response_worker/llm/world_tools/world_chat_service；world 两文件用 `db.session` 桥接）
- `federation/federation_service.py`：33 函数 `_ensure_repo`（fetch_github_registry 的 db 可选 → `db = _ensure_repo(db) if db is not None else None`）

### 全量验证（2026-08-23）
- 62 个改动 .py 文件全部 `py_compile` 通过
- 剩余未处理的 AsyncSession 依赖服务：`services/memory/memory_distribution.py`（死代码转发，无调用者，保持原样）、`audit/__init__.py`（ABC 接口类型标注，实现已兼容）

### A/B 比例现状（git diff 行数，含未跟踪新 repo 文件）
- Mode A 服务：invitation(60)+search(15)+export(28)+audit_service(24)+postgres_backend(36)+brain(16)+capability(56) = **235 行**
- Mode B 服务：memory(73)+skill(51)+agent(42)+world(96)+infra(95)+content(79)+federation(41) = **477 行**
- 配套调用方 diff ≈ 300 行（A/B 混合）
- 新 repo 文件 12 个 ≈ 600 行（A 相关 5 个 ≈ 250 行，B 相关 7 个 ≈ 350 行）
- **A 占比约 28-40%（取决于统计口径），未达 70% 目标**。若需严格达标，候选升级：content 3 文件（B→A，调用方以路由+2 个 ai 文件为主）、conversation_log_service（调用方 6 个）、world 域（超大，成本高）。请用户决策。


---

## 九、Mode A 扩容完成（2026-08-23 goal round 2）

### world 域 4 文件：Mode B → **Mode A**（复用 world_repo，新增 session 属性）
- `world_chat_service.py`（11 函数签名 `world_repo: WorldRepository`，71 处 world_repo）
- `world_tools.py`（2 函数，12 处 `world_repo.session` 桥接保留）
- `world_suggestions.py`、`world_chat_commands.py`
- 调用方更新：`routers/worlds.py`（4 端点注入 world_repo）、`routers/world_proxy.py`（9 端点注入）、`world_turn.py`（包装 SQLAlchemyWorldRepository(db)）、`decision_skill.py`（包装）
- 注意：world_chat_service 内部调 capability 时用 `SQLAlchemyCapabilityRepository(world_repo.session)`

### content 2 文件：Mode B → **Mode A**（复用 content_repo）
- `conversation_log_service.py`（19 函数 `content_repo: ContentRepository`）、`opencli_service.py`（12 函数）
- 调用方：`routers/conversation_log.py`（7 端点注入 get_content_repo）、`routers/admin.py`（19 处包装 SQLAlchemyContentRepository(db)，用 git 恢复+正确正则重做）、`dm.py`/`agents.py`/`execute_command.py`/`response_worker.py`/`executor.py`（包装）；`world_chat_service.py:239` 传 world_repo（duck type 兼容）无需改
- **注意**：conversation_log/opencli 服务内部**不用 content_repo.session**，传 session 也兼容（duck type）——但已全部更新为显式传 repo

### 验证
- 67 个改动 .py 文件全部 py_compile 通过
- **Mode A diff 985 行 (72.6%) vs Mode B 371 行 (27.4%)——达标 70% 目标**

### 当前 Mode B 剩余（合理取舍，均为超大文件/运行时密集）
memory(6文件) / skill(4) / agent配套(2) / infrastructure(8) / federation(1) / file_service / group_type_service(历史) / audit/__init__.py(ABC接口)

---

## 十、Round 9 — 扫描遗漏补齐（2026-08-23 全量 AST 复查后）

用户跑 AST 扫描（services/ 下顶层 async def：有 db 参数 + 用 db + 无 _ensure_repo 重赋值）发现 13 文件 25 处遗漏，全部 Mode B 补齐：

| 文件 | 函数 | 复用 repo |
|---|---|---|
| federation_manager.py | _apply_display_name_update / _apply_avatar_update | federation_repo |
| context_compression_service.py | get_compression_threshold | memory_repo |
| memory_buffer.py | _batch_write_memories / archive_low_value_memories | memory_repo |
| vector_pipeline.py | _vectorize_message / hybrid_search / _hybrid_search_text_fallback | memory_repo |
| plugin/catalog.py | sync_plugins_to_db | plugin_repo（新建） |
| plugin/skill_bridge.py | apply_skill_plugins | plugin_repo |
| decision_skill.py | get_decision_rules / save_decision_rule / delete_decision_rule | world_repo |
| market_github.py | get_market_config / save_market_config / sync_item_to_github | infra_repo（execute 支持 params） |
| skill_sandbox.py | _handle_call | world_repo + chat 桥接 db.session |
| world_api_docs.py | ensure_sections_seeded / get_sections / sync_sections_from_docs / save_sections | world_repo |
| world_blocks.py | update_block_for_all_worlds | world_repo |
| world_event_hook.py | notify_group_message / _enqueue | world_repo |
| world_scheduler.py | sweep_worlds | world_repo |

要点：
- 新建 `repositories/plugin_repo.py`（plugin 域通用 repo）
- skill_sandbox._handle_call 内 chat.message 的 get_group/create_message 是纯 session 函数 → 传 `db.session` 桥接；
  world_service 的 update_world/get_world_data 等已是 Mode A（repo 参数）→ 直接传 db
- market_github 用 infra_repo（其 execute 支持 params，world_repo 不支持）；federation_manager 用 federation_repo（支持 params）
- 验证：13 文件 + plugin_repo 全部 py_compile 通过；用户 AST 扫描脚本复跑 **No obvious db conversion issues found**
- 调用面安全：bootstrap / routers（admin/market/plugins/api_docs/world_proxy）/ ai（executor/llm/response_worker）/ chat.message 等调用方传 session 或 repo 均幂等兼容
- 本次改动 +143/-2（13 文件），**未提交 git**（等用户 review）

### 更新后 Mode A/B 比例（估算）
- 新增 Mode B ≈ 143 行（原 371 → ~514）；Mode A 985 行不变
- A 占比 = 985 / (985+514) ≈ **65.7%**（仍接近 70%；若需严格达标，可把 plugin 2 文件或 memory 3 文件升 Mode A）
