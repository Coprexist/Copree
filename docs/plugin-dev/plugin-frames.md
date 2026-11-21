# 插件帧（plugin frames）

> 版本：v1
> 契约来源：`app/services/plugin/frames.py`，经 `app/services/plugin/api.py` 导出
> 帧本身（存储 / 运行链 / 执行窗口）：[帧、锁与重建点](../dev/frame_lifecycle.md)、[状态链](../dev/state_chain.md)

## 1. 它是什么

帧是「AI 此刻在哪个状态」的身份容器：账本按帧的 `context_ref` 取，提示词前缀、工具白名单、
环境快照都挂在帧上。**帧是 AI 的**，不是插件的私有容器。

这些帧 AI 自己就能管：`list_states` 看全量、`enter_state` / `pop_state` 进出、`close_state` 收掉、
`finish_frame` 销掉。插件多出来的那条路是**代建**：AI 在做插件这件事时（比如看一条视频），
插件替它把状态帧建好、把内容写进去，省掉 AI 自己拼参数。建出来的帧照常进 AI 的状态链，
AI 随时可以接管——`leave_frame` 只是插件替它收尾的一种方式，不是唯一出口。

帧的机制一条都不归插件管：链、容量、锁、账本仍然只由 `state_stack_service` 写。

此前只有平台工具与内部服务能建帧（`push_state` / `ensure_active_frame` / 世界帧），
插件想替 AI 建一帧只能去动平台内部。这份契约就是那条窄口。

## 2. 三条边界

1. **命名空间**：`context_ref` 必须以 `<plugin_id>:` 开头。`group:` / `dm:` / `world:` 是平台与 AI
   自己的帧（一段会话一帧），插件不代建也不改——插件只代建「自己这件事」的帧。
2. **先声明**：帧类型写进 `plugin.json` 的 `frames` 段。没声明过的类型建不出来（`FrameError`）。
3. **平台落地**：建、改、结都转交 `state_stack_service`，`_save` 仍是帧的唯一写入点。

## 3. manifest 声明

```json
{
  "id": "notes",
  "frames": {
    "notes_reading": {
      "title": "读笔记",
      "title_en": "Reading notes",
      "title_ja": "ノートを読む",
      "tools": ["notes_open", "notes_search", "pop_state"]
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| 键（`notes_reading`） | 帧类型名，`^[a-z][a-z0-9_]{0,39}$`；它同时是日志与前端状态列表里的标识 |
| `title` / `title_en` / `title_ja` | 显示名，缺省退回类型名 |
| `tools` | 建帧时写进 `frame["tools"]`；`frame_tool_gate` 按它隔离工具（世界帧走同一套） |
| `skills` | 可选，写进 `frame["skills"]`，同样参与隔离 |

类型名不合法、或声明不是对象的条目会被忽略并记一条 warning——manifest 是手写的，静默丢弃比报错更难查。

**白名单里放什么**：只放这一帧「这件事本身」用得上的工具。

- 跨场景的出口别塞：`send_gm` / `send_dm` 这类属于群聊/私信，帧是「现在只做这件事」的隔离，
  混进去等于让他边刷 B 站边想着群，既分心，额度也会记到另一段对话上；
- 但**「这一轮到此为止」的出口要给**（`end_turn`）：少了它，模型想停只能继续空转，或者被迫
  用 `finish_frame` 把帧收掉。2026-11-06 线上就发生过：B 站帧里没有 `end_turn`，它连试
  `send_gm` / `pop_state` / `end_turn` 全被拒，七轮全部打空；
- `pop_state` / `finish_frame` 这类「离开帧」的出口同理要留——进得来出不去，比不给工具更糟。

## 4. 接口

| 接口 | 作用 |
|---|---|
| `api.enter_frame(db, agent, *, context_ref, type="", label="", why="", doing="", todo="", plan="", switch=True)` | 进帧，返回帧记录（含 `id`） |
| `api.update_frame(db, agent, frame_id, **fields)` | 改文本字段：`label` / `why` / `doing` / `todo` / `plan` / `journal` |
| `api.leave_frame(db, agent, frame_id="")` | 结束帧（`status=ended`，记录保留）；`frame_id` 为空时关当前帧，且必须是自己那帧 |
| `api.list_frames(db, agent, type="")` | 列本插件建过的帧（含已结束），可只列某个类型 |
| `api.declared_frames(plugin_id)` | 读 manifest 声明（调试 / 自检用） |

事务口径与 `api.report_notice` 一致：**函数只写不提交**，调用方自己 `await db.commit()`。

`agent` 是模型对象（`app.models.agent.Agent`），不是 `agent_id`——与其它插件公共接口一致。

### 运行期身份

插件身份（`api.get_current_plugin()`）在三种时候有值：模块加载期、**工具执行时**、**服务生命周期**
（`start` / `stop` / `get_status` / `environment`）调用时。后两种由平台在调用点用
`api.plugin_context(...)` 包一层（模块级的全局变量在并发下会串号，所以实现里是 `ContextVar`）。

也就是说：插件的工具和服务里**直接**调 `api.enter_frame` 就够了，不需要自己传插件 id；
不在这些上下文里调用会得到 `FrameError`。

## 5. 语义

- **身份是 `context_ref`**：同一段插件会话永远只有一帧，帧 `id` 稳定。插件可以把它存进自己的表
  当长期指针（和闹钟记帧 id 同理）。
- **再次进入 = 复活**：哪怕这帧已经 `ended`，再 `enter_frame` 同一个 `context_ref` 会复活它并就地
  更新本次给的内容，不会压出第二条。
- **`switch=False`**：只建一条沉睡帧（进历史区），当前帧不动。预建 / 预热用。
- **白名单每次重盖**：`enter_frame` 每次都按声明重新写 `tools` / `skills`，声明改了立刻生效，
  不会冻住一份旧清单。
- **`leave_frame` ≠ 删除**：结束只是出状态链。删帧只有 AI 自己的 `finish_frame` 能触发
  （平台不替 AI 猜"这帧没用了"）。

## 5.1 帧预算（调用次数与额度）

帧主打"持续"，所以它的预算和普通对话分开算。**「一次」= 一次 LLM API 调用**；普通对话
一轮用什么由 AI 设置（`agents.max_tool_rounds`，默认 6）决定，插件帧用下面三个配置键：

| 配置键 | 含义 | 默认 |
|---|---|---|
| `frame_round_limit` | 一轮（一次唤醒）最多几次调用 | 50 |
| `frame_hour_limit` | 每小时最多几次调用（0 = 不限） | 0 |
| `frame_day_limit` | 每天最多几次调用（0 = 不限，按展示时区换天） | 0 |
| `frame_session_minutes` | **建议**单次待多久（0 = 不提）。只进那行预算，平台不按它掐断 | 视插件 |

- 次数按固定桶累计：小时整点换桶、天按展示时区换天。不用定时任务，重启/停机都不丢账
  （`app/utils/pure/frame_usage.py`）。
- **每轮都复述**：状态摘要里多一行 `预算: 本轮上限 50 次 · 这一小时 3/20 次 · 今天 8/100 次 · token 已用 42%`
  （`format_state_stack_summary` 的 `budget_line`，由 `frame_budget.describe` 算）。0 = 不限的档位不念，
  普通对话帧没有这一行。
- 到 80% 会往帧自己的账本投一条通知（`report_notice`），告诉 AI 还剩多少；用满就不再开始新的
  一轮（中途也会停，见 `executor` 的 `frame_budget_check`）。
- **token 额度**复用通道那一套（`services/channel/token_quota.py`）：插件在 `config_schema` 里
  声明 `quota_meter_mode` / `quota_limit_tokens` / `quota_reset_*`，进帧时把设置推给平台
  （`QuotaGate` 同款 `policy_from_config`）；帧的每一轮消耗记在 `context_ref` 上，卡片上的
  「对话额度」能看到用量并单独重置。
- 认「这是插件帧」要两件事同时成立：`context_ref` 前缀是插件 id，**且**帧类型在该插件的
  `frames` 段里声明过（`frame_budget.plugin_of`）。只看前缀会把 `group:` / `dm:` / `world:`
  也当成插件帧——那恰好是绝大多数轮次，认错等于把普通对话的轮次上限与通道记账一起改掉。
- 预算是平台按 `context_ref` 现读的实例配置（`agent-<id>`）：插件不需要把预算抄一份到平台表里。

## 5.2 进帧钩子（`on_frame_enter`）

帧被切进来之后，平台会调一次所属插件的 `on_frame_enter(db, agent, frame)`（可选）：

- 用途只有一个：把**插件那边**的新消息读进帧的账本（B 站插件的「收件箱」就是这么做的）。
- 是**注入**，不是唤醒：平台不因它开新轮；看不看、回不回由 AI 自己看上下文决定。
- 有超时（8 秒）与异常兜底：钩子失败/超时只记日志，进帧照常成功；游标只在成功时前进。
- 帧切进来看不出来是"谁切的"——钩子收到的是落定后的那一帧，别在里面改身份字段。

## 6. 不能做

- 建 `group:` / `dm:` / `world:` 帧，或改别的插件的帧 → `api.FrameError`；
- 改身份字段（`type` / `context_ref` / `status`）：`update_frame` 只认文本字段；要换类型先
  `leave_frame` 再 `enter_frame`；
- 在插件加载上下文之外调用：`api.set_current_plugin` 未设置时直接 `FrameError`。

## 7. 例子

```python
from app.database import async_session
from app.services.plugin import api

async with async_session() as db:
    frame = await api.enter_frame(
        db, agent,
        context_ref=f"notes:reading:{note_id}", type="notes_reading",
        label=f"读《{title}》", doing="读到第 3 章",
    )
    await db.commit()

# 之后（同一帧，id 不变）
async with async_session() as db:
    await api.update_frame(db, agent, frame["id"], doing="读到第 5 章")
    await db.commit()
```

## 8. 谁管理这些帧

| 谁 | 能做什么 |
|---|---|
| AI（自己的工具） | `list_states` 看（含插件建的帧）、`enter_state` 进去（把插件那帧拉到链尾、原来那帧挂着）、`pop_state` 出来/换帧、`close_state` 按 id 收掉、`finish_frame` 销掉 |
| 插件 | `enter_frame` 代建 / 复活、`update_frame` 代写文本、`leave_frame` 代收；看不到也碰不到别的插件的帧 |
| 平台 | 链、容量、锁、账本、环境快照；插件帧没有任何特殊待遇 |

所以「AI 在看视频」这帧如果真的存在，AI 是可以自己 `close_state` 结束它的——
插件负责的是「把它建对、把看到哪写进去」，不是「独占它」。

## 9. 与环境上报的区别

[环境上报](./environment-api.md) 报的是「这个会话现在是什么环境」，写进帧的 `env_locked` /
`env_notified`，**平台拿来判变化、投通知**——插件不持有它。插件帧是插件**自己造的身份**，
内容归插件。两者都落在帧上，但一个是平台字段，一个是插件的容器。
