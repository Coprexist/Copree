# API 配置：一种配置、三个归属、一条链

**状态**：批次二已落地（2026-11-25）。已完成：单一解析器、删两档裸 Key、
迁移 0131 清空僵尸列、三条机械测试。未做：批次一（界面 stepper / 绑定数 / 请求链路入口）、
批次三（池改配置表）。2026-11-25 由用户与助手逐条确定；凡「助手推断」处均非用户构思。
**范围**：LLM 的 API 配置——厂商 / 配置档 / 池 / 解析链 / 界面呈现。语音共用同一套表，本文只在与形状相关的部分提及。
**取代关系**：`docs/dev/api_profiles.md` §2「解析链（唯一入口）」写的是当时的意图，与实现不符（见 §2.1）。本文落地时改写那一节，全仓只保留一种说法，不新增第二份规则文档。

---

## 0. 一句话

**配置只有一种形状，分三种归属；解析只有一处实现；界面显示的那条必须就是实际在跑的那条。**

---

## 1. 问题与证据

### 1.1 一份外部反馈（2026-11-24）

来源：用户转来的 `api-config-issues-2026-11-24.md`（环境写的是 Copree 0.4.16）。逐条核对当前 HEAD 的结果：

| # | 反馈的说法 | 在当前 HEAD 上核实 |
|---|---|---|
| 1 | 系统级 `provider_config` 与用户级 `user_api_providers` 两套并存 | 成立。系统级已不参与「地址 / Key」解析，只剩「按 base_url 反查元数据」；真正的病是解析实现不止一份 |
| 2 | `users.api_base_url` 仍在解析链第 ③ 层 | 成立（`executor.py` 的 user_key 分支） |
| 3 | 无 Key 时静默跳过，误伤不需要 Key 的本地模型 | 成立，但只在「没绑配置档」时：绑了档（`credit_source == "profile"`）已放行 |
| 4 | 没有任何「当前生效配置」的展示 | 成立：`credit_source` 只出现在日志 |
| 5 | 各层之间没有一致性校验 | 成立 |
| 6 | 插件商城保存本地预设必 500（`KeyError: work_model`） | **已修**（2026-11-23 收口进 `build_provider_config`）。反馈贴的 `admin.py:3411` 是老行号 |
| 7 | `system_layout` 探测只覆盖「已登记通道」 | 部分成立：绑配置档的路径用用户级厂商行，layout 正常；未绑时按地址兜底（localhost / 私网 → leading）已存在。真正空转的是**域名形式的本地地址**，且缺少「保存时探测」之外的回退 |

### 1.2 四份自相矛盾的「唯一入口」

同一件事——「这次请求用谁的 Key、打哪个地址、用哪个模型」——有四份实现，其中三份的文档还都写着「唯一入口」：

| 文件 | 自称 | 实际实现 | 调用情况 |
|---|---|---|---|
| `services/agent/api_config_resolver.py` | 唯一的回退链入口 | 配置档 → 本 AI → 用户全局 → 兜底 | 只有「配置档」那一档被 `executor` 调；`resolve_llm_config()` 零调用点 |
| `services/agent/llm_credentials.py` | 唯一入口 | 用户 Key → 池 Key | 世界 AI / 创建助手在用；**没有配置档、没有 AI 级** |
| `services/infrastructure/user_credentials.py` | 唯一入口 | 只读 `users` 两列 | 只认用户全局；**不知道配置档和池** |
| `routers/agents.py` 的 `_get_user_api_key` / `_get_user_api_base` | 无（私有小函数） | 只读 `users` 两列 + `settings.deepseek_base_url` | 「AI 生成性格」用；同上，**不知道配置档和池** |

另有 `services/agent/model_catalog.py` 自己拼模型清单、`ai/decider.py` 自己返回 `settings.deepseek_base_url`。四份实现里没有一份覆盖完整的那条链。

### 1.3 三次数据迁移留下的僵尸字段

0123 把账号级配置迁成兜底配置档、0124 把每个 AI 的配置迁成配置档，但**都没有清空原列**：

- `users.api_base_url / api_key_encrypted / global_chat_model / global_work_model`
- `agents.api_base_url / api_key_encrypted / chat_model / work_model`

于是同一份配置在库里存在两份。后果不是「永远不生效」，而是**解绑配置档之后会掉回这些遗留值**——用户以为回到了「用户全局默认」，实际用的是迁移前那一刻的快照。

### 1.4 兜底是假的

解析链最后一级是 `api_base = settings.deepseek_base_url`、`api_key = None`。系统级配置按既定口径**不存 Key**，所以这一级永远打不通：本地模型（不需要 Key）被它当例外拦住，云端模型则拿空 Key 去请求。

---

## 2. 用户确定的设计

> 「这个不应该跟配置走吗，就是先按 AI 自己的配置走，失败了按自己设置的全局默认配置走，失败了再按池 api 配置走」
>
> （随后本人修正：「我顺序说错了，2 和 3」——即池在全局默认之前，与本文 §3.1 一致。）

> 「我们之前说了必须绑定，否则会出跨厂商 key（key 与其对应使用环境不对应）问题。」

> 「所有地方都应该写 key 的调用按他的配置中的那些来，写死就不会出问题了？而且统一，要优雅。」

> 「可以是新表也可以不是，但是结构必须得一样是配置。」

> 「我还以为可以像 class 一样呢……哦哦。」

由此得到三条规则：

- **R1 绑定**：Key 必须长在「厂商 + 地址 + 模型」这条配置上，不允许裸存。
- **R2 单一**：全仓只有一处决定「用哪条配置」，其余调用点只消费它的结果。
- **R3 一致**：界面上标为「生效中」的，必须就是这一次请求实际用的那条。

### 2.1 概念：同一个类，不同的持有关系

用户先想到的是「池配置是不是配置的子类」，随后自己修正为「结构必须一样」。落到模型上是：

```
class Config:            # 一种形状，一份定义
    provider: {base_url, default_key, models, default_primary, default_secondary, layout, thinking, preset_key, kind}
    profile:  {name, provider, key?, primary, secondary?, is_default, sort_order}

class Pool:              # 持有，不是继承
    members: [(Config, priority, concurrent_limit)]

class Agent:  bound: Config | None
class User:   default: Config | None
```

**不采用表继承（STI / CTI）**：那会换来一个 polymorphic 判别列、池表里一堆永远 NULL 的配置字段，以及每次查询都要处理「这行是哪种」。本场景里池配置与个人配置从不合并查询，需要统一的只是「形状相同」，用同一张表即可。

### 2.2 字段归属判据

| 字段回答什么 | 归哪 |
|---|---|
| 这条配置**怎么请求**：地址、Key、模型清单、layout、thinking | 配置（`user_api_providers` / `user_api_profiles`） |
| 平台**怎么调度、谁能用**：优先级、并发、启停、哪个池、分给谁 | 池（`api_pools` / `api_pool_members` / `user_api_assignments`） |

现 `api_key_pool` 把两件事塞在一行里（连 `models` 都自己存一份，与 provider 的清单打架），这是要拆掉的对象。

---

## 3. 解析链

### 3.1 三档 + 一步失败（默认：按额度）

```
① 池配置              账单人还有额度（platform_gifted_credit + api_credit > 0）、分得到成员、且成员的 kind 与本次请求一致
      ↓ 不命中
② AI 绑定的配置        agents.llm_profile_id → 有效（厂商在、档在、Key 可解）
      ↓ 不命中
③ 用户默认配置        user_api_profiles 里该用户 kind=llm 且 is_default 的那条
      ↓ 不命中
④ 失败                报「未绑定可用配置」，不再打 settings.deepseek_base_url
```

每一档**整条命中或整条跳过**：地址、Key、模型一起取，不存在「地址来自 A、Key 来自 B」。

### 3.2 两套顺序：「优先使用本人配置」

用户 2026-11-25 拍定：默认**按额度**——平台送的池额度先用，用完（credit 清零）自然落到自己的配置；
开关 `users.prefer_own_key`（界面文案「优先使用本人配置」）打开后改成**本人配置优先**：

| | 顺序 |
| --- | --- |
| 关（默认） | ① 池 → ② AI 绑定 → ③ 用户默认 |
| 开 | ① AI 绑定 → ② 用户默认 → ③ 池兜底 |

- 池那一步自带额度判断，清零时直接跳过，不需要「先撞一次 402」；
- 开关语义从旧的「优先使用本人 API Key」改成「优先使用本人配置」——现在是一整条配置档
  （地址 + Key + 模型 + 能力），不再是单个 Key；
- 包断额度（`agents.api_credit_cost`）功能 2026-11-25 起**暂时关闭**：创建时拒绝非 0 值，
  编辑只允许保持原值。它原本该做到「该 AI 调用不扣额度」，但创建不扣、删除返还错账户、
  charge 不识别包断，三处都没接上，先不让用户为此付费。

### 3.3 删掉的两档

- `agents.api_base_url` + `agents.api_key_encrypted`（裸 Key，且地址回退到 `settings.deepseek_base_url`——只填 Key 不填地址就会把别家的 Key 打到 DeepSeek 上，正是 R1 要禁止的）
- `users.api_base_url` + `users.api_key_encrypted`（同上，且会让排查者误以为「当前用的是 DeepSeek」）

两列保留一个版本、标 deprecated、只读；解析链不再引用。

---

## 4. 数据模型与迁移

### 4.1 表

```
配置（不新增形状）
  user_api_providers    厂商：base_url / default_api_key_encrypted / options / default_primary
                        / default_secondary / thinking_supported / system_layout(+source/hash/checked_at)
                        / preset_key / kind
  user_api_profiles     配置档：name / provider_id / api_key_encrypted / primary_model / secondary_model
                        / is_default / sort_order / user_id
                        平台那个账号（user_id=0）名下的是池用的配置，与个人配置同表同形

池（只有调度语义）
  api_pools             id / name / kind(llm|voice) / is_active / note
  api_pool_members      pool_id / profile_id / priority / concurrent_limit
  user_api_assignments  现有表：user_id / pool_id（原 pool_key_id 改指池）
```

池成员是**引用**配置，不复制字段；因此「配置的形状只有一份」由结构本身保证，不需要 `scope` 列去标记谁是池里的。

**池可以混厂商**：成员是配置，配置自带厂商，所以「5 把 DeepSeek + 3 把 Qwen + 1 个本地 llama.cpp」同池并存没有问题。

**同一厂商的多把 Key 不是多个厂商行**：一个厂商行（地址、模型清单、默认 Key 都属于同一家）下面挂多条配置档，一把 Key 一条。9 条池成员在上面的例子里是 3 个平台厂商行 + 9 条平台配置档，形状与个人配置完全一致。

**池带 kind**：`api_pools.kind` 决定装哪一类配置（LLM / 语音），成员只装同 `kind` 的配置——否则「分到哪个池」对不上是 LLM 还是语音。

### 4.2 迁移（0131 / 0132 / 0133）

> 编号顺延：0130 已被上下文压缩段（context_segments）占用。

**0133 对话日志补三列**（批次一，见 §6.7；尚未实现）

给 `ai_conversation_logs` 补 `api_source`（profile / pool / default）、`api_base_url`、`api_profile_name`（快照字符串，配置档改名或删除后仍能看到当时用的是谁）。旧行留空 = 本次迁移前的记录。

**0132 池改配置**（尚未实现）

1. 每条 `api_key_pool` → 一行平台厂商 + 一行平台配置档（`user_id = 0`），字段对应：
   `api_base_url → provider.base_url`、`api_key_encrypted → provider.default_api_key_encrypted`、
   `models → provider.options`、`default_model → provider.default_primary`、`name → 两者 name`。
   `provider_name`（按名字模糊关联）→ `provider.preset_key`（有对应内置预设时），否则留空。
2. 一条 `api_pools` 记录承载这些成员的调度语义；`priority` / `concurrent_limit` / `is_active` 进 `api_pool_members`。
3. `user_api_assignments.pool_key_id` → `pool_id`。
4. `api_key_pool` 保留只读并标 deprecated，不立刻 drop。

**0131 清僵尸列**（已实现）

1. 核对：0123 / 0124 建出的配置档是否覆盖了 `users` / `agents` 上残留的值。
2. 有差异的补建配置档并绑定（沿用 0124 的口径：同用户内 (地址, Key, 聊天模型, 工作模型) 相同只建一条）。
3. **清空** `users.api_base_url / api_key_encrypted / global_chat_model / global_work_model`。
4. **清空** `agents.api_base_url / api_key_encrypted / chat_model / work_model`。

清空是本次迁移的重点：不清等于把人从「配置档」推回一份看不见的快照里。

### 4.3 回滚

三个迁移各自可 downgrade；`api_key_pool` 只是标 deprecated、数据仍在，回滚时能把池记录还原。清列不可逆，因此 0132 的 downgrade 只还原列结构，不还原值——迁移前先做一次库备份。

---

## 5. 唯一入口

### 5.1 形状

```
纯函数  resolve(*, agent, user, profile, provider, pool_member, default_profile) -> Resolution
取数壳  resolve_llm(db, agent, *, bill_to=None, kind="llm")        -> Resolution
```

`Resolution` 同时是「这次怎么请求」与「为什么是它」：

```
{
  base_url, api_key, chat_model, work_model,      # 怎么请求
  models, default_model, thinking_supported, system_layout,
  source,          # profile | pool | default | none
  chain: [         # 决策轨迹，四步，见 §3.1
    {level, status: hit|skip, reason, profile, provider, model}
  ]
}
```

### 5.2 消费点

| 调用点 | 现状 | 改后 |
|---|---|---|
| `ai/executor.py::_get_api_config` | 自己实现五档 | 删除，改调 `resolve_llm` |
| `ai/response_worker.py`（群 / 私聊） | 拿四元组判 `no_api_key` | 只消费 `Resolution` |
| `ai/alarm.py` | 同上 | 同上 |
| `services/agent/llm_credentials.py` | 用户 Key → 池 Key | 删除，改调 `resolve_llm` |
| `services/infrastructure/user_credentials.py` | 只读 `users` 两列 | 删除（或收窄成「这条厂商的 Key」单一职责） |
| `services/agent/model_catalog.py` | 自拼清单 | 用 `Resolution.models` |
| `ai/decider.py` | 自返回默认地址 | 用 `Resolution` |

### 5.3 三条机械保证

单测（不是靠 review）：

1. **形状契约** `test_config_shape_contract`：承载「配置」的表，列集合双向等于 §4.1 的契约；池表不得出现配置字段。
2. **单入口** `test_single_resolver_entry`：除 `api_config_resolver.py` 外，`backend/app` 内出现 `api_key_encrypted` 读取即失败（写入端与模型定义白名单除外）。
3. **无默认地址** `test_no_default_base_url`：除 resolver 外，`settings.deepseek_base_url` 不得出现在请求地址的位置。

这三条就是用户说的「写死就不会出问题」。

---

## 6. 界面

> **实现状态（2026-11-25）**：stepper 在 frontend/src/components/api-config/RuntimeChain.tsx，
> 数据来自 GET /agents/{id}/api-chain（与真实请求共用 resolve_llm，显示即实跑）；列表的
> 「生效中 / 未生效」徽标与设置页的绑定数浮标在 ProviderCards.tsx，绑定数由
> /user/api-config/providers 的 used_by / used_by_count 一次查出（LLM 与语音两处都算）。
> 「请求链路 →」只对管理员显示，跳到 /admin/conversation-log?agent=<id> 并定位到该 AI。
> ② 池节点暂不可点：池还没有独立界面，点了会进死链——等池改配置表（0132）时一起做。

### 6.1 三个位置的职责

| 位置 | 回答 | 数据来源 |
|---|---|---|
| AI 详情 → API 页签 → **stepper** | 这次实际用哪条、前面为什么跳过 | `Resolution.chain` |
| AI 详情 → API 页签 → **配置列表** | 可选项；哪条已选、哪条绑了没生效 | `agents.llm_profile_id` + `Resolution` |
| 设置 → API 配置 → **配置卡片** | 这条配置被哪些 AI 绑定（反向，静态） | 一次 `group by llm_profile_id` |

### 6.2 stepper（独立区块，在列表之外）

```
实际使用
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ ① 池          │ › │ ② AI 绑定     │ › │ ③ 用户默认    │ › │ ④ 无法请求    │
│ gemma4        │   │ 无额度        │   │ 家里          │   │ 未配置        │
│ llama.cpp     │   │               │   │ deepseek      │   │               │
│ ● 命中 · 无需Key│  │ 跳过          │   │ 跳过          │   │ 跳过          │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
```

- 四个节点常驻、等宽，位置不因命中项而重排。
- 命中的节点高亮，其余灰暗；未命中节点的底行写**原因**（未绑定 / 无额度 / Key 解密失败），不是笼统的「跳过」。
- ④ 平时灰暗，三个都没命中时变红并给「去绑定」入口。
- 节点之间用 `›` 分隔，不画连线。
- 可点：① 定位到列表对应行；② 管理台池设置；③ 设置页默认配置；④ 新建配置引导。
- 窄屏（`< 640px`）改竖向四行，左侧一条竖线串起来，命中段变绿、④ 变红。
- 不做呼吸灯/闪烁；只有 150ms 入场淡入。这块是结论，不是监控面板。

### 6.3 配置列表里的状态

| 语义 | 视觉 | 出现条件 |
|---|---|---|
| 已选（声明） | 现有 ✓ | `agents.llm_profile_id` 指向它 |
| 生效中（事实） | `<Badge tone="mint">生效中</Badge>` | 它是 `Resolution.source` 指向的那条（池配置不在列表里，故通常不出现） |
| 未生效 | `<Badge tone="accent">未生效</Badge>` | 它是已选的那条、但①没命中 |

「未生效」只出现在①上，不会满屏。灰色等第 ④ 步的失败不在此列——它由 stepper 承担。

### 6.4 设置页的绑定数（反向索引）

- 配置行右侧、⋯ 左边：`≥1` 时显示 `<Badge tone="muted">2</Badge>`；0 不渲染。
- 点浮标或 ⋯ →「使用情况」：同一个 `MenuPanel`，列出 AI 名（点击跳 AI 详情）；空列表给一句「还没有 AI 使用这条配置」。
- 池卡片同一位置改为「已分配 N 个用户」，点开列用户名。
- 语义严格是「**N 个 AI 绑定了这条配置**」，不是「正在使用」——后者依赖额度与有效性，静态统计不出来。

### 6.5 平台标准样式（在此基础上的实现口径）

不新造外观，一律走现有语义类与组件：

| 需要 | 用 |
|---|---|
| 节点 / 卡片 | `.card`（需要可点时加 `.card-interactive`） |
| 徽标、计数、状态 | `<Badge tone=…>`（mint / accent / muted / rose） |
| 按钮 | `<Button variant=… size="sm">`（卡片内一律 32px，由 CSS 兜底） |
| 菜单面板 | 现有 `MenuPanel` / `MenuItem`（`ProviderCards.RowMenu` 已有实现） |
| 浮标 | `<Badge tone="muted">`，不另造圆点 |
| 圆点 + 文案 | 按 `ui_system.md` §5：颜色即语义，圆点用 `●` 字符前缀 |

需要两个新变体（命中 / 失败），按 `ui_system.md` §6 的流程**只加在 `index.css` 的 `@layer components`**：

```css
.card.is-live { border-color: rgb(var(--tw-mint-500) / .45); background: rgb(var(--tw-mint-500) / .06); }
.card.is-fail { border-color: rgb(var(--tw-rose-500) / .45); background: rgb(var(--tw-rose-500) / .06); }
```

页面里不拼 `border-mint-400/50 bg-mint-400/5` 这类组合；未命中的灰暗用工具类 `opacity-55`（状态类，可接受）。

### 6.6 i18n

新增文案全部进 `frontend/src/i18n/ns/` 对应命名空间（zh / en / ja 三份）：
`runtimeChainTitle`、`runtimeHit`、`runtimeSkipNoBind`、`runtimeSkipNoQuota`、`runtimeSkipBadKey`、`runtimeFail`、`badgeLive`、`badgeNotLive`、`usageCount`、`usageEmpty`、`goBind`、`openRequestLog`。

### 6.7 请求链路入口

stepper 回答的是「**现在**用哪条」，一次性的结论。要看「**刚才那次到底发生了什么**」——完整的 messages、工具调用、token、以及这一轮用的是哪条配置——去对话日志，不在本页另做摘要。

- `ai_conversation_logs` 本来就是「每轮一行」的账，补三列即可（0130）：`api_source`、`api_base_url`、`api_profile_name`（快照字符串，配置档改名或删除后仍能看出当时用的是谁）。
- stepper 区域右上角一个文字按钮：`请求链路 →`（`openRequestLog`），跳到「对话日志」页并带上 `?agent=<id>`，默认展开该 AI 最新一条记录。
- **入口按权限出现**：对话日志页由 `conversation_log_config.default_user_log_access` 控制。没有权限的用户看不到这个按钮——不摆一个点进去 403 的入口。
- 不额外做「最近一次请求」摘要接口：日志页已有的详情就是完整请求，重复一份只会有第二个真相。
- 顺带价值：日志按时间排列，能看出「从哪天开始从配置档掉到池里」——这是 stepper 给不了的时间维度。

> 需要在 `ConversationLogTab` 支持一个 `agent` query（默认筛选该 AI、展开最新一条）。这是本页唯一的改动点，日志页本身不加新功能。

---

## 7. 启动自检

只 WARN，不阻塞启动（对齐 embedding 那条线的做法）。实现在
`services/agent/config_selfcheck.py` 的 `collect_api_config_problems(db)`——返回
人话字符串列表（空 = 没问题），`bootstrap` 的 lifespan 里调一次、逐条 `logger.warning`。

**不进 `/health`**：那是给外部监控看的「活着 / 哪个版本」信号；配置卫生是给自己排障的，
混进去会让监控因为一条历史脏数据报警。

| 检查 | 判据（静态，不联网） | 告警文案要点 |
|---|---|---|
| 裸 Key 两列残留 | users 的 4 列、agents 的 4 列任一非空 | 哪张表、哪个 id；提示「迁移 0131 应已清空，说明有人绕过写入」 |
| 默认档缺失 / 重复 | 有 kind=llm 配置档、却没有 `is_default` 的用户；或 `is_default` > 1 | 用户 id、实际条数；**没配过的人不报**——那不是错 |
| 配置档指向的厂商 kind 不符 | profile.provider 缺失，或 provider.kind != llm | 配置档 id / 名称 |
| 系统预设仅模板 | 每次启动记一条 info（不是 warning） | 「系统预设不参与运行时解析，别以为它在生效」 |

**池分配不查**：user_api_assignments.pool_key_id 上有 ON DELETE CASCADE，删池 Key 会
连带删分配，结构上不可能悬空——机械保证已经在了，再加一条运行时检查只是自欺。

**为什么不做「配置档指向的地址全不连通」**：那是网络探测（每家 5 秒），启动时做会把
启动时间拖成分钟级；要查连通性用管理台的「测试连接」，那里有超时与并发控制。

## 8. 不变量

| 编号 | 不变量 |
|---|---|
| C1 绑定 | 任何一次请求的 Key 都来自一条配置，且该配置的厂商与地址一致；不存在裸 Key 路径 |
| C2 单一 | 「用哪条配置」只有一处判断；界面显示与实际请求共用同一个结果 |
| C3 整条 | 一档要么整条命中，要么整条跳过；不存在跨档拼接 |
| C4 形状 | 池成员引用配置；池表不含配置字段，配置表不含调度字段 |
| C5 可见 | 有可用配置时界面必须能指出是哪一条；无可用配置时必须说明缺哪一环 |

---

## 9. 失效模式

| 编号 | 失效 | 表现 | 对策 |
|---|---|---|---|
| F1 | 迁移漏清裸列 | 解绑配置档后掉回迁移前的快照 | 0131 清列 + 启动自检 |
| F2 | 配置档失效（厂商被删 / Key 解不出） | 静默回落到②③ | stepper 标注原因；列表标「未生效」 |
| F3 | 池无额度但仍被选中 | 请求 401 / 402 | 命中条件里额度是前置判断 |
| F4 | 本地模型被「无 Key」拦住 | AI 一句话不说 | 按「这条配置需不需要 Key」判断，不按「有没有 Key」 |
| F5 | 界面显示与实跑不一致 | 排查成本高 | 展示与请求共用 `resolve_llm`（C2 的结构性保证） |

---

## 10. 分批与验证

| 批次 | 内容 | 进度 |
|---|---|---|
| 一 | #3 无 Key 判断（已做）+ #4 展示（stepper / 列表 / 浮标 / 请求链路入口）+ 日志补列 0133 | 一半：#3 与 usable 判定已落地；展示与日志未做 |
| 二 | 单一解析器 + 删两档裸 Key + 自检 + 三条机械测试 | ✅ 2026-11-25：resolve_llm / resolve_user_llm、test_api_config_contract.py、test_api_config_resolver.py |
| 三 | 池迁移（0132）+ 清列（0131） | 清列 ✅ 2026-11-25（含补建孤儿 Key 的配置档）；池改表未做 |
| 四 | #7 模板自纠正（任意自建 / 中转通道的 system 摆放） | ✅ 2026-11-25，见 §11.4 |

每批都要跑：`docker exec` 内 pytest、前端 `tsc --noEmit` + 三个 check 脚本；改了 LLM 相关再用本地小模型真跑一遍（`scripts/selfcheck_local_llm.py`）。

---

## 11. 实现默认（不需要产品决策）

以下四条是实现层面的取舍，不属于待拍板的产品问题，按此执行；要改再改。

1. **语音链留到后续**：`agent_voice_configs` 上也有裸 `base_url / api_key_encrypted`，与 LLM 同构。等本文落地后照抄一遍，不同时改两条线。
2. **只支持一个池**：`api_pools` 结构允许多条，但先不提供创建入口、也不定「用户怎么分到哪个池」的规则。等真有按额度档或按厂商分池的需求再设计。
3. **平台归属用 `user_id = 0`**：沿用仓库已有的 `SYSTEM_USER_ID = 0` 约定（系统通知那套在用）。新建系统账号要额外处理「不能登录、不计入用户数、不进后台列表」，代价更大。
**4. 模板错误自纠正（#7 落地设计）**

请求返回 400 且错误含 `System message must be at the beginning` 时，把这条厂商的
`system_layout` 纠正为 `leading`、`system_layout_source` 记为 `detected` 并写回，
下一轮生效。运行期探测每轮 5 秒超时，不值——所以只在真报错时学一次。

**数据流（四层，单向，各管一件事）**：

1. `ai/llm.py` 只做「识别」，不碰库：
   `_raise_classified_error(..., base_url)` 遇到上述错误文本 → 抛
   `SystemLayoutError(base_url, error_text)`；其它 400 行为不变（仍是普通 Exception）。
   这样 LLM 层保持纯传输，不在错误路径里开数据库会话。
2. `services/agent/channel_layout.py` 做「学习」，是唯一写 `system_layout` 的地方：
   `learn_from_error(db, base_url, error_text) -> bool` 按 base_url 找用户厂商行，
   写 `system_layout=leading`、`source=detected`、`checked_at=now`；已一致则直接返回。
   **只改用户厂商行，不改系统预设**：系统预设是管理员维护的模板，自动改写会让
   「我只填了个地址，配置怎么自己变了」无从解释；系统预设那条只在日志里提示管理员。
3. `ai/executor.py` 的 LLM 异常处理捕获它：调一次 `learn_from_error`（失败只记日志，
   不影响本轮），本轮按已有错误路径处理（告知 owner「这家要求 system 放开头，
   已自动记录，下一轮生效」），**不重试**——重试要用新布局重建整个请求，风险与收益
   不划算，而下一轮就会用新结论。
4. 可观测：纠正成功记 info（哪条通道、从哪档改成哪档）；§7 的启动自检复查
   `system_layout_source` 的分布——同一家反复被纠正，说明有人在手选里改回去了。

**幂等**：DB 已是 `leading + detected` 就直接返回；同一 base_url 10 分钟内只写一次
（进程级内存桶）。多进程各写一次也无害（结果相同，最后一次覆盖）。

**测试**：错误文本匹配（含大小写、前后缀、其它 400 不触发）；写回用户厂商行且不动
系统预设；已是 leading 时不重复写；`_raise_classified_error` 的分类不回归。
