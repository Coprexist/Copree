# 代码沙箱（Code Sandbox）

> 状态：已落地（2026-09-26）。实现：`app/services/sandbox/`；守卫用例：`backend/tests/test_sandbox_layers.py`。

## 1. 一句话

平台里"跑一段不受信 Python"只有**一个实现**：`app/services/sandbox/runner.py`。
世界代码、AI 自己的脚本都是它的调用方，差别只有工作目录、环境变量、隔离档位三处。

## 2. AI 的沙箱就是它的文件空间

AI 原本就有独立目录 `data/agents/{agent_id}/`（代码里一直叫它"AI 沙箱目录"，OpenCLI 的文件操作就用它）。
沙箱不另造一块存储，而是**把这同一个目录锁成代码的全部世界**：只看得见它、只写得进它。
两条边界（能碰的文件 / 能用的算力）因此重合。

| 能力 | 落在哪里 | 边界来源 |
|------|---------|---------|
| 代码执行 | 沙箱目录 | `run_script` 工具 / 决策技能 `do.run_script` → `sandbox/runner` |
| 脚本存取（`run_script` 的 `path`） | 沙箱目录 | `agent_sandbox.script_path`（越界直接拒绝） |
| OpenCLI 文件操作 | 沙箱目录 | `opencli_service._resolve_agent_path` |
| `file_*` 工具 | **共享的 `data/` 树** | 数据库元数据鉴权（`file_service`），不是目录隔离 |

> ⚠️ 两套空间并存是历史遗留：`file_*` 工具写的是共享 `data/` 树、靠 `file_metadata` 判权限
> （`file_read` 的工具描述声称"只能访问 `/app/data/agents/{your_id}/`"，与实现不一致）。
> 沙箱选的是独立目录那套（真正的文件系统隔离），所以 `run_script` 的脚本不会出现在 `file_list` 里。
> 把 `file_*` 也迁到独立目录的收敛方案不在本次范围内，先如实记录。

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

## 6. 结果与失败文案

统一返回 `{success, stdout, stderr, exit_code, duration_ms, timed_out, reason}`。

`reason` 必须是人话：被信号杀掉时 stderr 往往是空的，只留一个负数退出码，写脚本的 AI
只能靠猜。`runner.exit_reason` 把常见信号翻译成"CPU 时间超限（本次配额 CPU 5s / 内存 96MB）"
这类可行动的原因（实测：`while True: pass` 在 5s 处收 SIGXCPU）。

## 7. 入口清单

- `run_world_code(world, code|entry, background, readonly)` —— 世界代码（工具 `run_world_code` 调用）
- `run_world_trigger(world, event, entry, ...)` —— 世界 `main.py:handle(event)`（事件钩子调用）
- `run_agent_code(agent_id, code|entry, ctx, deny_net, deny_fork)` —— AI 脚本
- 工具 `run_script(code, path?)` —— AI 面向入口：`path` 给定则先落盘到文件空间再执行（攒自己的脚本库）
- 决策技能 `do.run_script` —— 经 `run_agent_code` 执行，事件上下文走 `DECISION_CTX`
- 例外：`world/skill_sandbox.py` 走 stdin/stdout JSON 行协议（世界 skill 的 ctx 能力转发），
  自己起进程但复用同一套隔离库与 rlimit

## 8. 新增一个 owner（例如"世界外的某类实体"）

1. 定目录：在 `config` 里给出唯一来源，别在业务代码里拼字面量。
2. 写适配层：`Policy` + `base_env()` 补自己的变量 → 调 `runner.run_code`。
3. 加档位选择与一条用例：越界读被拒、网络按档位、超时能收回。

## 9. 实测（容器内，2026-09-26）

| 场景 | 结果 |
|------|------|
| AI 脚本写文件 | `cwd=/app/data/agents/1`，文件落在该目录 |
| 读 `/etc/hostname` | `PermissionError`（Landlock） |
| `socket.socket()` | `PermissionError`（seccomp 禁网络） |
| `while True: pass` | 5s 收 SIGXCPU，reason 给出配额说明 |
| `time.sleep(30)`（墙钟 1s） | `timed_out=true`，进程组被杀 |
| 世界代码读 `/etc/hostname` | 拒绝（重构前后一致） |
| 世界 `handle(event)` | 结果原样返回，缺入口时 reason 保留"入口文件不存在" |
