# 通道的 system 摆放（anywhere / leading / none）

一个**判定**、两处进料、运行时只读。改这一块之前先读完本文。

## 为什么要有这一层

Copree 为了前缀缓存，把「这一轮才知道」的信息全部作为 system 追加在对话末尾
（见 `app/ai/llm.py` 的 16 处 system append）。OpenAI / DeepSeek 容忍这种写法，但模板
是模型自带的：

| 渲染器 | 中间 system 会怎样 |
|---|---|
| llama.cpp `--jinja` | 直接执行模型 Jinja；Qwen 的模板 `raise_exception` → 500 |
| ollama | 自己的 Go 模板，把多条 system 合并进 `.System`（挪到开头） |
| OpenAI / DeepSeek | 原样接受 |

所以每条通道要有一档「system 能摆哪」：`anywhere` / `leading` / `none`。
适配规则在 `app/utils/pure/chat_template.py`（纯函数，判定与裁剪都在那里）。

## 2026-11 的事故

用户自己填了一个 `http://192.168.x.x:8080` 的 llama-server，选了一个 Qwen 模型。
厂商行里的 `system_layout` 是建表默认值 `anywhere`，没人判过，但**长得像结论**——
运行时照它拼出一堆中间 system，Qwen 的 Jinja 每轮抛 500：

```
Jinja Exception: System message must be at the beginning
```

根因不是「猜错了」，而是「没有判定这件事，也没有地方记录结论是怎么来的」：
用户手选的 `anywhere`、探出来的 `leading`、预设给的 `leading`、默认填的 `anywhere`，
在库里一模一样。

## 判定只写一遍

`app/services/agent/channel_layout.py` 的 `decide_system_layout()`：

```
手选（用户在界面上明确选了）        → source=user
  > 探测（保存时读对端 chat 模板）    → source=detected
  > 预设（平台维护的结论）            → source=preset
  > 按地址保守猜（字面私网 → leading）→ source=host
```

四条规则只在这里出现一次。调用方：

- `POST/PUT /user/api-config/providers`（用户配置档）
- `POST /user/api-config/providers/connect`（批量接入）与 `/restore`：`probe=False`，
  预设已经给了结论，为每一家各等 5 秒换不来新信息
- `PUT /admin/provider-presets/save`（管理员通道）：同一个函数，`allow_private=True`

## 存哪儿

两处，形状同构：

| 位置 | 字段 |
|---|---|
| `system_settings.provider_config[]` 条目（JSON） | `system_layout` / `system_layout_source` / `system_template_hash` / `system_layout_checked_at` |
| `user_api_providers` 表（0126 起） | 同四个列 |

`system_layout_source` 是**空**才有意义：它表示「从未判定」。历史行都是空，所以运行时
对它们按地址保守取值，而不是把默认的 `anywhere` 当结论。

## 运行时只读，两条路径同一条规则

- 用户配置档：`services/agent/api_config_resolver.py::provider_system_layout()`
- 管理员通道：`utils/pure/provider_config.py::resolve_system_layout()`

两者都是：有来源 → 信 `system_layout`；没来源 → `guess_layout_from_host(base_url)`。
对话路径**永不探测**（`services/agent/template_probe.py` 的文档也说这一点）：
每轮发 HTTP 去问模板既慢又不该在失败路径上做。

`guess_layout_from_host()` 是纯函数、零 IO，只认字面量：

- `localhost` / `127.0.0.0/8` / `::1` / `10/8` / `192.168/16` / `172.16/12` → `leading`
- Docker 固定的本机别名 `host.docker.internal` / `gateway.docker.internal` → `leading`
  （Copree 在容器里、模型在宿主上是最常见的自托管形态；这两个名字是字面量，不是域名解析）
- 其它（含内网主机名 `gpu-box`）→ `anywhere`

猜错的代价不对称：对一个支持 anywhere 的服务猜成 leading，只是把 system 合并到开头
（缓存收益小一点）；反过来猜成 anywhere，就是每轮 500。

## 探测的边界

`probe_chat_template()` 只打三个固定端点（llama.cpp `/props`、ollama `/api/version`、
`/api/show`），5 秒总预算，失败不阻塞保存。

**私网地址只有在「已经保存过」时才允许探**（`services/agent/base_url_registry.py` 的
`saved_private_hosts()`）。否则这个接口就是一台内网扫描器。所以：

- 新建一条本机地址：还不在登记集里 → 退化成 `host` 猜 → 字面私网给 `leading`，够用；
- 之后再保存一次，地址已登记 → 探测给出 `detected`；
- 登记集包含 `users` / `agents` / `group_assistants` / `user_api_providers` 四处。

## 前端怎么表达

布局下拉的第一项是「自动识别」（值为空）。只有用户真的选了一档，才把值发给后端；
否则后端按上面的优先级判。列表里 `system_layout_source != "user"` 时下拉显示「自动识别」
——**不要**把自动结论回填成手选，那会让它从此不再重新判定。