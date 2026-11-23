# 删除语义：谁跟着一起走，谁留下来

全库有 67 个外键指向 `users.id` 或 `agents.id`。这两个表是全库的中心节点，删一次会
牵动几十张表。原先其中 20 个没写 `ondelete`（PostgreSQL 默认 `NO ACTION`），于是
「先删谁、后删谁」只能靠业务代码里的注释和记忆维持：

- 换一行顺序、或者新开一条删除路径，线上就是 500
  （2026-11-15「删除 AI 报服务器内部错误」，卡在 `dm_messages.sender_id`）；
- `db_reset` 这类工具必须开 `session_replication_role = replica` 才能清表。

现在删除语义写进数据库，唯一真源是 **`backend/app/models/fk_policy.py`**。

## 两种语义

| 语义 | 用在 | 例子 |
|---|---|---|
| `CASCADE` | **从属数据**：主体没了，它就不该存在 | 私信会话与消息、入群邀请、粗糙记忆、自习记录、AI 的配置/技能/触发器等 |
| `SET NULL` | **事实记录**：主体没了，记录本身仍然是事实 | 用量 `api_usage_log`、审计 `updated_by`/`created_by`、兑换码、商城作品 |

判据只有一个问题：**这行数据在没有主人之后还有意义吗？**
有意义（账、审计、已发出的兑换码、别人下载过的作品）→ `SET NULL`；没意义 → `CASCADE`。

`SET NULL` 的列必须允许为空。0128 把三个原来 `NOT NULL` 的列改成可空：
`api_usage_log.user_id`、`redemption_codes.created_by`、`world_market_items.author_id`
——它们原来是 NOT NULL，只是因为以前不会有人被删。

## 两个容易踩的特别约定

1. **`agents.user_id` 是 `SET NULL`，不是 `CASCADE`。**
   这一列是 AI 在 `users` 表里的**影子账号**。删 AI 的唯一入口是
   `agent_service.delete_agent()`：它先把群/世界交还主人（业务逻辑）、再删 agent 本体、
   最后回收影子账号。如果写成 `CASCADE`，任何绕过业务、直接删影子账号的路径都会
   **静默删掉 AI**，额度返还与资产交还全部跳过。`SET NULL` 的后果是「AI 还在、账号没了」——
   可以被巡检发现并补救，比静默丢数据安全。

2. **`ai_conversation_logs.user_id` 是 `CASCADE`，而 `api_usage_log.user_id` 是 `SET NULL`。**
   不是笔误：前者按记账人分区，记账人消失后这些行既不能归属、也没有对账对象，留着只是
   噪声；后者是扣费流水，必须在主体消失后仍然能查。

## 加一个新外键的流程

1. 想清楚它属于哪一类，写进 `app/models/fk_policy.py`（按表名字母序）；
2. 模型列上写 `ForeignKey(..., ondelete=...)`，与策略一致；
3. 写一条新迁移（按 `docs/dev/migrations.md` 的编号规则），并带上该迁移的
   `FROZEN_POLICY` 快照；
4. `SET NULL` 的列记得 `nullable=True`。

漏掉任何一步都会被 `tests/test_fk_delete_policy.py` 拦下：

- 模型里新增了指向 `users`/`agents` 的裸外键 → 失败（「没登记删除语义」）；
- 模型 `ondelete` 与策略不一致 → 失败；
- 最新迁移的 `FROZEN_POLICY` 与策略文件不一致 → 失败；
- `SET NULL` 的列还是 `NOT NULL` → 失败；
- 另外还有两条行为测试：真删一次 AI、真删一个账号，从属数据清空、事实记录留成 `NULL`。

## 一个曾经的坑：模型有列，库里有列，但没有约束

`group_invitations.approved_by` 是最典型的：模型声明了 `ForeignKey("users.id")`，
而当年那条迁移只写了 `op.add_column(..., sa.Column('approved_by', sa.Integer(), nullable=True))`。
于是 `create_all` 建的新库有这个外键、升级上来的老库没有——同一个版本，两套结构。
0128 把它补齐（`SET NULL`），并把「模型与迁移链是否一致」这件事交给上面那套门禁。

## 删 AI 现在长什么样

```python
await _transplant_ai_assets(db, agent_id, ai_user_id, owner_id)  # 业务：群/世界交还主人
await db.delete(agent)     # 从属表由 agents 级联
await db.flush()
if ai_user:
    await db.delete(ai_user)  # 私信、邀请、记忆由 users 级联
    await db.flush()
```

顺序不再敏感，唯一不可省的是「先交还资产」——那是业务逻辑，数据库替不了。
