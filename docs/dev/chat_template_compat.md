# 按对端 chat 模板适配消息结构

**一句话**：system 怎么摆由这条通道自己声明；声明来自探测、预设或手选，请求侧只读一个值。

## 背景问题

2026-11-22 辰天反馈：llama.cpp `--jinja` 加载 Qwen3 接 Copree，AI 完全无法回复，
服务端每轮 500：

```
Jinja Exception: System message must be at the beginning.
```

根因：Copree 为了前缀缓存把"这一轮才知道"的信息全部作为 system 追加在对话末尾
（`llm.py` 共 16 处）。Qwen 的 chat template 把"system 只能在第一条"写成硬校验
（`raise_exception`），llama.cpp 照模板渲染就抛异常。

**同一个模型，不同渲染器表现完全不同**（实测）：

| 渲染器 | 中间 system 会怎样 |
|---|---|
| llama.cpp `--jinja` | 直接执行模型自带 Jinja → Qwen `raise_exception` → **500** |
| ollama | 自己的 Go 模板：把多条 system 合并进 `.System`，挪到开头（内容不丢、位置丢） |
| OpenAI / DeepSeek | 原样接受 |

所以问题不在模型，在**谁负责渲染**。ollama 那层兜底是它替我们做了合并——代价是缓存断点前移。

## 三档 layout

| 值 | 含义 | 消息怎么摆 |
|---|---|---|
| `anywhere` | 默认。OpenAI / DeepSeek 语义 | **原样不动**（缓存布局逐字节不变） |
| `leading` | system 只能出现在开头 | 稳定块合并成一条放头部；动态块**就地降级为 user** |
| `none` | 不接受 system 角色（Gemma 2） | 稳定块并入第一条 user；动态块同样就地降级 |

## 关键设计：稳定块 vs 动态块

**规则只有一条：历史之前的 system 是稳定块，历史之后的是动态块。**

```
message 0 : 人格 + 帧清单 + 会话定位        ← 稳定块（解锁点之间字节不动）
history   : user / assistant 交替
  ↑ 【想起】记忆召回、[上一轮交接]           ← 动态块（每轮变）
尾部      : 当前上下文、回合预算、时间        ← 动态块（每轮变）
```

处理方式必须分开：

- **稳定块合并进头部**：一次性重算，之后是稳定前缀。
- **动态块保持原位、只改 role**：断点位置与 `anywhere` **完全一样**，缓存没有额外损失。

**为什么不能全部折叠到开头**：缓存命中的是最长公共前缀。把动态块挪到开头，每轮变的内容
就跑到了最前面，整段前缀每轮失效。本地 ollama（CPU 7B）实测同一份 payload：

| | 预填充耗时 |
|---|---|
| 缓存命中 | 0.21 s |
| 前缀变了一个字 | 39.45 s |

**180 倍**。而且 ollama 自己就是这么干的（它把所有 system 合并到开头），所以我们判它
`leading` 主动处理——让它没有可合并的东西。

**身份靠正文，不靠 `name` 字段**：ChatML 的 Jinja 模板只读 `message.content`，
`name` 不在渲染路径上，填了也被丢掉。所以降级为 user 的动态块在正文前加 `[系统] `
（与工具附图那句「不是用户发的」同一个思路）。

## 权威链与存储

1. **用户手选**（`system_layout_source="user"`）
2. **自动探测**（`"detected"`，保存通道时跑一次）
3. **出厂预设**（`ProviderPreset.system_layout`，`"preset"`）
4. **默认** `anywhere`

**只存一处**：`system_settings.provider_config` 里该通道条目的：

```python
system_layout: "anywhere" | "leading" | "none"
system_layout_source: "user" | "detected" | "preset"
system_template_hash: "sha256:…"        # 模板指纹，判断"换模型了没有"
system_layout_checked_at: "2026-11-22T09:42:00+00:00"
```

请求侧只读 `system_layout`，其余三个是诊断与重探依据。解析入口只有一个：
`utils/pure/provider_config.py::resolve_system_layout`。

**地址与能力分开**："我要连哪个地址"的唯一真相是 `users.api_base_url` /
`agents.api_base_url`；`provider_config` 里那条只是**按地址缓存的能力**，不参与路由。

## 探测

- **时机**：保存通道时一次。**对话路径永不探测。**
- **方式**：三个端点并行，总预算 **5 秒**（不是每个 5 秒），谁先给结论用谁：

| 端点 | 谁 | 给什么 | 实测耗时 |
|---|---|---|---|
| `GET {root}/props` | llama.cpp | `chat_template`（Jinja） | 0.0004 s（404 也快） |
| `GET {root}/api/version` | ollama | 存在性 | 0.0003 s |
| `POST {root}/api/show` | ollama | `template`（Go 模板） | 0.16~0.22 s |

- `{root}` 由 `llm_endpoint.server_root` 剥掉版本段给出，不手拼。
- **两种方言分开判**：llama.cpp 给 Jinja（`{%- if %}`），ollama 给 Go 模板
  （`{{ .System }}` / `{{ range .Messages }}`）——同一套字符串猜不出来，由来源决定。
- 失败/超时：不写结论、保持现值、**保存照常成功**。
- 私网政策与连通性探针共用 `api_probe.allow_internal_policy`（管理员路径放行）。

判定（纯函数 `layout_from_template(template, dialect)`）：

- Jinja：非首条 system 有 `raise_exception` → `leading`；模板完全没有 system 概念 →
  `none`；所有 system 比较都被 `messages[0]` 守卫（循环里没有通用分支）→ `leading`
  （静默丢弃比报错更隐蔽）；其余 → `anywhere`。
- Go：`.System` 只出现一次且循环里不认 system → `leading`；循环里自己认 system →
  `anywhere`；只有 `{{ .Prompt }}`（gemma4）→ `anywhere`（没有角色概念，宁可宽松）。

## 调用点

```
provider_config 条目
   └─ resolve_system_layout()          ← 唯一解析入口（pure）
        └─ channel_provider_info()     ← 组装通道能力
             └─ chat_completion(system_layout=…)
                  └─ _build_chat_payload(system_layout=…)
                       └─ fit_system_layout()   ← 唯一出口，流式/非流式共用
```

## 预设

`ProviderPreset` 加 `system_layout`（缺省 `anywhere`）。本地预设：

| 预设 | base_url | 出厂 layout | 理由 |
|---|---|---|---|
| Ollama | `http://localhost:11434` | `leading` | Go 模板只有一个 `.System`，多的会被合并到开头 |
| llama.cpp | `http://localhost:8080` | `leading` | `--jinja` 直接执行模型模板，Qwen 会 raise |
| LM Studio | `http://localhost:1234` | `anywhere` | 走 OpenAI 兼容层，能正确保留位置 |
| vLLM | `http://localhost:8000` | `anywhere` | 同上 |

## 不变式

1. **`anywhere` 通道行为逐字节不变**——`fit_system_layout` 返回**原对象**，
   DeepSeek / OpenAI 的前缀缓存不受任何影响（有测试锁 `payload["messages"] is messages`）。
2. 对话路径**不做网络探测**，只读已存的 `system_layout`。
3. 三层不交叉：判定不碰网络、探测不碰 payload、payload 不碰模板。
4. 动态块**只改 role、不挪位置**——缓存断点位置与 `anywhere` 一致。
5. 探测总预算 ≤ 5s，失败不阻塞保存。

## 边界与未定义项

| 情况 | 处理 |
|---|---|
| `none` 且一条 user 都没有 | 新建一条 user 放最前，装合并后的稳定块 |
| 开头就是对话（没有稳定块） | 原对象返回，不把动态块往前提 |
| 模板认不出形状 | `anywhere` |
| 探测全失败 / 超时 | 不写记录，保持现值；保存成功 |
| 中转（one-api 等）后面挂严格模型 | `/props` 拿不到 → 落回预设或手选 |
| 用户已手选 | 探测**不覆盖**（`source="user"`） |
| 脏值（库里存了非法值） | 当没存，回落预设 |

## 测试

- `test_chat_template.py`：判定（Jinja 严格 / Gemma / 静默丢弃 / 通用 / Go 三种形状 /
  空与脏值）；归一化（anywhere 原对象、稳定块合并、动态块保位置、none 并入首条 user、
  无稳定块不动、不改入参、多模态）。
- `test_template_probe.py`：`/props`、`/api/version`+`/api/show`、全 404、慢端点
  不阻塞保存、指纹稳定。
- `test_system_layout_wiring.py`：解析优先级、payload 出口（anywhere 同一对象、
  leading 生效、脏值忽略）。
- 回归：`test_prefix_freeze` / `test_prefix_contract` / `test_group_context_ledger`。

## 落地进度

1. ✅ 纯函数 `layout_from_template` / `fit_system_layout` + 单测
2. ✅ `ProviderPreset.system_layout` + 四个本地预设
3. ✅ `template_probe`（三端点/5s/两方言）+ 保存时写回 + 单测（真机 Ollama 验证通过）
4. ✅ `_build_chat_payload` 接入（唯一出口）+ `resolve_system_layout`
5. ⬜ 前端：预设区下拉（标准 / 只放开头 / 不支持 system）+「已识别：Ollama · 来自模板」提示
   + 「重新识别」按钮

## 不做

- **不做"匹配报错文本 → 归一化重试"**：每轮多一次失败请求，且文本匹配会被上游措辞变化打断。
- **不改业务层生成 system 的方式**：尾部动态块是缓存设计的一部分
  （见 [能力懒加载](./capability_lazy_loading.md) 与 [会话历史与前缀缓存](./conversation_history.md)）。
- **不引入统一 LLM SDK**：问题不在客户端库，SDK 也不做这件事。
- **不自动重试探测**：报错只在下次保存/点「重新识别」时重探，请求路径保持干净。

## 参考

- [OpenAI 兼容端点拼接](./llm_endpoint.md)
