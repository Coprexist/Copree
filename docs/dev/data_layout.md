# 数据根与目录布局 / Data Root & Layout

> **单一来源**：数据根是 `settings.data_dir`（认环境变量 `DATA_DIR`），根下的目录结构只写在
> [backend/app/paths.py](../../backend/app/paths.py)。动路径前先读它——模块里另拼一份就是
> 「同一个世界两份文件、文件不见了」的来源。

## 1. 根目录怎么定

| 场景 | 值 | 由来 |
|------|----|------|
| 容器（compose） | `/app/data` | `docker-compose.yml` 的 `backend.environment: DATA_DIR: /app/data`——`environment` 盖过 `env_file`，宿主路径漏不进来 |
| 容器（裸 `docker run`） | `/app/data` | 镜像里的 `ENV DATA_DIR=/app/data` |
| 宿主机直跑 | `<repo>/data` | `_default_data_dir()` 按部署布局推：容器里 backend 挂在 `/app`，宿主上是 `<repo>/backend`，两种布局都落回仓库的 `data/` |
| 显式指定 | `DATA_DIR` 的值 | 要把数据挪到别的磁盘（如 `/mnt/data`）时用 |

`.env` 里的 `DATA_DIR` 是**宿主机视角**（compose 拿它做挂载源 `${DATA_DIR:-./data}:/app/data`），
容器内那份由 compose 显式钉死。同一个变量两种视角：在容器里给宿主路径，应用照着写，
文件就落在容器可写层，`docker compose up` 重建一次即丢。

## 2. 根下有什么

| 常量 / 函数 | 相对数据根 | 用途 |
|------------|-----------|------|
| `AGENTS_DIR` / `agent_dir(id)` | `agents/` | 每个 AI 的文件空间，同时是它的代码沙箱目录 |
| `WORLDS_DIR` / `world_dir(id)` | `worlds/` | 世界：`{id}/` 是代码区与沙箱工作目录，`{id}/data/` 是数据区 |
| `AI_SKILLS_DIR` | `world_ai_skills/` | 设计侧技能库（造物主全局共享，与世界内 `skills/` 不是一回事） |
| `PLUGINS_DIR` | `plugins/` | 用户安装的插件（内置插件在 `backend/plugins/`，随代码走，用户包撞内置一律拒） |
| `PLUGIN_PACKAGES_DIR` | `plugin_packages/` | 插件安装包仓库 |
| `MARKET_DIR` | `market/` | 世界商城商品包 |
| `BACKUPS_DIR` | `backups/` | 完整备份产物 |
| `SHARED_MEMORIES_DIR` | `shared_memories/` | 全局共享记忆（共振 AI 的 `cross/` 软链指向它） |
| `THEME_VOTES_FILE` / `THEME_VOTES_R2_FILE` | `theme_votes*.json` | 主题选色投票 |
| —（其他模块自己的单点文件名） | `maintenance_msg.json`、`encryption_key`、`uploads/`、`attachments/`、`napcat/`、`postgres/` | 维护公告、开发环境的加密密钥、附件（`/app/uploads` 是另一个挂载点）、消息附件、QQ 协议端缓存、数据库目录 |

规则两条：

1. **模块里不写 `Path("data/...")`**——加目录只改 `app/paths.py`。
2. **实体目录只走 `world_dir()` / `agent_dir()`**——七处各拼一遍的写法曾经让世界文件分成两份。

只有一处解析的根文件名（如上表最后一行的维护公告、密钥）留在各自的模块里：
搬进 `paths.py` 只多一层间接，不消除任何重复。

## 3. 怎么查现在用的是哪一份

```bash
docker exec ai_group_backend python -c "from app import paths; print(paths.DATA_DIR, paths.WORLDS_DIR)"
```

测试侧：`backend/tests/conftest.py` 在导入任何 app 模块**之前**把 `DATA_DIR` 指向临时目录
（`tempfile.mkdtemp`），用例因此永远不写真实数据卷——`app/paths.py` 在 import 时就把布局定死，
再晚改就来不及。

## 4. 迁移、备份与恢复（路径层面）

- **升代码不搬文件**：没有哪个数据库迁移（alembic）会碰数据目录。改了 `DATA_DIR` 或 compose 里那份之后
  必须 `docker compose up -d --build backend` 重建容器——`docker restart` 不重读环境变量。
- **数据根是 bind mount**（`./data:/app/data`）：重建容器、换镜像、`docker compose down -v` 都不动它
  （docker 只删 named volume）；`rm -rf data` 会。
- **换机器**：带走整个 `data/` + 数据库 dump（或直接恢复完整备份）即可，视界与 AI 文件跟着走。
- **完整备份包含什么、恢复是什么语义**（回到那个时间点、卷里多出来的文件不会删）见
  [管理与开发者手册 §8](../guides/管理与开发者手册.md#8-备份与恢复)。
