# 世界能力注入：群 AI / 世界 AI 的能力边界与路径

> 补充 world_skill_design.md 的角色边界与注入机制。

## 一、角色与能力作用域（造物主 ≠ 居民）

| 角色 | 身份 | 用的能力 | 位置 |
|---|---|---|---|
| 世界 AI（群视界机器人） | **造物主**，在世界之外设计世界 | 平台内置工具 + **设计侧 skills** | `data/world_ai_skills/`（全局库） |
| 群 AI（居民） | 世界内活动者 | 默认平台工具 + **绑定世界的世界侧 skills** | `data/worlds/{id}/skills/`（世界颁布） |

**铁律**：世界 AI **不注入世界侧 skills**（那是居民的能力，造物主不该拿）；
群 AI 不碰设计侧 skills（造物主工具不暴露给居民）。
（修正：早期实现把两侧合并给了世界 AI，已按本表拆分。）

## 二、两种能力路径（各有优点，并存）

### 路径 A：世界 skills 工具化（推荐）

世界侧 skills（manifest + code.py）→ function calling 工具定义 → AI 直接调用 → skill 的 `run(args, ctx)` 执行 → 返回结构化结果。

**优点**：
- **结构化可靠**：AI 按参数 schema 调用，不靠"碰巧说出正确语法"；返回 JSON 结果，AI 能读能继续推理
- **能力最小化**：manifest.permissions 声明什么 ctx 有什么（文件/数据/群消息），越权调用直接报错
- **可组合**：一个 skill 的输出可喂给下一步（工具循环内多轮组合）
- **版本化懒加载天然适配**：skill 定义 = 工具定义，走 capability_versioning（known/effective）

**适合**：新写的能力、需要参数化/返回结果的逻辑（查状态、算数据、生成内容）。

### 路径 B：world_command 文本命令（兼容）

AI 调 `world_command("旅人移动到 2,3")` → 以 AI 身份发群消息 → 群消息钩子 → 世界程序 `main.py handle()` 解析 → 程序逻辑执行 → publish SSE 生效。

**优点**：
- **零改动复用世界程序**：存量世界（如 2d-adventure）的命令语法已写在世界程序里，群 AI 直接走同一套，不重复实现
- **群内可见可审计**：命令是群消息，所有人看到 AI 干了什么；世界程序还能在群里回应
- **世界程序逻辑统一**：用户和 AI 用同一解析器，行为一致（"我去 2,3" 用户能发、AI 也能发）
- **适合常驻世界的玩法命令**：NPC 移动、公告、身份签到等已有语法体系

**适合**：世界程序已定义命令语法的存量能力、需要世界程序整体逻辑（含状态机/推演）的命令。

### 选择原则

| 场景 | 用哪个 |
|---|---|
| 新能力、参数化、要结果 | **A 工具化** |
| 存量世界已写好的命令语法 | **B world_command** |
| 常驻世界玩法命令（SSE 驱动） | **B**（世界程序就是执行器） |
| 需要 AI 拿到结构化结果继续推理 | **A** |

## 三、AI 绑定世界机制

现有 `world_bindings`（entity_type: group / dm / user）只有**群绑定**——群 AI 通过所在群间接关联。
新增 **agent 绑定**：`entity_type='agent' + entity_id=agent.id`，AI 直接绑定世界（工具集按 AI 算，跨群一致）。

两种绑定并存：
- **群绑定**（推荐多人玩法）：群 AI 在群里 → 用该群绑定世界的能力
- **agent 绑定**（个人专属）：AI 直接绑一个世界，所有对话都有该世界能力

## 四、工具集构建（版本化懒加载）

```
世界 AI 工具集 = 平台 WORLD_TOOLS + 设计侧 skills（list_ai_skills）
群 AI 工具集   = 默认平台工具（get_allowed_tools）+ 绑定世界的世界侧 skills（effective 版本快照）
```

- 每次响应重新计算（读快照），内容稳定 → 前缀缓存命中
- 变更（绑定/skill 增删/平台发布）→ capability_versioning：known 增量告知 + compact 后 effective 切新
- 未绑定世界的 AI：无世界能力工具；绑定后按版本化流程注入

**Skill 分层注入**：居民工具集再按**绑定类型**过滤——
skill manifest 可声明 `types`（适用类型列表，省略或 `["*"]` = 所有类型通用），
注入时只给匹配类型的居民发对应 skill（详见 world_skill_design.md 三·六）。

```
群 AI 工具集 = 默认平台工具 + 世界侧 skills（effective 快照）∩ 该 AI 绑定类型的 skills
```

- 通过群聊进入世界的实体 → 默认落到该群绑定的 AI 类型；AI 直接绑定世界 → 落到自己绑定的类型

## 五、实现清单

1. ✅ world_command 工具（路径 B，已落地）
2. ✅ 世界 AI 工具集修正：只用设计侧 skills（world_chat_service 拆分 build_skill_tools）
3. ✅ AI 直接绑定世界：world_bindings 支持 entity_type='agent' + 绑定/查询 API
4. ✅ 群 AI 工具集注入：response_worker 群聊路径 = 默认 + 绑定世界世界侧 skills（effective 快照）
5. ✅ 群 AI 世界源变更通知（build_messages 注入 capability_versioning world 源）
6. ✅ 沙箱加固（2026-08-07）：skill 执行 subprocess + Landlock/seccomp + 协议转发

## 六、AI 认知注入（2026-08-07 补充）

机制实现 ≠ AI 知道。两处 system prompt 负责把能力边界讲清楚，
避免 AI 凭猜测回答（如世界 AI 误说"群 AI 没有工具"）：

- **世界 AI（造物主）**：【能力边界】段（world_chat_service 静态前缀）——
  自己是造物主（平台工具+设计侧 skills）；群 AI 居民绑定世界后拥有世界侧 skills（工具化）
  + world_command；技能由造物主在 worlds/{id}/skills/ 颁布；
  用户直接发命令与世界程序交互是另一条并行路径
- **群 AI（居民）**：【本群世界】段（llm.py 动态尾部）——
  列出本群绑定世界 + 已获得的世界技能工具名（明确"像调普通工具一样 function calling 直接调用"）
  + world_command 语法提示；能力版本化懒加载（known/effective）

## 七、同名 skill 冲突策略（2026-08-07，方案 1+3）

多群/多世界绑定下可能出现同名 skill（如两个世界都颁布 `travel`）：

- **定义层去重**（response_worker）：同名只注入一个工具定义，
  当前群绑定世界优先（agent 直接绑定世界的同名版本不重复注入）
- **world_id 显式指定**（方案 3）：工具定义自动追加可选 `world_id` 参数（build_world_tools 生成，
  skill 作者无感）；AI 可传 world_id 指定其他世界的同名版本
- **执行路由**（ToolRegistry.dispatch 兜底）：world_id 存在 → 校验目标世界 ∈ 已绑定世界
  （agent 绑定 + 群绑定，防越权）→ 执行指定世界版本；缺省 → 群绑定世界优先遍历
- **清单告知**（llm.py【本群世界】）：同名技能列出所有颁布世界 id，提示可用 world_id 指定
- 测试（真实 DB）：无 world_id → 群绑定版本；world_id=22 → 指定世界版本；
  world_id=999/abc → INVALID_ARGS 拒绝
