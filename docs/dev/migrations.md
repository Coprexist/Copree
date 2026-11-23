# 数据库迁移（alembic）的约定

128 个迁移，一条线性链，head 在 `backend/alembic/versions/0128_fk_ondelete_policy.py`。
升级命令永远是 `alembic upgrade head`，启动时由 `app/bootstrap.py` 自动执行。

## 编号

| 范围 | 形态 | 说明 |
|---|---|---|
| 0001 – 0068 | hash（`f2a3b4c5d6e7`） | 早期 alembic 默认风格，**永不修改** |
| 0069 – 0128 | 4 位顺序号（`0069`…`0128`） | 现行约定，严格 +1，无跳号 |

**老 id 为什么不能改成顺序号**：`alembic_version.version_num` 里存的就是
revision 字符串本身。把 `b8d2e4f6a1c3` 改名成 `0007`，所有已升级实例的
`alembic current` 立刻找不到自己的版本，`upgrade head` 报
`Can't locate revision`。这不是风格问题，是升级路径的契约。

**新迁移取号流程**：

1. `git pull`，看 head（`alembic heads`）；
2. 取 head 编号 +1，文件名 `0NNN_一句话说明.py`（文件名前缀必须等于 revision）；
3. `down_revision` 写 head 的 revision。

多人并行时可能撞号（两边都取 `0127`），alembic 会直接报重复——撞了就顺延，
不要改别人的号。

`tests/test_migration_chain.py` 把这三条变成红灯：链必须唯一线性、
0069 之后必须是连续 4 位顺序号、文件名必须与 revision 一致。

### 编号会用完吗（9999 之后）

按目前的节奏（0069 → 0126 用了 57 天，约 1 个/天），从 0126 到 9999 大约 **27 年**。
真到那一天，功能上**不会坏**：alembic 靠 `down_revision` 链定位，不按字符串排序，
`10000` 照样能升级。坏掉的只有「文件名排序 = 时间线」这个给人看的性质
（`9999` 与 `10000` 交界处 `ls` / `git log` 会错位一格）。

届时的做法是 **alembic 官方的分卷**：新建 `backend/alembic/versions_v2/`，在
`alembic.ini` 的 `version_locations` 里把它加上，新迁移一律写进新目录；老目录冻结不动。
这样既不用改历史 id，也保住了「目录内按编号排序」的可读性。

所以现在**不用预留位数**：`tests/test_migration_chain.py` 的 4 位校验会在第 10000 个
迁移时变红——那正是该做这次决策的时刻，比提前猜一个规则可靠。

## 迁移必须自包含

**迁移里不要 import 应用代码**（`app.*`）。理由不是洁癖：迁移是时间胶囊——
它问的是「当时的代码与数据」，而 `app` 里的常量会变。

0123/0124/0125 曾经 `from app.utils.pure.provider_presets import PRESETS`，
于是同一版迁移在不同时间给出不同结果（预设改了地址、加了模型，还没升级的实例就会拿到
另一个答案）。现在它们读 `alembic/versions/_provider_presets_snapshot.json`
（冻结于 2026-11-23，27 条 × 10 字段）。**改动预设表时不要同步这个 JSON**，它就该停在
那一天。

需要别的历史数据时照此办理：把数据冻结成迁移目录里的 JSON/常量，而不是去查活代码。

## 从空库重建整条链是已知不成立的

`tests/conftest.py` 的 `migrated_db` 用
`Base.metadata.create_all` 建表，不跑 alembic——历史迁移链里有依赖
早期手工建表（`init-db.sql`）的步骤，从空库 `upgrade head`
会在中途报 `relation "agents" does not exist`。

所以：

- **验收标准是「已部署实例从当前 head 升级」**，不是「空库跑完整条链」；
- 不要为了让它能从空库跑而改动历史迁移（那会破坏已升级实例的版本定位）；
- 真要新建库，用 `create_all` 那条路（与测试一致）。

## 补表迁移：模型有了、迁移链没有

「新库走 `create_all`、老库走迁移链」这条双轨有一个必然的裂缝：**只加模型、忘了写迁移**，
新装实例一切正常，升级实例却缺整张表。`study_records` / `study_settings` 就是这么丢的
（2026-08 建表写在同一提交的 `prestart.py` 补丁清单里，后来 prestart 收敛成「只跑 alembic」，
补丁清单被删掉，迁移链上从来没有这两张表；而且补丁建的版本没有外键）。

0127 是补表迁移的范例，写法上有两条硬要求：

- **先判存在再建**（`sa.inspect(bind).get_table_names()`）：新库已经 `create_all` 建过表，
  `op.create_table` 会直接失败；
- **表已存在就只补缺的部分**（外键、约束），否则升级实例拿到的仍是残缺结构。

`downgrade` 不删表：降级之后老代码仍要能读到业务数据，删表是不可逆的数据损失。

## 写一条新迁移时要问的几件事

- 加列：给 `server_default`，否则已有行会拿到 NULL 而模型层可能不允许；
- 时间列：一律 `DateTime()`（无时区，naive），与全库一致
  （见 `tests/test_schema_contract.py`）；
- 删/改数据：先 `SELECT` 统计影响面，迁移里写清楚"为什么可以删"；
- 幂等：迁移集合指纹失效或手工 `alembic upgrade` 都会让迁移再跑一遍，
  插入类操作要按业务键去重（0125 就是按「同用户 + 同地址」认同一家厂商）。
