"""指向 users / agents 的外键，删除语义的唯一真源。

为什么要有这个文件
------------------
users 与 agents 是全库的中心节点：删一次会牵动几十张表。这些外键原先大多没写
ondelete（PostgreSQL 默认 NO ACTION），于是「先删谁、后删谁」只能靠业务代码里的
注释和记忆维持——换一行顺序、或者新开一条删除路径，线上就是 500
（2026-11-15「删除 AI 报服务器内部错误」就是被 dm_messages.sender_id 挡下来的）。

现在把删除语义写进数据库，并按这里的表逐列落地。三种语义：

CASCADE   从属数据：主体没了，它就不该存在（私信会话与消息、入群邀请、粗糙记忆、
          自习记录、AI 的配置/记忆/技能/触发器等）。
SET NULL  事实记录：主体没了，记录本身仍然是事实，必须留着（用量与对话日志、审计
          字段 updated_by/created_by、兑换码、商城作品）。这类列必须允许为空。

两条特别约定
------------
1. agents.user_id 是 AI 在 users 表里的**影子账号**。删 AI 的唯一入口是
   delete_agent()：它先把群/世界交还主人、再删 agent 本体、最后回收影子账号。
   数据库这一层只能 SET NULL——如果写成 CASCADE，任何绕过业务、直接删影子账号的
   路径都会静默删掉 AI（额度返还、资产交还全部跳过）。SET NULL 的后果是「AI 还在、
   账号没了」，可以被巡检发现并补救，比静默丢数据安全。
2. ai_conversation_logs.user_id 是 CASCADE 而不是 SET NULL：这张表按记账人分区，
   记账人消失后这些行既不能归属、也没有对账对象，留着只是噪声。其余用量表
   （api_usage_log）保留事实，所以是 SET NULL——两边不是笔误。

机器怎么守住
------------
tests/test_fk_delete_policy.py 会遍历 SQLAlchemy metadata：
- 每个指向 users/agents 的外键都必须在这张表里出现（新增裸外键 → 失败）；
- metadata 里的 ondelete 必须与本表一致；
- 迁移 0128 的冻结快照必须与本表一致（数据库侧不会悄悄漂移）。
所以改删除语义的正确顺序是：先改这里 → 再改模型列 → 再写迁移。
"""

from typing import Final

CASCADE: Final = "CASCADE"
SET_NULL: Final = "SET NULL"

# (表名, 列名) → ondelete。按表名、列名排序，便于人工比对。
FK_POLICY: Final[dict[tuple[str, str], str]] = {
    # ── AI 的从属数据 ───────────────────────────────────────────────
    ("agent_alarms", "agent_id"): CASCADE,
    ("agent_attention", "agent_id"): CASCADE,
    ("agent_collaborators", "agent_id"): CASCADE,
    ("agent_collaborators", "user_id"): CASCADE,
    ("agent_config_history", "agent_id"): CASCADE,
    ("agent_configs", "agent_id"): CASCADE,
    ("agent_creation_drafts", "user_id"): CASCADE,
    ("agent_emoji_packs", "agent_id"): CASCADE,
    ("agent_history_entries", "agent_id"): CASCADE,
    ("agent_history_seq", "agent_id"): CASCADE,
    ("agent_knowledge", "agent_id"): CASCADE,
    ("agent_skill_relations", "agent_id"): CASCADE,
    ("agent_skills", "agent_id"): CASCADE,
    ("agent_state_chain", "agent_id"): CASCADE,
    ("agent_state_chain_node", "agent_id"): CASCADE,
    ("agent_state_stack", "agent_id"): CASCADE,
    ("agent_triggers", "agent_id"): CASCADE,
    ("agent_user_configs", "agent_id"): CASCADE,
    ("agent_user_configs", "user_id"): CASCADE,
    ("agent_voice_configs", "agent_id"): CASCADE,
    ("agent_workspace", "agent_id"): CASCADE,
    ("channel_conversation_usage", "agent_id"): CASCADE,
    ("channel_token_policy", "agent_id"): CASCADE,
    ("context_segments", "agent_id"): CASCADE,  # 压缩原文的短期缓存，AI 删了就该一起没
    ("member_silences", "agent_id"): CASCADE,
    ("opencli_agent_whitelist", "agent_id"): CASCADE,
    ("pending_messages", "agent_id"): CASCADE,
    ("personality_anchors", "agent_id"): CASCADE,
    ("proxy_judgements", "agent_id"): CASCADE,
    ("structured_records", "agent_id"): CASCADE,
    ("unread_summary_cache", "agent_id"): CASCADE,
    ("vector_acceleration_requests", "requester_id"): CASCADE,  # 短期加速请求，不是账
    ("world_agents", "agent_id"): CASCADE,

    # ── AI 本体与影子账号 ───────────────────────────────────────────
    ("agents", "owner_id"): CASCADE,
    ("agents", "user_id"): SET_NULL,  # 影子账号：删账号不静默删 AI，见文件头约定 1

    # ── 用户的从属数据 ──────────────────────────────────────────────
    ("chat_imports", "agent_id"): CASCADE,      # 导入记账：AI 没了，这批记账也就没有主体
    ("chat_imports", "creator_user_id"): CASCADE,
    ("dm_messages", "sender_id"): CASCADE,  # 与 dm_sessions 的级联同向
    ("dm_sessions", "user1_id"): CASCADE,
    ("dm_sessions", "user2_id"): CASCADE,
    ("group_invitations", "invitee_id"): CASCADE,
    ("group_invitations", "inviter_id"): CASCADE,
    ("group_join_requests", "user_id"): CASCADE,
    ("redemption_records", "user_id"): CASCADE,
    ("rough_memories", "user_id"): CASCADE,
    ("study_records", "user_id"): CASCADE,
    ("study_settings", "user_id"): CASCADE,
    ("user_api_assignments", "user_id"): CASCADE,
    ("user_api_profiles", "user_id"): CASCADE,
    ("user_api_providers", "user_id"): CASCADE,
    ("user_api_scenario_off", "user_id"): CASCADE,
    ("user_api_scenario_routes", "user_id"): CASCADE,
    ("user_api_scenario_status", "user_id"): CASCADE,
    ("user_dm_preferences", "user_id"): CASCADE,
    ("user_group_preferences", "user_id"): CASCADE,
    ("user_plugin_prefs", "user_id"): CASCADE,
    ("worlds", "owner_id"): CASCADE,

    # ── 必须是事实的记录：主体没了也要留 ────────────────────────────
    ("ai_conversation_logs", "agent_id"): SET_NULL,
    ("ai_conversation_logs", "user_id"): CASCADE,  # 见文件头约定 2
    ("api_usage_log", "agent_id"): SET_NULL,
    ("api_usage_log", "user_id"): SET_NULL,
    ("conversation_log_config", "updated_by"): SET_NULL,
    ("external_identities", "bound_user_id"): SET_NULL,
    ("external_identities", "user_id"): SET_NULL,
    ("group_invitations", "approved_by"): SET_NULL,
    ("group_join_requests", "resolver_id"): SET_NULL,
    ("opencli_command_whitelist", "created_by"): SET_NULL,
    ("opencli_config", "updated_by"): SET_NULL,
    ("opencli_usage_log", "agent_id"): SET_NULL,
    ("redemption_codes", "created_by"): SET_NULL,
    ("redemption_codes", "used_by"): SET_NULL,
    ("system_settings", "updated_by"): SET_NULL,
    ("world_market_items", "author_id"): SET_NULL,  # 删号不连带下架作品（author_name 已冗余）
}
