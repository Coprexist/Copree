# API 配置：厂商与配置档（LLM / 语音同一套）

> 读者：改这块代码的人。想知道**用户怎么用**看 `docs/guides/用户手册.md` §1.3。
> 实现入口：`backend/app/services/agent/api_config_resolver.py`（判断）、
> `backend/app/routers/user_api_config.py`（增删改）、`frontend/src/components/api-config/`（界面）。

## 1. 为什么是两层

一个人往往不止一套 Key：家里一把、公司一把、自建中转一把；而同一个 Key 又常被多个 AI 共用。
所以配置不是一个"全局默认"，而是**可命名的、可复用的**：

| 层 | 回答什么 | 表 |
| --- | --- | --- |
| **厂商**（provider） | 在哪请求：地址、模型/音色清单、自定义默认 Key | `user_api_providers` |
| **配置档**（profile） | 这次用哪一套：名称 + 厂商 + 可覆盖的 Key 与型号 | `user_api_profiles` |

一个 AI 只记一个指针：LLM 是 `agents.llm_profile_id`，语音是 `agent_voice_configs.voice_profile_id`。

**为什么 kind 在厂商上而不在配置档上**：配置档的种类由它所属厂商决定，冗余一份迟早不一致；
需要按种类过滤时 join 一次即可（个人级几十条，join 无压力）。

**语音与 LLM 同表同形**：列名用 primary / secondary 这种中性说法。
LLM 里它们是聊天模型 / 工作模型；语音里 primary 是音色，secondary 是 TTS 模型名（多数家留空）。

## 2. 解析链（唯一入口）

`resolve_from()` / `resolve_voice_from()` 是纯函数：只吃对象、不查库、不落库，单测直接喂构造数据。
查库只是外面的壳（`resolve_llm_config` / `resolve_voice_config`）。

    LLM：配置档（整包） → 本 AI 手填 → 用户全局 → settings 兜底
    语音：配置档（整包） → 这条语音记录手填 → 用户全局（同厂商才借）

两条铁律：

1. **配置档是整包**。选了档就全用它，不往下一层退——地址和 Key 分属两家只会得到 401 或"模型不存在"。
2. **Key 绝不跨厂商借**。解析时只读「配置档 → 它所属的厂商」这一条链，代码层面拿不到别家的 Key。

`provider_info_of()` 把解析结果拼成与 `channel_provider_info` **同形**的字典交给执行器
（`backend/app/ai/executor.py` 的 Tier 0）。注意它返回的 `models` 必须是**字符串数组**，
形状错了会让模型名校验静默失效——`test_api_config_resolver.py::test_provider_info_shape_matches_channel` 钉着这一点。

### 语音的取数入口

`backend/app/services/agent/voice_config.py` 的 **`voice_of(db, agent_id)`** 一次 join 取齐
「行 + 它此刻生效的配置」，返回 `(cfg, eff)` 或 `(None, None)`。

- `cfg` 带检测结论（`probe_ok`），门控看它；
- `eff` 是解析后的地址 / Key / 音色 / 模型，合成看它。

四个消费点全部走它，不再各自读 `cfg.base_url`：

| 消费点 | 用途 |
| --- | --- |
| `tools/chat_social/send_voice.py` | 合成语音 |
| `voice_config.has_voice_capability()` | 工具门控（这个 AI 现在能不能发语音） |
| `voice_config.probe_now()` | 检测（**测解析后的那一套**，否则会出现"档里是好的、测的是空行"的假失败） |
| `routers/agents.py` 的 `/voice/preview` | 试听 |

## 3. 接口

### 用户配置管理（前缀 `/user/api-config`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/providers?kind=llm|voice` | 厂商 + 名下配置档；Key 只回末四位 |
| POST | `/providers` | 新建 / 接入一家（带 `preset_key` 时从内置预设取材） |
| POST | `/providers/connect` | 从内置预设批量接入 |
| PUT | `/providers/{id}` | 改一家 |
| POST | `/providers/{id}/restore` | 按内置预设恢复默认（不动 Key） |
| DELETE | `/providers/{id}` | 删一家；名下有档要 `?force=true` |
| POST/PUT/DELETE | `/profiles{,/{id}}` | 配置档增改删 |
| POST | `/profiles/{id}/default` | 设为默认档（同一 kind 内互斥） |

删除有约束：默认档不许直接删（要先指认另一条为默认），厂商名下还有档时要显式 force。

### AI 绑定

`PUT /agents/{id}/config` 接受 `llm_profile_id` 与 `voice_profile_id`，
两段逻辑合并成 `agent_service._bind_profile(db, agent, kind=..., profile_id=...)`，只判三件事：

1. 档存在；
2. 档所属厂商的 `kind` 与要绑的种类一致（语音档不能绑成 LLM）；
3. 档属于**操作者自己**或**这家 AI 的主人**（管理员不限）。

第 3 条是有意的放宽：合作者可以把自己账号下的档绑到自己管理的 AI 上——用谁的 Key 谁付钱。
运行时解析不再按 owner 复核。

语音换档会调 `_invalidate_voice_probe()`：旧结论是上一个地址的答案，必须作废。

## 4. 前端结构

```
components/api-config/
  useApiDialogs.tsx    两个编辑器 + 选厂商弹窗的**唯一**开关；按 kind 取数
  ProviderCards.tsx    厂商卡片（mode="manage" 管理 / "pick" 选档），手风琴、默认折叠
  ProviderEditor.tsx   唯一能改厂商的地方（地址/清单/自定义默认 Key/能力声明）
  ProfileEditor.tsx    编辑一条配置档
  ProviderPickModal    「设置自定义默认 Key」的厂商选择
  KeyMark.tsx          「已设置 Key」的钥匙角标 + 说明（悬停/点击都出）
  ApiConfigSection     设置页一整块（标题 + 卡片）
  ProfilePicker        AI 详情「API」页签，复用 ProviderCards mode="pick"
```

- 设置页渲染两块：`<ApiConfigSection kind="llm" />` 与 `<ApiConfigSection kind="voice" />`。
- AI 详情：`<ProfilePicker agentId={id} kind="llm|voice" />`，点一行就 `PUT /agents/{id}/config`。
- 厂商字段**只在 ProviderEditor** 维护，配置档里不再放一份（避免两处漂移）。
- 内置厂商**点开只预填、保存才落库**（不再"点一下就被加入配置"）。

## 5. 迁移

| 版本 | 做什么 |
| --- | --- |
| 0123 | 建两张表 + 两个指针列；`users.api_*` → 兜底厂商 + 兜底配置档 |
| 0124 | 每个 AI 的自带配置 → 配置档；与兜底等价的复用兜底，有差异的新建并去重 |
| 0125 | 每条 `agent_voice_configs` → 语音厂商 + 配置档，并绑定 `voice_profile_id` |

三条都可重放：`downgrade` 只清指针与迁移出来的档（`name LIKE '%· 迁移配置%' / '%· 迁移语音%'`），
再 `upgrade` 结果一致。老库 `alembic upgrade head` 一次到位；新库 `create_all + stamp head`。

## 6. 约束与坑（改之前先看）

- **跨厂商永不借 Key / 模型**（见 §2），改回退链时别顺手"复用一下上一层的 Key"。
- **配置档整包生效**：任何"档里空就借上一层的地址"都会把两家拼在一起。
- **语音有两个来源**：选档时用档；没选时用 `agent_voice_configs` 行上那五个历史字段（老数据兼容）。
  解析层只读一处所以没有歧义，但确定不再支持手填后应把这几列删掉。
- **大表单弹窗不要 `backdrop-blur`**：滚动时每帧重算整屏模糊，长表单会卡死。
- **`Dialog` 必须 portal 到 body**；页面级浮层（宽度拖条等）层级不得超过 `z-modal`。
- Key 只回末四位，没有"读出明文"的接口。
