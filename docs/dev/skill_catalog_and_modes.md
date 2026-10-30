# 技能目录、工具装载与三种模式

> **设计口径（2026-10-25，用户原话）**
> 「先列出技能条目，然后放在一起详细说明要点（跟状态（在群聊、私信、世界）有关）」；
> 「技能目录要写上怎么调用技能，以及能干什么的基础信息」；
> 「要点是要按技能自己拼的，每个技能都要提供」；
> 「每个状态（聊天、编程、世界）默认需要打开的 tools 数组都不一样；AI 自己新建状态也可以开那些让它默认打开」；
> 「平台侧从现在开始统计每一个 tools 的调用频率、次数，还要分状态统计」；
> 「增加 PTC 模式……策略模式改名为策略代理模式」（正文按最终命名：**决策代理**）。

本文是这批改动的单一事实源。实现与本文冲突时以本文为准，改口径先改本文。

---

## 一、现状与问题

| # | 现状 | 证据 | 后果 |
|---|---|---|---|
| 1 | 技能目录只列工具名，不写用法与调用时机 | ai/llm.py:768-830 | 模型知道「有 file_read」，不知道「该先 grep 再分页读」 |
| 2 | tools 全量下发 73 个 / 47,820 B | 容器实测 | 每轮固定成本，冷工具白占 |
| 3 | 改/删 tools = 整段前缀 miss（含全部 messages） | world_chat_service.py:1829-1830：同一份 messages，带 tools 命中 76416/76584，去掉 tools 只剩 3328/52569 | 工具集一变，缓存全废 |
| 4 | 允许名单每轮现算 get_allowed_tools(agent.state, thinking, delay_reply) | response_worker.py:1025-1031 | 状态一变 tools 就变 → 整段 miss，且没有任何告知 |
| 5 | 快照只冻「定义内容」，不冻「给不给」 | capability_versioning.py:549-563 | 锁定态仍会漂 |
| 6 | dispatch 不校验 plugin.states | base.py:293-378 | 今天只是靠「请求里没有」兜住，一旦锁名单就漏 |
| 7 | 状态（active/dnd/inactive）不在环境层 | plugin_env.py:25-32 只有 channel / origin_name / member_num / full_mode / origin_memo | 状态变化无告知通道；模型看到英文 active |
| 8 | 工具调用统计只在进程内内存 | metrics_collector.py:127-134 | 重启即失，一个月后拿不到一手数据 |
| 9 | 决策技能（现「决策代理」）启用无需验证 | tools/decision.py | 写了永不触发的规则也会落库，AI 以为它在替自己看着 |
| 10 | 多步只能多轮 function calling | — | 编排成本高，无法循环/条件 |

## 二、目标与非目标

**目标**

1. 模型知道「有什么、怎么用、什么时候用」——目录 + 要点承担这一层。
2. 凡是进前缀的内容，锁定态字节不动；解锁点只有 compact / clear / 帧切换。
3. 按状态装载：不同状态帧默认打开的能力不同，AI 可自建状态并声明。
4. 从上线第一天起就有分状态的工具调用数据，一个月后用数据决定「常驻哪些、折叠哪些」。
5. 能力可编排（PTC），同时保留一次一调。
6. 决策代理落地必须过「虚假触发」检验。

**非目标（本期不做）**

- 不追求把 tools 压到几百字节；先测量再收敛。
- 不改世界侧 skill 的目录契约（manifest.json + code.py 布局不变）。
- 不改 capability_versions 的表结构，锁只用 holder 里的 map。

---

## 三、请求体的三层

折叠后的请求体由三层构成，各自有唯一职责，互不重复：

| 层 | 位置 | 装什么 | 变化频率 |
|---|---|---|---|
| **环境** | message 0 锁定段（含环境段 + 通道规矩） | 你在哪：群/私信/世界、通道、在线状态 | 极低（变化落通知） |
| **技能目录 + 要点** | 锁定段（静态段之一） | 有什么、怎么调、要注意什么 | 低（能力变动落通知） |
| **工具契约** | payload.tools | 段内工具的 JSON Schema | 只在解锁点变 |

### 3.1 技能目录

格式（每段一段头 + 每个工具一行）：

    ## 技能目录
    群聊社交（27）— 在群聊和私信中发言、切换在线状态、管理免打扰
      send_message    在群里以你的名义发言
      send_dm         私信某人（对方收到的是私信，不是群消息）
      file_read       读文件。先 file_grep 定位行号，再用 offset/limit 分页读，别整文件全读

- **一个工具一行**，行内 = 名字 + 一句「干什么 + 调用要领」。
- 不写参数 schema（那是 tools 的事）。但要写**最小调用形态**：file_read(path, start_line?, end_line?)，
  这样模型能在未展开时直接拼对参数（见 7.2）。
- 数据来源：ToolPlugin.description（干什么）+ ToolPlugin.usage_notes 的第一条（怎么调）。
  目录文字由插件自带，不在 llm.py 里硬拼。

### 3.2 要点（usage notes）

**层级 100 一档，值小者优先**（预算超了从尾部砍，砍的必须是最不重要的）：

| 层级 | 名字 | 放什么 | 谁写 | 例子 |
|---|---|---|---|---|
| 100 | 铁律 | 违反就出事的硬约束，与场景无关 | 平台（段级） | 自动策略不可把所有情况截走（勿扰时段除外）；@/私信/群公告永远穿透 |
| 200 | 场景 | 只在某场景有意义的行为纪律 | 平台（段级） | 被唤醒后先判断要不要回；不回就免打扰/屏蔽；世界帧里只有基础工具 + 世界技能 |
| 300 | 用法 | 这个工具怎么用才对（前置、顺序、陷阱） | 工具作者 | file_read 先 grep 再分页；delay_reply 在积压时会跳过 |
| 400 | 提示 | 优化项，可省 | 工具作者 | 别连续发三条；@all 前先想清楚 |

存储：

    class ToolPlugin:
        usage_notes: list[tuple[int, str]] = []      # [(层级, 正文)]
        usage_scenes: frozenset[str] = frozenset()   # 空 = 所有场景；否则 {"group_chat","dm","world"}

    SKILL_SEGMENT_META["chat_social"]["usage_notes"] = [(100, "…"), (200, "…")]

场景过滤只影响**渲染**（不该出现在私信里的群聊纪律就不渲染），不影响权限。

### 3.3 渲染排序（必须字节级确定）

    sort key = (层级, 段序, 工具名字典序, 条目序号)

- **段序**：把 SKILL_SEGMENT_META 固化成有序常量表（当前：chat_social → file_operations →
  memory → group_management → self_config → self_management），不复用 dict 遍历顺序。
- **工具名**：字典序，不用注册顺序（插件加载顺序会随目录扫描变化）。
- 排序不稳定会让提示词字节每轮变 → 整段 miss，这条是硬约束不是偏好。

### 3.4 去重

| 情况 | 处理 |
|---|---|
| 同一工具在多段出现（web_fetch 同时在 chat_social 与 file_operations） | 只在主段（ToolPlugin.segment）渲染 |
| 两个工具写了同一句话 | 保留层级高的；同层级取段序靠前的；相同文本只留一条 |
| 插件想覆盖内置工具的要点 | 只允许在同层级内覆盖；不能用一个 400 覆盖内置的 100 |

### 3.5 预算与截断

目录与要点各给上限（初值：目录 1200 字、要点 1500 字，可调）。超限时按
(层级降序, 段序逆序) 砍，末尾必须写明：

    （另有 7 条要点未列出：file_edit、web_search、… 需要时调 open_skill_segment）

宁可说清截断，不静默给半截——沿用 capability_versioning 的既有口径。

### 3.6 渲染示例

    ## 技能目录
    群聊社交（27）— …
      send_message    在群里以你的名义发言
      …
    文件操作（12）— 读写文件、管理自己的工作文件夹
      file_read       读文件。先 grep 定位行号，再分页读
      …

    ## 要点
    ·（铁律）自制策略等自动功能，制定时原则上不可让所有事物都无法唤醒自己（勿扰时段这类自带时限的除外）
    ·（铁律）@你、私信、群公告永远穿透任何自动策略
    ·（场景）被消息唤醒后先判断是否需要回复；不回复就定免打扰、屏蔽，或写一条自制策略（决策代理规则）
    ·（用法）先 file_grep 定位行号，再 offset/limit 分页读，别整文件全读

场景标签只到层级（铁律/场景/用法/提示），不写具体场景名——场景已由渲染时的过滤决定，
写进正文只是让同一句话在两个场景里字节不同，白丢命中。

---

## 四、按状态装载工具

### 4.1 「状态」的口径

这里的「状态」= **会话帧的类型**（frame.type），不是 agent.state。
push_state 的枚举已经是这套：group_chat / dm / world / file_work / alarm / project / write / other
（tools/self_management/push_state.py:26-29）。

帧上已经有两个能力字段：`tools`（白名单）、`skills`（`utils/pure/state_stack.py` 的 `make_state_frame`
里默认都是 None）。这里**不写行号**：加一行 docstring 就会把它写歪（2026-10-27 就这么歪过一次）。

### 4.2 三个概念，别混

| 概念 | 字段 | 语义 | 变了会怎样 |
|---|---|---|---|
| 白名单 | frame.tools | 硬闸：只有这些能用 | 换帧 = 换上下文，允许 miss |
| 默认展开 | frame.open_segments（新增） | 软默认：这些段默认进 tools | 同上 |
| 在线状态 | agents.state | active/dnd/inactive，影响**执行**不改编排 | **不**改 tools（见 5.2） |

### 4.3 默认展开表（初版，等统计校准）

| 帧类型 | 默认展开的段 | 体量 |
|---|---|---|
| group_chat | chat_social + memory | ≈ 19.8 KB |
| dm | chat_social + memory | ≈ 19.8 KB |
| world | 基础集 + 该世界颁布的技能（沿用 frame_tool_gate） | 视世界而定 |
| file_work / write | file_operations + self_management + memory | ≈ 27.4 KB |
| project | file_operations + self_management | ≈ 21.6 KB |
| alarm | self_management + memory | ≈ 19.8 KB |
| other | chat_social + self_management | ≈ 28.0 KB |

段体量实测：chat_social 14,011 B / self_management 14,013 B / file_operations 7,574 B /
memory 5,832 B / self_config 5,604 B / group_management 786 B（合计 47,820 B）。

> 这张表**只影响默认值**，不限制能力：没展开的段随时可 open_skill_segment 打开，
> 也可以直接 call_tool（见 7.2）。统计满一个月后按真实分布重排这张表。

### 4.4 AI 自建状态时声明

push_state 增加两个可选参数：

    {"open_segments": ["file_operations", "memory"], "why": "写一个脚本"}

- 服务端校验：段名必须存在于 SKILL_SEGMENT_META，上限 6 段，越界回 INVALID_ARGS 并列出合法值
- 落库到 frame.open_segments，pop_state 回来后回到上一帧的默认集
- **不越权**：open_segments 只能展开「该 AI 本来就允许」的工具；frame.tools 白名单仍然优先

### 4.5 解锁点

tools 数组只在三处重算：**compact、clear、换帧**。换帧是一次真正的上下文变化（帧摘要、便签、
交接尾巴都跟着变），这一次 miss 躲不掉也不值得躲。

---

## 五、锁与执行闸

### 5.1 名单冻结

现状：快照（capability_versions）只冻定义内容，名单每轮现算。改为：

- agents 新增列 `cap_effective_allowed`（JSONB）：
  {"{会话键}|allowed": ["file_read", …]}；读/写/清三个入口统一走
  `capability_versioning.frozen_allowed / freeze_allowed / clear_frozen_allowed`
- 存**排序后的列表**：顺序本身就是字节，不能依赖 set 遍历
- 锁定态请求的 tools = **快照 ∩ 冻结名单**；名单只在解锁点重算
- 首次见（没有记录）按当前允许集建档，不算变更

### 5.2 状态从可见性闸降为执行闸

名单冻结后，dnd 时请求里仍有 send_message，所以 dispatch 必须补一道校验：

    if agent.state not in plugin.states:
        return _build_tool_error(ToolErrorCode.INVALID_ARGS,
                                f"你当前是「{state_label}」，这个工具现在不能用")

这条同时解决了 base.py:293-378 今天的隐性依赖（「请求里没有」当权限）。

### 5.3 状态进「尾部读数」，不进环境段（实现时改的口径）

环境段是**会话级事实**（在哪个群、接没接渠道），锁在 message 0 里等解锁；状态却是**每轮读数**，
变化频繁（AI 自己 switch_state、自动 DND）。冻进前缀的代价是 AI 一直按旧状态行事，只能靠补通知，
而每次切状态都写一条账本条目又贵又慢。

所以状态与「## 当前时间」同一条尾部消息，每轮现算：

    你的状态：勿扰（只能被动响应：@你、私信、群公告能穿透，其余消息不主动开口）

- 每轮都准，**不需要**通知
- 与前缀无关（那条消息本来每轮在变）
- 文案统一走 `utils/pure/agent_state.py`：提示词与执行闸拒绝话术共用一份，不各写一套

### 5.5 实现落点（2026-10-25）

- 冻结名单：`capability_versioning.py` 三个函数 + `mark_prefix_fresh` 里清空（解锁点重算）
- 取用：`response_worker.py` 群聊与私信两条路径各一行 `frozen_allowed / freeze_allowed`
- 执行闸：`ToolRegistry.dispatch` 执行前判 `plugin.states`，回可读错误（附「要改状态用 switch_state」）
- 状态文案：`utils/pure/agent_state.py`（`state_line` / `state_denied_text`）

### 5.4 不变式

1. 锁定态下 tools 数组字节不变（除非 compact/clear/换帧）
2. 名单与定义各自冻结，二者都只在解锁点换
3. 任何「能力变了」必须同时落一条可见通知——哪怕只是工具下线
4. 状态变化不改 tools，只改环境段与执行闸

---

## 六、统计（从第一天起落库）

现状：metrics.record_tool_call 是进程内 Counter（metrics_collector.py:127-134），重启即失，
且没有 agent/状态维度。

新增表 tool_usage_daily（形态对齐 usage_daily：日聚合、UPSERT、只增不删）：

| 列 | 说明 |
|---|---|
| stat_date | 统计日期（UTC） |
| tool_name | 工具名 |
| frame_type | frame.type（group_chat / file_work / world / …）；无帧记 "" |
| agent_state | active / dnd / inactive |
| calls / errors | 次数 |
| latency_ms_sum | 耗时累加（平均值 = sum / calls） |

唯一键 (stat_date, tool_name, frame_type, agent_state)，命中就累加。
记录点：ToolRegistry.dispatch **唯一入口**（成功与失败都记；世界侧 skill 兜底也走这里）。
体积量级：73 工具 × 8 帧类型 × 3 状态 × 天数——一年也就百万行级，可接受。

用途：
- 一个月后定「常驻段 / 折叠段」——用真实分布替掉 4.3 的初版表
- 发现冷工具（可下线）、误用（某工具错误率高）
- 画像「这个 AI 是什么型」——聊天型 vs 干活型，进而决定默认展开

---

## 七、工具装载的两种形态

### 7.1 元工具集（恒定）

无论展开哪几段，tools 里恒定存在的：

| 元工具 | 作用 |
|---|---|
| open_skill_segment(segment) | 展开一段的工具 schema（写账本，见 7.3） |
| open_tool(name) | 展开单个工具的 schema |
| call_tool(name, args) | 直接调用；参数不对才回 schema |
| run_program(code) | PTC：写程序编排多步（见第八节） |

### 7.2 不展开也能调（消除「多走一步」）

call_tool 由服务端按真实 schema 校验：

- **合法** → 直接执行，零额外轮
- **不合法** → 返回 {"error": "参数不对", "schema": {…}, "hint": "…"}，这一轮才把 schema 塞进来

于是「展开」从**必经步骤**降级为**报错兜底**。多一步只在两种情况下发生：模型拼错参数，
或它主动想先看清楚。

### 7.3 展开落在哪

展开结果（schema 文本）进**账本条目**（drop_on_unlock，活到解锁）：

- 为什么不用尾部块：展开是「这个会话里我学会用这个工具了」——属于会话事实，不是当轮读数；
  账本一次写入后每轮按命中价计费，比每轮放尾部便宜一个数量级
- 代价诚实写在这里：它占上下文直到 compact，一条展开（如 file_operations ~7.6 KB）会一直
  跟着这个会话；所以默认只展开「当前状态需要的」，不预展开

能力变更通知则相反——属于短命事件，放**尾部块**（3 轮后自然消失），不放账本。

---

## 八、PTC（run_program）

### 8.1 形态

AI 写一段程序，程序里可以调工具，按顺序/循环/条件编排：

    # 示例：把三个文件里的待办合并成一条周报发群里
    items = []
    for f in ["todo/a.md", "todo/b.md", "todo/c.md"]:
        r = await tools.file_read(path=f)
        items += [l for l in r["content"].splitlines() if l.startswith("- ")]
    await tools.send_message(content="本周待办：\n" + "\n".join(items[:40]))
    return {"count": len(items)}

### 8.2 执行模型（复用现有沙箱）

不新造安全模型：沿用世界侧 skill 的「子进程沙箱 + 协议回桥」
（services/world/skill_sandbox.py + skill_runner，见 docs/group_world/design/world_skill_design.md 四节）。

- 脚本跑在 python -I -u 子进程，Landlock 只给文件空间、seccomp 禁网络与 fork
- await tools.xxx(...) 序列化回宿主 → 走 ToolRegistry.dispatch → 权限、状态、帧白名单照常校验
- 宿主侧同一 AI 串行（与现有 agent_sandbox 口径一致），防并发写冲突

### 8.3 权限

默认：**全部已展开工具可调**，但 dispatch 的既有闸（状态、绑定世界、帧白名单）照样生效。
脚本不额外获得任何权限——它只是一次批量调用，不是提权通道。

### 8.4 与一次一调并存

保留 function calling 直接调工具；PTC 是「一个工具」而不是替代品。用得少就零成本。

---

## 九、决策代理（原「决策技能」）

### 9.1 改名

| 位置 | 旧 | 新 |
|---|---|---|
| 工具描述 / 文档 / 前端 | 决策技能、决策引擎 | 决策代理 |
| 工具名 | list/write/delete/test_decision_skill | **不改**（兼容既有调用与数据） |
| 存储 | agent_skills.skill_type='decision' | **不改** |

> 顺带澄清一个历史命名：设置页那个「策略模式」是 auto_approve_vector 的旧标题，
> 与本次无关，不要混。

### 9.2 启用前强制检验

write_decision_skill 增加必填的 verify ——「我认为这条代理策略能触发我自己的检验样例」：

    {
      "name": "签到代办",
      "when": {"event": "group_message", "conditions": {"content_clean": "签到"}},
      "do": {"action": "reply_template", "reply": "已记录"},
      "verify": {
        "event": "group_message",
        "conditions": {"content_clean": "签到", "sender_id": 12},
        "expect": "hit"
      }
    }

落库流程：

1. 先用 verify 跑一次引擎（DRY_RUN=1，不产生任何外部效果）
2. 命中 → 写库，返回 {"saved": true, "checked": [...]}
3. 不命中 → **不写库**，返回 {"error": true, "reason": "这条规则在当前定义下不会触发", "checked": [{name, hit, reason, why}]}

复用现有 test_decision_skill 的引擎与返回结构（tools/decision.py、decision_skill.test_rule_desc()），
只是把「可选试跑」变成「启用必经」。

### 9.3 时序叠加（独立阶段）

用户要的能力：某群成员发消息后 300 秒又有人发消息 —— 这是**有记忆的条件**，现在的条件是单事件的。

需要：
- 规则可声明 after：{"event": "group_message", "after": {"same_rule_since": 300}}
- 帧/规则上存最近一次命中的事件与时间戳
- 由 trigger_sweep 周期检查窗口（而不是只在消息到达时判）

这块单独一阶段落地，不和 9.2 混在一起改。

---

## 十、实施顺序

| 步 | 内容 | 风险 | 收益 |
|---|---|---|---|
| **1** | 技能目录 + 要点（含层级/排序/去重/预算）+ tool_usage_daily 统计 | 低（不动 tools，零额外轮次） | 模型会用、会用对；数据开始积累 |
| **2** | 状态进环境段 + 名单冻结 + dispatch 执行闸 | 中 | 状态变化不再断缓存，且模型知道自己在什么状态 |
| **3** | 按帧默认展开（open_segments）+ 元工具集 + call_tool 直调 | 高（动 tools） | 冷段不再每轮付费 |
| **4** | PTC（run_program）+ 决策代理改名与 verify 门禁 | 中 | 多步编排；代理不可用时立刻知道 |

第 3 步上线后满一个月，用 tool_usage_daily 的真实分布回填 4.3 的默认表，再决定是否继续压缩。

---

## 十一、验收

**单测 / 集成测**

- 目录渲染：字节级确定（同一状态两次渲染完全相同）；超预算截断有「另有 N 条」字样
- 去重：跨段同名工具只出现一次；同层级取段序靠前
- 锁：锁定态下改状态/改 thinking/改 delay_reply，tools 数组**不**变
- 执行闸：dnd 时 call_tool("send_message") 被拒且错误可读
- 环境：状态变化落一条【环境变化】账本条目（不重复投递）
- 统计：一次成功一次失败后，tool_usage_daily 的两个计数各 +1，且帧类型/状态正确
- 决策代理：verify 不通过 → 库里查不到这条规则；通过 → 查得到

**线上指标**

- tools 体量（字节）与命中率（cache_stats 既有口径）每周对比
- 每帧类型实际展开的段数与个数
- 冷工具清单（30 天 0 调用）

---

## 十二、风险与回退

| 风险 | 缓解 |
|---|---|
| 目录过长反而占位 | 有预算与截断；先按 1200 字上线观察 |
| 折叠后模型拼错参数 | call_tool 直调 + 错误回带 schema；目录带最小调用形态 |
| 换帧频繁导致反复 miss | 帧切换本来就重算上下文；若统计显示频繁，再考虑「帧内固定」 |
| 统计表写放大 | 日聚合 + UPSERT，不是每次调用一行 |
| 锁名单后忘记补执行闸 | 取消「请求里没有」这一隐性依赖是第 2 步的硬要求 |

回退方式：每一步都可用配置关闭（目录/要点渲染、默认展开表、run_program），
关掉后回到当前行为，不需要回滚数据。
