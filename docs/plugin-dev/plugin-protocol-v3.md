# Copree 全面插件化 — 阶段三：服务类插件（category: service）

> 状态：**已实现**（2026-09-25）
> 前置：`docs/plugin-protocol-v2.md`（语言中立行为插件）、`docs/plugin_system/design/plugin_system_design.md`（目录即插件）
> 原则：现状导向 —— 不新造第三套插件体系，把已有的两套收敛成一套
> 动机：让「QQ 通道」这类**常驻服务**能做成插件，而不是硬塞进核心代码

## 1. 问题：改之前有两套插件，长得像但互不相通

| | 目录即插件 | 服务插件 |
|---|---|---|
| 位置 | `services/plugin/`（catalog / skill_bridge / api）+ `routers/plugins.py` + `models/plugin.py` | `services/infrastructure/plugin_registry.py` |
| 发现方式 | 扫磁盘（`backend/plugins/` + `DATA_DIR/plugins/`） | 代码 import 时自注册（`PluginRegistry.register()`） |
| 类别 | skin / skill / world / other | service（只有一个 `browser`） |
| 生命周期 | **无**（v2 第 9 节明确「不做事件总线、消息钩子」） | `get_status() / start() / stop()` |
| 开关 | 两级（管理员全局 + 用户个人） | 只有即时启停，**重启后靠 `bootstrap.py` 硬编码恢复 browser** |
| 前端 | `PluginManager.tsx` 下半屏（`/plugins`） | 同文件上半屏（`/admin/plugins`，5 秒轮询） |

后果：想加一个常驻服务（QQ 通道），只能写进 `services/`，跟目录插件体系完全脱节——**装不了、看不见、开关管不着、凭据没地方放**。

## 2. 目标

1. **一套协议**：`plugins/<id>/` + `plugin.json`，`category` 增加 `service`
2. **生命周期复用**：service 插件的 `start/stop/get_status` 直接用现有 `ServicePlugin`/`PluginRegistry`，不新造
3. **装好即可用**：目录出现 → 登记 → 按期望状态启动；关闭/删目录 → 先 `stop()` 再回收，不留后台残余
4. **凭据有地方放**：插件级配置有唯一存储与唯一读取入口，机密加密落库、永不回显
5. **概念最少**：作者只写一个 `plugin.py`，声明 + 行为 + 配置 schema 合一（延续 v2 原则）

## 3. 契约

### 3.1 目录结构

```
plugins/
  qq-channel/
    plugin.json      # 展示元数据（category: "service"）
    plugin.py        # @service 装饰器：声明 + 行为 + config_schema 合一
```

行为入口按 v2 规则推断：`plugin.py` 存在即为行为插件（`catalog.ENTRY_FILE` 仍只登记 JSON 载荷的 skin/skill，
service 不进 `ENTRY_FILE`——那是个 `json.loads` 表）。

### 3.2 plugin.py（QQ 通道将来的样子）

```python
from app.services.plugin.api import service, ServicePlugin

@service(
    name="QQ 通道",
    description="把 Copree 的 AI 接入 QQ 群：被 @ 时唤醒，走官方机器人 API",
    config_schema={
        "app_id": {"type": "string", "title": "AppID", "required": True},
        "client_secret": {"type": "string", "title": "ClientSecret", "secret": True, "required": True},
        # 本期表单只支持 string：多值先用逗号分隔，插件自己 split（不做"看起来支持数组"的假支持）
        "group_allowlist": {"type": "string", "title": "允许接入的群号", "description": "多个群号用逗号分隔"},
    },
)
class QqChannelPlugin(ServicePlugin):
    """一个插件 = 一个类：声明在上面，行为在下面。"""

    async def get_status(self) -> dict:
        # 只报事实：连没连上、当前挂了哪些群
        return {"installed": True, "running": self._task is not None, "groups": sorted(self._groups)}

    async def start(self) -> bool:
        cfg = await self.config()          # 唯一读取入口，机密已解密
        if not cfg.get("app_id") or not cfg.get("client_secret"):
            return False                   # 缺凭据就老实返回 False，缺失项前端会显示"待配置"
        self._task = asyncio.create_task(self._gateway_loop(cfg))
        return True

    async def stop(self) -> bool:
        if self._task:
            self._task.cancel()
            self._task = None
        return True
```

要点：
- `@service` 与 `@skill` 同构（一个装饰器 = 声明 + 注册），owner 由加载器注入，回收时精确回收
- **插件 id 取自目录名**，作者不写 id；`ServicePlugin` 由 `plugin/api.py` 转发导出，作者不必 import `infrastructure`
- `self.config()` 是插件拿配置的唯一方式，插件不碰 DB、不碰加解密

### 3.3 plugin.json

```json
{
  "id": "qq-channel",
  "name": "QQ 通道",
  "description": "把 Copree 的 AI 接入 QQ 群：被 @ 时唤醒，走官方机器人 API",
  "category": "service",
  "version": "1.0.0",
  "author": "Copree",
  "icon": "MessageSquare",
  "default_enabled": true
}
```

## 4. 生命周期（谁在什么时机做什么）

| 时机 | 动作 |
|---|---|
| 进程启动 | `bootstrap._startup_plugins()` → `apply_skill_plugins(db)`：只**登记**全局启用的 service 插件实例，不启动 |
| 启动收尾 | `_start_service_plugins()`：遍历注册表，按 `plugins.service_desired_running`（缺省 true）逐个 `start()`；`browser` 成为其中一个，**硬编码取消** |
| 管理员关闭插件 / 目录消失 | 先 `await plugin.stop()`，再 `PluginRegistry.unregister(id)` |
| 管理员点「启动 / 停止」 | 走 `/admin/plugins/{id}/start\|stop`，并写回 `service_desired_running`（重启后状态不丢） |
| 重扫（`/plugins/rescan`） | `apply_skill_plugins(db, force=True)`：重新导入代码，并把原本在跑的服务拉回运行态（重扫不改变运行状态） |
| GET `/plugins` | 只登记/回收，**绝不启动服务**——不能因为一次列表请求就把后台服务拉起来 |
| 进程退出 | `_stop_service_plugins()` 停掉全部注册的服务 |

一句话：**skill 插件管「AI 能做什么」，service 插件管「进程里跑着什么」，共用同一套目录、开关与加载时机。**

## 5. 配置与凭据

### 5.1 存储

新表 `plugin_configs`：`plugin_id`(FK 级联) / `key` / `value` / `is_secret` / 时间戳，唯一约束 `(plugin_id, key)`。
密文用 `utils/crypto.py` 的 Fernet（与用户 API Key 同一把钥匙、同一套实现）。

### 5.2 唯一入口 `app/services/plugin/config.py`

- `get_config(plugin_id)`：全量，机密解密；解密失败记日志并置空，不抛 500（与 `user_credentials.user_api_key` 同款处理）
- `set_config(plugin_id, values, actor=...)`：部分更新，未传的键不动；**空串 = 清除**；schema 之外的键直接拒绝；
  审计日志只记"改了哪些键"，**不记值**
- `mask_config(plugin_id)`：API 视图，机密只回 `has_value`，永不回显
- `get_schema(plugin_id)`：从已加载的 ServicePlugin 上取（未加载 = 空；所以配置流程是「启用 → 配置 → 启动」）

### 5.3 crypto 收敛

`encrypt_secret / decrypt_secret` 是唯一实现，`encrypt_api_key / decrypt_api_key` 变成同义别名——现有调用点零改动。

## 6. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/plugins` | 列表；service 类多带 `service: {running, config_schema, missing_required}` |
| GET | `/plugins/{id}/config` | 管理员；schema + 非机密值 + 机密键 `has_value` |
| PUT | `/plugins/{id}/config` | 管理员；部分更新 |
| POST | `/admin/plugins/{id}/start\|stop` | 启停；写回期望状态。**代码只是多了两行**——它读的就是 `PluginRegistry` |

## 7. 前端

`PluginManager.tsx`：

- `CATEGORY_ICON/LABEL` 增加 `service`（`Server` / 「服务」）
- **service 卡片落在「统一插件」区**：运行状态徽标 + 待配置提示 + 启动/停止 + 「配置」展开（schema 驱动表单，`secret` 用 password）
- 下半屏改为只列**内置服务**（`owner == null`，如 browser）——目录插件归统一插件区，**同一个插件只出现一次**
- service 类不显示用户个人开关：服务是全局资源，"用户个人启用某个后台服务"是假语义
- 文案走 i18n（`tool:pluginService.*`，zh/en/ja 三份），无 emoji

## 8. 改动面（实际）

| 文件 | 改动 | 性质 |
|---|---|---|
| `utils/crypto.py` | `encrypt_secret/decrypt_secret` + 旧名别名 | 重写（35 行） |
| `services/infrastructure/plugin_registry.py` | `config_schema`/`owner` 字段、`config()`、`unregister()`、状态带 owner/schema | 小改 |
| `services/plugin/api.py` | `@service` 装饰器 + 转发导出 `ServicePlugin` | 纯新增 |
| `services/plugin/config.py` | 配置读写唯一入口 | 新增 |
| `services/plugin/skill_bridge.py` | service 分支、`_loaded` 统一追踪、幂等加载、force 重载 | 中改 |
| `models/plugin.py` | `PluginConfig` 表 + `service_desired_running` 列 | 小改 |
| `alembic/versions/b7d1e2f3a4c5_plugin_service_and_config.py` | 迁移（已 apply） | 新增 |
| `routers/plugins.py` | `GET/PUT /plugins/{id}/config`、视图带 service 状态、rescan force | 小改 |
| `routers/admin.py` | 启停写回期望状态（`_remember_service_desired`） | 小改 |
| `bootstrap.py` | `_start_browser_service` → `_start_service_plugins`（含 shutdown） | 小改 |
| `frontend/src/utils/skin.ts` | `PluginConfigField` / `PluginServiceState` / `PluginView.service` | 纯新增 |
| `frontend/src/components/PluginManager.tsx` | service 卡片 + `PluginConfigForm` + 内置服务过滤 | 中改 |
| `frontend/src/i18n/tool.ts` | `tool:pluginService.*` 三语 14 键 | 纯新增 |
| `tests/test_plugin_protocol_v3.py` | 四层用例 | 新增 |

## 9. 验证闭环（四层，实测）

1. **加载层**：临时 service 插件 → 注册进 `PluginRegistry`、owner 正确、schema 带出、重复 apply 不换实例 ✓
2. **配置层**：机密落库是密文、插件读到明文、接口视图不含明文、schema 外的键被拒、空串=清除 ✓
3. **开关层**：关闭 → 从注册表摘除且**先 stop 后摘**（用插件自己写的痕迹文件验证）；内置 `browser`（owner=None）不被误删 ✓
4. **集成层**：`alembic upgrade head` 成功；重启后端日志出现 `[OK] 服务插件已启动: browser`；
   `/plugins/{id}/config` 路由已注册（无凭据实测 401，不是 404）；`tsc -b` 与 `i18n:check` 全绿；
   `python tests/run_without_pytest.py test_plugin_protocol_v3` → PASS ✓

## 10. 边界（不做）

- 不做 `plugin.js` 版 service（Node 子进程）——待有真实需求
- 不做插件市场 / 一键安装 / 版本升级
- 不做每用户级的 service 开关（服务是全局资源）
- 不做热重载：改 `plugin.py` 需 rescan（会重载）或重启
- 配置表单只支持 `string`（含 `secret`）；array/object 等有真实需求再补，不先做假支持
- 不动 v2 已有的 skill/skin 语义（完全兼容）

## 11. 顺带修掉的一个旧漏

以前"目录消失"只检查声明式技能（`_from_plugins`），**行为式插件的处理器会留在注册表里**。
现在统一由 `_loaded`（plugin_id → category）追踪，目录消失时连行为处理器一起回收。

## 12. 分阶段

- **阶段一（本文档 1–11 节）**：协议本身 ✅
- **阶段二（§13）**：`backend/plugins/qq-channel/` —— 第一个 service 插件，QQ 通道 ✅
- **阶段三（未定）**：plugin.js 版 service

## 13.5 多实例：一个插件，多份配置（协议调整）

写 QQ 通道时撞上的真问题：**同一个插件要接 3 个机器人**（每个 AI 一个自己的 AppID）。
原协议是"一个插件一份配置"，于是只能退化成"装 3 遍插件"——同一个插件在页面上出现三次、
代码加载三次、卸载还要分别摘。这不是插件多，是**配置多**。

所以调整协议：**插件声明与实例分离**。

| 概念 | 含义 | 存在哪 |
|---|---|---|
| 声明（ServiceDef） | 这个插件能当服务跑：类 + 展示信息 + 配置 schema + 是否多实例 | 内存（`plugin/api.py`，导入时产生） |
| 实例（instance） | 这个插件的一份配置 + 一个可独立启停的运行时对象 | 配置表 `plugin_configs.instance` |
| 注册表 key | `plugin_id`（单实例）或 `plugin_id:instance`（多实例） | `PluginRegistry` |

配套改动（都是为了"只有一处"）：

- `@service(multi_instance=True)` **只声明不实例化**；实例化交给加载器按 DB 里的实例列表逐个建，
  单实例插件就是"实例固定为空串"的特例——**单实例与多实例共用同一条加载代码**，不是两套
- `plugin_configs` 加 `instance` 列，唯一键变 `(plugin_id, instance, key)`
- 期望运行状态按实例存（新表 `plugin_service_states`），取代 v3 最初加在 `plugins` 上的单列——
  一个插件多个实例各自启停，一列装不下
- 配置接口从"部分更新"改成**列表即真相**：`PUT /plugins/{id}/config {instances:[...]}`，
  没提交的实例被删除（先 stop 再回收）
- 注册表 key 带实例后缀，所以启停接口 `/admin/plugins/{key}/start|stop` 一行没改就支持了实例
- schema 从**声明**读（不再要求插件已加载）→ 没启用的插件也能先看配置项

## 13.6 私聊：给 AI 一个能单独说话的门（协议调整）

群里 @ 是"公开说话"，私聊是"单独说话"。两者在 Copree 里是两条不同的链路（群消息 / 私信会话），
所以私聊不是给群通道加个分支，而是补齐对称的两半：

- 入站：`C2C_MESSAGE_CREATE` → `get_or_create_dm_session(QQ用户, 该AI的 user_id)` → `send_dm_message`
- 出站：`send_dm_message` 新增**私信出口分发**；`app/chat/dm_delivery.py` 与 `group_delivery.py` 对称，
  ws 路由与外部通道共用（`ws.py` 的私信分支也因此少了一截）

一个意外的好消息：Coprope 的私信规则本来就是「**涉及 AI 免好友校验**」（防的是人类之间的陌生人骚扰），
所以 QQ 用户私聊 AI 不需要先加好友——不用为桥接破例。
`dm_policy`（everyone / allowlist / off）是这个实例的收紧开关，默认 everyone。

## 13. 阶段二：QQ 通道插件（已实现）

`backend/plugins/qq-channel/` —— 一个目录 + 一个 `plugin.py`（含 `plugin.json`），走官方 QQ 机器人 API v2：

| 方向 | 做法 |
|---|---|
| 收 | WebSocket 长连网关（op10 hello → op2 identify → op1 心跳 → op0 dispatch），只订 `GROUP_AND_C2C_EVENT(1<<25)`；`GROUP_AT_MESSAGE_CREATE` 是入口 |
| 进 Copree | `send_gm_message` + `chat.group_delivery`（**与网页端同一条投递链路**：在线直推、离线暂存、唤醒 AI） |
| 出 Copree | 注册到 `chat.outbound` 的出口；只转发绑定群里 **AI** 发的消息 |
| 发 QQ | 群 `POST /v2/groups/{openid}/messages`、私聊 `POST /v2/users/{openid}/messages`；优先被动回复（带 `msg_id`：群 5 分钟/5 次、私聊 60 分钟/4 次），超时/超次转主动消息，并按 20/关系/分钟、60/Bot/分钟 限频 |

配置项（每个实例一份，加密项走插件配置表）：`app_id`、`client_secret`(secret)、`target_agent`、
`copree_group_id`（可留空 = 只做私聊）、`qq_group_allowlist`、`dm_policy`、`dm_allowlist`。
**多实例**：一个实例 = 一个 QQ 机器人 + 它背后的那个 AI；「每个 AI 绑自己的机器人」就是加几个实例
（见 §13.5）。私聊见 §13.6。

### 为此新增的两个共用层（阶段一没预料到，但它是"单一来源"的必然结果）

1. `app/chat/group_delivery.py` —— 「消息落库后谁来收、怎么收」原本长在 WebSocket 路由里，
   而它跟 WebSocket 其实没关系。抽出来后 **ws 路由与外部通道共用**：
   `message_view` / `fanout_group_message` / `wake_group_ai` / `forward_group_message_federated` /
   `maybe_vectorize_group_message`。`ws.py` 因此少了约 125 行，投递口径仍然只有一处。
2. `app/chat/outbound.py` —— 群消息出口分发（可注册多个出口）。原来 `send_gm_message` 里硬写了一句
   "喂给绑定世界"，现在世界是第一个 sink，QQ 通道是第二个；加通道不用再改核心。
   单个 sink 抛异常只记日志：**外部通道坏了不能拖垮"发消息"本身**。

### 已知限制（v1，写在插件文件头，免得被当成 bug）

- 多个 QQ 群共用一个 Copree 群时，**群**回复回到最近一次来消息的那个 QQ 群（私聊不受影响）
- 富媒体不回传；收到的图片/语音/文件转成文字占位（语音优先用官方给好的 `asr_refer_text`）
- 私聊不校验"是不是 AI 的创造者"：QQ 侧没有身份锚，认人要靠绑定流程（下一步）
- 改配置需要重启这个实例（凭据在 `start()` 时读取并生效）

### 验证

- 用例 `tests/test_qq_channel_plugin.py`（7 条）：群往返、私聊往返、重复 `msg_id` 去重、群白名单、
  私聊策略 off/allowlist、**多实例一份配置一个实例（含各自凭据不串、删配置即回收实例）**、
  非 AI/非绑定群不外流、坏 sink 不拖垮发消息、媒体占位
- 实测：启用前不加载、启用后注册（owner=qq-channel、schema 5 项）、无凭据启动返回 False 并给出
  "未配置：AppID、ClientSecret、Copree 群 ID"、关闭后回收；后端全量测试 **185 passed / 0 failed**

## 14. 为什么这是优雅的

- **收敛而非新增**：把「目录即插件」和「服务插件」合成一套，将来微信/飞书通道都只是 `plugins/` 下的一个目录
- **单一来源**：生命周期只有 `ServicePlugin` 一份实现；配置只有 `config.py` 一个入口；加解密只有 `crypto.py` 一份
- **零重复 UI**：运行态与启停沿用现成接口与卡片，只补了"配置"这一块
- **装好即可用**：与 skill 插件同款体验——丢一个目录进去、重扫、配置、启动
- **凭据不裸奔**：机密加密落库、接口永不回显、审计只记键不记值
