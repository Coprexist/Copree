# AI 的两种 id：agents.id 与虚拟账号 users.id

同一个 AI 在库里有两种身份，用途不重叠。写代码时先判断「这个位置会不会同时是某个人类」，
答案决定用哪个 id。本文件是判据的单一来源，各模型字段的注释引用同一条。

## 判据

> **人和 AI 共处一张表的地方 → user_id；AI 独有的地方 → agents.id。**

- 共有（人类也会出现）：`users`、`messages.sender_id`、`group_members.member_id`、
  好友、@提及、世界绑定。AI 在这里用它的虚拟账号，与人类同构。
- AI 独有：`agents`、文件空间/沙箱、记忆、知识库、技能、模型与工具配置、用量。

## 归属表

| 位置 | 用哪个 id | 说明 |
|---|---|---|
| `users.id` 及所有外键指向 users 的列 | user_id | 人与 AI 同构 |
| `messages.sender_id` | user_id | `sender_type='ai'` 时是该 AI 的虚拟账号 |
| `group_members.member_id` | user_id | 同上 |
| 好友、@提及、`world_bindings` | user_id | 2026-08-12 `63af43dd`：AI 对外标识统一 user_id |
| `agents.id` | agents.id | AI 本体 |
| `file_metadata.owner_id`（owner_type='ai'） | **agents.id** | 文件是 AI 独有；human → users.id |
| `data/agents/{id}/`、沙箱、记忆、知识库、技能、用量、模型配置 | agents.id | `paths.agent_dir`、`executor.py:949` 传 agent.id |

## 为什么不能只留一套

- 消息与群成员表里人类和 AI 必须同构：一条 `WHERE sender_id = ?` 要能同时取到人与 AI 的发言，
  换成两套 id 这个查询就没了。
- 文件与沙箱是 AI 独有：用虚拟账号会把「文件空间」和「发言身份」混为一谈，还会在
  `data/agents/{虚拟账号}/` 下留出没人清理的目录（2026-07 那批存量就是这么来的）。

## 两套 id 数值会撞

`agents.id` 与另一 AI 的 `agents.user_id` 数值可能相等（线上实例：agent 29 的虚拟账号是
user 49，而文件归属的 agent 49 的 id 就是 49）。因此：

- 一个字段只存一种 id，不写 `id == X OR user_id == X`——一次命中两行，
  `scalar_one_or_none` 抛 `MultipleResultsFound`（文件预览/下载整片 500 就是这么来的）。
- 需要兼容历史数据时，归一只有一个入口（`file_service._owner_agent`），不要散落到各查询。

## 存量归正

2026-07 一批 AI 文件的 `owner_id` 与路径写成了虚拟账号 id（16 → agent 10、41 → agent 25），
物理文件落在 `data/agents/16/`、`data/agents/41/`，AI 自己反而读不到。

```bash
python scripts/repair_ai_file_owner_ids.py            # 只报告
python scripts/repair_ai_file_owner_ids.py --apply    # 真改
```

## 新增 AI 独有表时

- 列名用 `agent_id`，外键指向 `agents.id`，不要叫 `user_id`。
- 必须多态时（owner_type + owner_id），在字段注释里写清各类型对应的表，并在写入侧校验。
- 前端页面、权限判定里出现「这个 AI 是谁」时，先确认拿的是本体 id 还是虚拟账号，不要靠数值猜。
