# 代码沙箱（Code Sandbox）

> 状态：已落地（2026-09-26）。实现：`app/services/sandbox/`；守卫用例：`backend/tests/test_sandbox_layers.py`。

## 1. 一句话

平台里"跑一段不受信 Python"只有**一个实现**：`app/services/sandbox/runner.py`。
世界代码、AI 自己的脚本都是它的调用方，差别只有工作目录、环境变量、隔离档位三处。

## 2. AI 的沙箱就是它的文件空间

AI 原本就有独立目录 `data/agents/{agent_id}/`（代码里一直叫它"AI 沙箱目录"，OpenCLI 的文件操作就用它）。
路径本身只由 `app/paths.agent_dir()` 解析（数据根怎么定见[数据根与目录布局](./data_layout.md)）。
沙箱不另造一块存储，而是**把这同一个目录锁成代码的全部世界**：只看得见它、只写得进它。
两条边界（能碰的文件 / 能用的算力）因此重合。

| 能力 | 落在哪里 | 边界来源 |
|------|---------|---------|
| 代码执行 | 沙箱目录 | `run_script` 工具 / 决策技能 `do.run_script` → `sandbox/runner` |
| 脚本存取（`run_script` 的 `path`、决策技能 `do.entry`） | 沙箱目录 | `agent_sandbox.script_path`（越界直接拒绝） |
| OpenCLI 文件操作 | 沙箱目录 | `opencli_service._resolve_agent_path` |
| `file_*` 工具 | 沙箱目录 | 路径经 `file_service.ai_stored_path` 前缀化，元数据鉴权照旧 |

`file_*` 曾经落在共享的 `data/` 树（历史遗留）：同一个「我的文件」分成两处，`file_write` 写出来的
脚本 `run_script` 里 `os.listdir` 找不到，两侧还各留一份同名不同内容的文件——AI 改完再读像是
「读到旧缓存」。现在四个入口（脚本、记忆、OpenCLI、`file_*`）都落在 `data/agents/{id}/`：

- AI 面向的路径不带前缀，存储层用 `ai_stored_path` 拼上 `agents/{id}/`，回显用 `ai_view_path` 剥掉；
  规范化后仍以 `..` 开头的一律拒绝。
- 存量记录的搬迁见 `backend/scripts/migrate_ai_files_to_sandbox.py`（幂等；目标位置已有同名文件时
  不覆盖——沙箱那一份才是真在跑的，被取代的旧副本归档到 `agents/{id}/.superseded/`，不删）。

## 3. 为什么收成一层

世界沙箱（`world_sandbox.py`）先落地，隔离原语在 `sandbox_isolate.py`。给 AI 加沙箱时若照抄一份，
就会出现两套 subprocess 代码各自演化：配额在一处改了、另一处没改，超时 kill 在一处修了、另一处漏了。
现在两边的**执行、隔离、配额、超时、输出截断**都在 `runner.py`：

- 世界侧：`world/world_sandbox.py` 只提供工作目录（`data/worlds/{id}`）、`WORLD_*` 环境与 `worlds.config` 配额，
  外加世界特有的"跑完语法自检"。
- AI 侧：`sandbox/agent_sandbox.py` 只提供 `data/agents/{id}`、`AGENT_*` 环境与固定配额。

一条守卫用例把这件事钉住：`apply_isolate` 的调用点只允许存在于
`sandbox/runner.py`（一次性执行）与 `world/skill_runner.py`（协议式执行，边跑边与宿主对话）。
多出第三处，测试当场红。

## 4. 隔离构成

子进程内施加（父进程到不了那里，档位只能经环境变量 `SANDBOX_*` 传）：

| 层 | 做什么 | 实现 |
|----|--------|------|
| Landlock | 文件系统锁死在授权目录（`work_dir` 读写 + 额外只读目录），其余路径 EACCES | `sandbox_isolate.apply_landlock` |
| seccomp | 黑名单禁 execve/mount/ptrace/内核接口/xattr；可选再禁网络与 fork/clone | `sandbox_isolate.apply_seccomp` |
| rlimit | 内存（RLIMIT_AS）、CPU 秒、单文件 4MB、进程数 16、core 关闭 | `runner.apply_rlimits` |
| 超时 | 独立进程组 + 墙钟超时 → `killpg` 连子孙进程一起杀 | `runner.run_code` |
| env | 白名单基座（PATH/LANG/TZ/HOME + `SANDBOX_LIB_DIR`），不继承后端密钥 | `runner.base_env` |

隔离是纵深防御的一层：Landlock/seccomp 施加失败只告警并降级，不阻断功能（非 x86_64 平台 seccomp 自动跳过）。

## 5. 档位

| 调用方 | work_dir | readonly | deny_net | deny_fork |
|--------|----------|----------|----------|-----------|
| 世界代码 `run_world_code` | `data/worlds/{id}` | 计划模式强制 | 否（受控 API 是 HTTP） | 否（可能用线程池） |
| 世界触发 `run_world_trigger` | 同上 | 同上 | 否 | 否 |
| AI 脚本 `run_agent_code` | `data/agents/{id}` | 否 | **是** | **是** |

AI 脚本禁网络是刻意的：要联网有 `web_search`/`web_fetch` 等平台工具，不该从脚本里开洞；
要发消息也没有句柄——脚本把要说的话 `print` 成 JSON，由宿主代发（见决策层文档）。

## 6. 同一个 AI 的脚本串行

`agent_sandbox.agent_lock(agent_id)` 给**每个 AI 一把 `asyncio.Lock`**，只包住 `run_agent_code` 的**执行段**。
等 `LOCK_WAIT_SECONDS = 20.0`（两倍单脚本墙钟 10s）还拿不到，就返回**如实失败**，不让调用方以为脚本跑过了。

为什么要在这一层串行：AI 的文件空间**只有一个目录**，脚本惯用「读 JSON → 改 → 写回」；而接话判定只按
**(AI × 群)** 串行（`ai/chat_chain.py:388` 的 `try_claim`），跨群、跨情景（本体 `run_script`、各群决策技能、
闹钟情景）不串——两份脚本各读旧账本再各写一遍，就是 lost update。

## 7. 结果与失败文案

统一返回 `{success, stdout, stderr, exit_code, duration_ms, timed_out, reason}`。
失败结果的统一形状由 `runner.fail_result()` 给出（本轮从内部 `_fail` 提为公开）：调用方在起进程之前就失败
（例如等不到上面那把锁）也用它，不必各写一份。

`reason` 必须是人话：被信号杀掉时 stderr 往往是空的，只留一个负数退出码，写脚本的 AI
只能靠猜。`runner.exit_reason` 把常见信号翻译成"CPU 时间超限（本次配额 CPU 5s / 内存 96MB）"
这类可行动的原因（实测：`while True: pass` 在 5s 处收 SIGXCPU）。

## 8. 入口清单

- `run_world_code(world, code|entry, background, readonly)` —— 世界代码（工具 `run_world_code` 调用）
- `run_world_trigger(world, event, entry, ...)` —— 世界 `main.py:handle(event)`（事件钩子调用）
- `run_agent_code(agent_id, code|entry, ctx, deny_net, deny_fork)` —— AI 脚本
- 工具 `run_script(code, path?)` —— AI 面向入口：`path` 给定则先落盘到文件空间再执行（攒自己的脚本库）
- 决策技能 `do.run_script` —— 经 `run_agent_code` 执行，事件上下文走 `DECISION_CTX`
  （JSON：本次情景的全部字段 + 引擎补的 now/today/weekday/hour，见 `docs/dev/decision_layer.md` §2）
- 例外：`world/skill_sandbox.py` 走 stdin/stdout JSON 行协议（世界 skill 的 ctx 能力转发），
  自己起进程但复用同一套隔离库与 rlimit

## 9. 新增一个 owner（例如"世界外的某类实体"）

1. 定目录：在 `config` 里给出唯一来源，别在业务代码里拼字面量。
2. 写适配层：`Policy` + `base_env()` 补自己的变量 → 调 `runner.run_code`。
3. 加档位选择与一条用例：越界读被拒、网络按档位、超时能收回。

## 10. 实测（容器内，2026-09-26）

| 场景 | 结果 |
|------|------|
| AI 脚本写文件 | `cwd=/app/data/agents/1`，文件落在该目录 |
| 读 `/etc/hostname` | `PermissionError`（Landlock） |
| `socket.socket()` | `PermissionError`（seccomp 禁网络） |
| `while True: pass` | 5s 收 SIGXCPU，reason 给出配额说明 |
| `time.sleep(30)`（墙钟 1s） | `timed_out=true`，进程组被杀 |
| 世界代码读 `/etc/hostname` | 拒绝（重构前后一致） |
| 世界 `handle(event)` | 结果原样返回，缺入口时 reason 保留"入口文件不存在" |
