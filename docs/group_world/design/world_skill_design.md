# 文件式 skill/tool 机制（world skill runtime）

> 2026-08-06 落地。方向：世界能力走"skill/tools 放文件夹让实例识别"，
> **不平台硬编码**。world_command 将是该机制的第一个真实 skill。

## 一、两个作用域（造物主 ≠ 居民）

世界设计 AI（群视界机器人）是**设计世界的造物主**，在世界**之外**；
世界内的居民/管理者/物品是世界的**内容**。两者能力分开放：

| 作用域 | 位置 | 谁可用 | 例子 |
|---|---|---|---|
| 设计侧（造物主工具） | `data/world_ai_skills/<name>/` | 所有世界的设计 AI（全局共享库） | `world_stats` |
| 世界侧（居民能力） | `data/worlds/{id}/skills/<name>/` | 该世界的 AI / 居民 / 群 AI | `world_command`（下一步） |

同一套运行时，区别只在 ctx 能力集。

## 二、skill 目录约定

每个 skill 一个目录，含两个文件：

```
data/world_ai_skills/world_stats/
├── manifest.json    # 声明：名字/描述/参数 schema/所需能力清单
└── code.py          # 逻辑：async def run(args, ctx) -> dict
```

### manifest.json

```json
{
  "name": "world_stats",
  "description": "统计世界文件夹的文件数量与总大小。",
  "arguments": { "type": "object", "properties": {} },
  "permissions": ["file:list"]
}
```

### code.py

```python
async def run(args: dict, ctx) -> dict:
    files = ctx.file.list()          # 只能通过 ctx 做事
    return {"success": True, "file_count": len(files)}
```

## 三、ctx 能力注入（capability-based）

ctx 只包含 `manifest.permissions` **声明过**的能力——没声明的根本不注入
（代码里 `ctx.file` 会直接报错，不存在"越权调用"）。

| 权限 | ctx 能力 | 说明 |
|---|---|---|
| `file:list` / `file:read` / `file:write` / `file:delete` | `ctx.file.*` | 只限本世界文件夹（隔离目录+扩展名白名单，复用 world_file_service） |
| `data:read` / `data:write` | `ctx.data.*` | **世界数据（world_data 表）**：`await ctx.data.get(key)` / `set(key, value)` / `delete(key)`——结构化/操作数据只经 API/skill 读写 |
| `world:read` / `world:update` | `ctx.world.*` | 世界信息读取/更新（以世界主人身份） |
| `group:send` | `ctx.group.send()` | 发消息到绑定群（以世界创建者身份） |
| （无） | `ctx.log()` | 平台日志（无需声明） |

## 三·六、Skill 分层注入（按 AI 类型分发）

> 设计口径："我们后端要考虑给Skill做分层注入的。例如只通过群聊进入世界的默认到一个AI类型过去，然后给不同类型的AI发不同的skill。所以设计Skill的时候就可选这是给所有类型通用的skill还是只有哪些类型可用的skill。"

**背景**：世界有群类型体系（group_types.json，群聊和 AI 都按类型入场），skill 也应**按类型分层注入**——不是所有居民拿到同一套技能。

### 分层规则

1. **入口默认类型**：通过群聊进入世界的实体 → 默认落到该群绑定的 AI 类型；AI 直接绑定世界 → 落到自己绑定的类型
2. **按类型发 skill**：每个类型收到的 skill = 声明了"本类型可用"的 skill + 通用 skill
3. **skill 适用性声明**（manifest 新增字段）：

```json
{
  "name": "forge_weapon",
  "description": "打造武器（仅铁匠可用）",
  "arguments": { "type": "object", "properties": {} },
  "permissions": ["data:read", "data:write"],
  "types": ["blacksmith"]        // 仅 blacksmith 类型可用
}
```

| types 字段 | 含义 |
|-----------|------|
| `["*"]` 或省略 | **所有类型通用** |
| `["blacksmith", "merchant"]` | 仅这些类型可用 |

### Skill 设计准则（给 AI 的 skill 必须考虑）

> 设计口径："请考虑 skill 给到 AI 后 AI 的可操作性和简便性，和一次 skill 能实现效果的最大化性"

设计 skill 时站在**使用方（AI 居民）**角度想，而不是实现方角度：

1. **可操作性**：AI 拿到这个 skill 后真的会用、会调——参数清晰、返回值明确、失败有可读报错；不要让 AI 猜参数含义
2. **简便性**：一次调用能完成的事不要拆成多次——AI 调工具是有成本的（多轮往返、token、出错概率），能一步到位就别让 AI 绕弯
3. **效果最大化**：skill 的实现尽量把"一个完整意图"做进一次调用（如 打造武器=锻造+装备+消耗材料一次完成），而不是让 AI 分三次调三个细碎工具
4. **反面例子**（要避免）：`add_item` / `remove_item` / `calc_status` 三个分开的 skill——AI 想"给玩家一把剑"要调三次；应该一个 `give_weapon`（校验+发放+属性变化一步完成）

> 判断标准：**AI 用最少次调用完成用户意图 = 好 skill**。发布 skill 前模拟一遍 AI 调用路径，觉得"要调好几步才能办成事"就合并。

### 注入链路

```
skill manifest.types 声明
        ↓
群/AI 绑定类型（group_types.json slug）
        ↓
匹配：types 含 * 或 含该类型 slug → 注入该居民工具集
```

- 与能力懒加载（capability_versioning）叠加：类型变更/绑定变更 → 增量 changelog 告知，compact 后 effective 切换
- 世界 AI 设计世界时：为关键角色/职位创建类型 + 配套 skill（如 铁匠类型 + forge_weapon）

## 三·五、代码/数据分离（世界发布打包）

世界文件夹约定：

```
data/worlds/{id}/
├── index.html / skills/ / main.py ...   ← 代码区（发布世界时打包）
└── content/                             ← 内容产物区（静态文字类，自由层级）
```

- **content/ 不属于代码**：世界创作者在自己世界的 content/ 里自由建层级（设定文本/文档/素材文字）
- **发布世界不打包 content/**；下载数据**可选是否包含**（export `?include_content=false` 只打包代码，默认 true 包含）
- **结构化数据不进 content/**：一律走 world_data 表（API/skill ctx.data 读写）
- 现有世界（星野镇）暂不迁移，新约定对新世界生效

## 四、安全模型（v2 子进程沙箱，2026-08-07 加固）

信任边界：**世界创作者（或其 AI）编写的代码**——防"AI 生成代码越权乱来"
（含内省逃逸），对抗性恶意代码的终极隔离（容器）仍后置。

- **子进程执行**：`python -I -u`（不吃 site/不继承 PYTHON* 环境）+ 独立进程组
  （start_new_session）+ rlimit（内存 64MB / CPU 10s / 文件 4MB / NPROC 16）+ 超时 killpg 强杀
- **Landlock**：文件系统锁死在世界目录（读写）+ skill 目录（只读）+ 标准库目录（只读），
  其余路径（/etc、后端代码、其他世界数据）一律 EACCES；绕过 builtins 的 `open` 逃逸也读不到世界外
- **seccomp-BPF**（x86_64）：禁 execve/网络/挂载/ptrace/内核接口等危险 syscall；
  skill 沙箱连 fork/clone 一起禁（纯计算+协议，无线程需求）
- **ctx 协议转发**：一切 IO（file/world/data/group）序列化回宿主校验权限后执行，
  子进程零宿主引用——内省逃逸拿不到任何宿主对象
- **import 白名单 + builtins 收紧**：纵深防御（即使逃逸，Landlock/seccomp 兜底）
- **超时强杀**：单次执行 30s，killpg 连子进程/孙进程一起杀

⚠️ 已知边界：Landlock 按路径授权（同路径前缀内的文件均可访问）；进程内逃逸
理论仍可读标准库源码（公开代码，无敏感信息）；终极隔离（容器）后置。

## 五、接入点

- `world_chat_service.py`：`tools_for_world = [*WORLD_TOOLS, *build_skill_tools(world_id)]`
  → LLM function calling 定义动态合并（首轮 + 工具循环都带）
- `app/tools/world/__init__.py` `run_world_tool`：未知平台工具 → `execute_skill()` 分发（找不到返回 None 走兜底）

## 六、路线图

1. ✅ 运行时骨架 + 两个作用域 + 安全加载 + ctx 注入 + 示例 `world_stats`
2. ✅ 用户移动命令（`我去 2,3` → 世界程序解析 → 玩家传送）——语法已定
3. ✅ `world_command`：世界侧能力入口——群 AI 发命令操作世界（与用户共用语法）
4. ✅ 群 AI 上下文注入：「本群绑定世界 X，可用世界颁布的能力行动」（能力懒加载版本化）
5. ✅ 沙箱加固（2026-08-07：subprocess + Landlock + seccomp + 协议转发，
   文件系统锁死世界目录、禁进程/网络/危险调用；对抗性逃逸验证通过）
   - 终极隔离（容器化）后置
6. ✅ Skill 分层注入（2026-08-12）：manifest `types` 字段（省略/["*"] = 通用，列表 = 仅这些类型可用）；
   `build_world_tools_for_type(world_id, type_slug)` 按绑定类型过滤；
   llm.py 群 AI 能力清单按 agent/群绑定类型取并集注入（详见三·六）
