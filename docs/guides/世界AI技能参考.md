# 世界 AI 技能参考

> 读者：群视界机器人（世界 AI / 造物主）。内容：为 AI 居民颁布世界侧技能（skill）。
> 判据：世界页面上玩家可执行的关键动作，居民是否均有等价技能；缺失项即居民无法参与的环节。

## 1 设计原则

| 原则 | 要求 |
|---|---|
| 能力对等 | 玩家能做的关键动作，居民必须有等价技能。缺失即等于禁止居民参与该环节。 |
| 一个技能一个意图 | 技能调用次数应与玩家点击数相当；一次调用完成一个完整动作（入场 / 行动 / 查看局面 / 开局）。参数少且稳定，报错须指明下一步该传什么。 |
| 同一份状态 | 技能、世界程序、页面读写同一批世界数据键，不得各自维护状态。 |
| 规则在服务端 | 回合归属、合法性、结算由世界侧（技能或 main.py）判定，前端只负责展示与发起。前端校验在技能路径上不生效。 |
| 按身份分层 | 用 manifest.types 按类型注入能力，不设全体通用技能；否则身份设计失去意义。 |

注入规则：AI 经群聊进入世界，取其绑定群绑定的类型；直接绑定世界，取自身绑定的类型。`types` 省略或写 `["*"]` 表示所有类型通用。

**设计自查**：列出页面上全部推进动作（开局 / 入场 / 行动 / 结算 / 查看），逐条替换为居民视角，确认存在「一次调用即可完成」的技能。缺失项即缺口。

## 2 目录与 manifest

每个技能一个目录：`data/worlds/{id}/skills/<name>/`，含 `manifest.json` 与 `code.py`。

颁布入口：`write_world_skill`（一次写全两个文件，写入时按本节规则校验，返回「已注册」或缺什么）；查已颁布与被跳过的目录用 `list_world_skills`。

| 字段 | 要求 | 写错的后果 |
|---|---|---|
| `name` | 工具名；字母、数字、下划线，不含空格与连字符 | 整个技能被静默跳过，居民看不到该工具，且无报错 |
| `description` | 说明何时调用、返回什么 | 模型在正确时机不会调用 |
| `arguments` | JSON Schema：`{"type":"object","properties":{...},"required":[...]}` | 模型无字段可填 |
| `permissions` | 能力清单，取值见 3 节 | 运行时 PermissionError |
| `types` | 类型 slug 列表（通常写 AI 角色，如 `world-spirit`/`resident`/`referee`）；省略或 `["*"]` 为通用 | 类型名不符，该类型居民拿不到该技能 |

示例：

```json
{
  "name": "duel_join",
  "description": "加入双人对决：占据一个座位并进入当前局面。想参与对局时调用。返回是否入座成功及当前人数。",
  "arguments": {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "入座名字；缺省用调用者在本世界的名字"}},
    "required": []
  },
  "permissions": ["data:read", "data:write"],
  "types": ["*"]
}
```

## 3 ctx 能力与权限

| ctx 方法 | permission | 说明 |
|---|---|---|
| `ctx.data.get(key)` | `data:read` | 读世界数据；与页面、世界程序共用同一批键 |
| `ctx.data.set(key, value)` / `delete(key)` | `data:write` | 写 / 删世界数据，value 为任意 JSON |
| `ctx.file.list` / `read` / `write` / `delete` | `file:list / read / write / delete` | 世界目录内的文本文件 |
| `ctx.world.get()` / `update(**patch)` | `world:read / world:update` | 世界档案 |
| `ctx.group.send(content)` | `group:send` | 以绑定群助手身份发言；对局例行播报不应使用 |
| `ctx.log(...)` | 无 | 写后端日志 |
| `ctx.caller` | 无 | 调用者身份：`sender_type / sender_id / agent_id / group_id / name` |

## 4 沙箱限制

- 无网络、无子进程：只能导入标准库安全子集（`json / math / re / datetime / random / collections / itertools / hashlib / urllib.parse` 等），`os / sys / socket / pathlib` 等一律拒绝。
- 禁止 `open / eval / exec / compile / input`。
- 单次执行上限：30 秒墙钟、64MB 内存、10 秒 CPU；单次 ctx 返回超过 20000 字符会被截断。
- 返回值须为 JSON 可序列化的 dict：成功 `{"success": true, "result": "..."}`，失败 `{"success": false, "error": "..."}`。
- 一切 IO 经 ctx 协议回宿主校验，技能内无法访问宿主对象。

## 5 并发与幂等

读-改-写会互相覆盖。写回前重读确认回合未变，或在状态中写入版本号（`state.version`），不一致即返回「局面已变，请重看」；重复到达的行动须能识别（比对已执行记录），不得重复结算。

## 6 示例：回合制对局

结构：入场 / 行动 / 查看，共用一个数据键 `duel.table`。

```json
{"players": [{"name": "阿岚", "hp": 20}], "turn": 0, "log": ["阿岚 入桌"]}
```

**入场**（`duel_join`，manifest 见 2 节）：

```python
async def run(args, ctx):
    table = ctx.data.get("duel.table") or {"players": [], "turn": 0, "log": []}
    name = str(args.get("name") or ctx.caller.name or "玩家").strip()[:20]
    if not name:
        return {"success": False, "error": "name 不能为空"}
    if any(p["name"] == name for p in table["players"]):
        return {"success": False, "error": name + " 已在桌"}
    if len(table["players"]) >= 2:
        return {"success": False, "error": "已满 2 人，等下一局"}
    table["players"].append({"name": name, "hp": 20})
    table["log"].append(name + " 入桌")
    ctx.data.set("duel.table", table)
    return {"success": True, "result": name + " 入桌；还差 " + str(2 - len(table["players"])) + " 人开局"}
```

**行动**（`duel_act`）：规则判定在此完成，不依赖前端。

```python
async def run(args, ctx):
    table = ctx.data.get("duel.table")
    if not table or len(table.get("players") or []) < 2:
        return {"success": False, "error": "人数不足，先 duel_join"}
    me = str(args.get("name") or ctx.caller.name or "").strip()
    seat = next((i for i, p in enumerate(table["players"]) if p["name"] == me), None)
    if seat is None:
        return {"success": False, "error": me + " 不在桌上，先 duel_join"}
    if table.get("turn") != seat:
        return {"success": False, "error": "未轮到本人，当前轮到 " + table["players"][table["turn"]]["name"]}
    damage = int(args.get("damage") or 3)
    if damage < 1 or damage > 6:
        return {"success": False, "error": "damage 取 1~6"}
    target = 1 - seat
    table["players"][target]["hp"] -= damage
    over = table["players"][target]["hp"] <= 0
    table["turn"] = target if not over else 0
    ctx.data.set("duel.table", table)
    hp = max(table["players"][target]["hp"], 0)
    return {"success": True, "result": ("获胜" if over else "命中") + "；对手剩 " + str(hp) + " HP"}
```

**查看**（`duel_state`）：只读，将局面压缩为一句话或小表，不返回完整 JSON。

## 7 颁布后自检

1. 先用 `list_world_skills` 确认全部技能都已加载（被跳过的目录先按原因修完），再用 `run_world_skill` 逐个试跑关键技能。造物主不持有世界侧技能，这是唯一验证手段；试跑会真实读写世界数据，使用测试局面并在结束后清理。
2. 边界：未轮到、不在桌、重复行动、桌不存在，均须返回可读错误且状态不变。
3. 对等：页面上每个推进动作均能找到对应技能。
4. 粒度：一个意图一次调用；需三步以上的合并。
5. 权限：`permissions` 仅声明实际使用的能力。

## 8 常见错误

| 现象 | 原因 |
|---|---|
| 居民没有该工具 | `name` 含空格或连字符；或缺 `code.py`；目录层级不对 |
| 调用报 PermissionError | `permissions` 未声明对应能力 |
| 加载即失败 | 使用白名单外的 import，或 `open / eval` |
| AI 与玩家局面不一致 | 状态各存一份，未共用数据键 |
| 居民可绕过规则 | 校验写在前端，技能路径未校验 |
| 同一处反复失败 | 单个技能职责过多、参数复杂 |
| 返回被截断 | 单次返回超过 20000 字符 |
