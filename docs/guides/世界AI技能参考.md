# 世界 AI 技能参考：让 AI 居民真的能玩

> 适用对象：群视界机器人（世界 AI / 造物主）。
> 只解决一件事：**世界里玩家能玩的，AI 居民也能玩**——靠的是技能怎么写。
> 玩法举不完，所以这里不给清单，给原则和一份骨架；判断自己的设计对不对，用原则，不用对号入座。

## 0 三句结论

1. 页面决定「看起来是什么」，技能决定「居民能不能参与」。页面上玩家点得到的关键动作，居民没有对应技能，就等于被挡在门外。
2. 验收只有一条：把玩家的操作路径逐条换成居民视角，每一步是否都有一次调用就能完成的技能。缺哪一步，居民到那一步就只能旁观。
3. 状态只有一份、规则只在世界侧。技能与页面读写同一批世界数据；回合、合法性、胜负由世界侧判定。

## 1 五条原则

### 1.1 能力对等

技能不是「给 AI 一点参与感」的装饰，是它在这个世界的双手。玩家按按钮能做到的事，居民必须能通过技能做到；否则世界越像游戏，AI 越像在旁边看人打游戏。

自查问法：这个页面上一共有哪些「推进一局 / 推进一轮」的动作？逐个问一遍——居民能不能做？

### 1.2 一个技能 = 一个完整意图

居民调用技能的次数，应当等于玩家的点击数，而不是数倍。一次调用要把「这一步」办完：落座、出手、开新局、看清局面。要连调三四次才能推进一回合的技能，模型会中途放弃、参数会传歪，表现出来就是「AI 好像不太会玩」。

参数少而稳；报错要说清下一步该传什么。

### 1.3 同一份状态

技能、世界程序、页面必须读写同一份世界数据（受控数据 API 的同一批 key）。技能写进去，页面刷新立刻能看见；玩家在页面上的操作，技能读出来就是那个结果。

各存一份状态 = 两个世界：居民在自己的状态里出牌，玩家在自己的界面里看不见，两边都以为对方没动。

### 1.4 规则在服务端

回合归属、行动是否合法、扣血结算、胜负判定，全部放在世界侧（技能或 main.py）判定，前端只负责展示与发起。

原因不是洁癖：技能可以被直接调用，前端校验在技能这条路径上根本不存在。校验放前端，等于让居民自己给自己发牌，也等于让玩家改一个变量就赢。

### 1.5 按身份分层

不同角色、职业、身份该有不同的能力。用 manifest 的 types 声明可用类型，分层注入；不要给全体居民一份万能技能——万能技能会让每种身份都只会同一套动作，世界里的身份就白设计了。

注入规则：AI 从群聊进来，落到该群绑定的类型；直接绑定世界，落到自己绑定的类型。省略 types 或写 ["*"] = 所有类型通用。

## 2 协议

### 2.1 位置与骨架

每个技能一个目录：```data/worlds/{id}/skills/<name>/```，里面两个文件——

- ```manifest.json```：名字、描述、参数、权限、适用类型
- ```code.py```：```async def run(args, ctx)```，执行体

### 2.2 manifest.json

| 字段 | 作用 | 写坏的后果 |
|---|---|---|
| ```name``` | 工具名，字母/数字/下划线，不含空格与连字符 | 不合法的整个 skill 被静默跳过，居民只是「看不见这个工具」，没有任何报错 |
| ```description``` | 写给模型看的：什么时候该调、返回什么 | 写含糊，模型在正确时机想不起来调 |
| ```arguments``` | JSON Schema：```{"type":"object","properties":{...},"required":[...]}``` | 参数名就是模型要填的字段，写错它填不出来 |
| ```permissions``` | 能力清单（见 2.3） | 没声明就调，运行时直接 PermissionError |
| ```types``` | 适用类型 slug 列表；省略或 ```["*"]``` = 通用 | 写错类型名，该类型的居民就拿不到这个技能 |

### 2.3 ctx 能做什么

| ctx 方法 | 需要的 permission | 说明 |
|---|---|---|
| ```ctx.data.get(key)``` | ```data:read``` | 读世界数据；与页面、世界程序同一批 key |
| ```ctx.data.set(key, value)``` | ```data:write``` | 写世界数据（value 任意 JSON） |
| ```ctx.data.delete(key)``` | ```data:write``` | 删除一个 key |
| ```ctx.file.list(prefix)``` / ```read``` / ```write``` / ```delete``` | ```file:list / read / write / delete``` | 世界文件夹内的文本文件 |
| ```ctx.world.get()``` / ```update(**patch)``` | ```world:read / world:update``` | 世界档案 |
| ```ctx.group.send(content)``` | ```group:send``` | 以绑定群助手身份发一条群消息（对局里的例行播报不要用它） |
| ```ctx.log(...)``` | 无 | 写后端日志（排查用） |
| ```ctx.caller``` | 无 | 调用者身份：```sender_type / sender_id / agent_id / group_id / name``` |

### 2.4 沙箱边界（写代码前必须知道）

- 无网络、无子进程、无系统调用；只能 import 标准库安全子集（```json / math / re / datetime / random / collections / itertools / hashlib / urllib.parse``` 等），```import os / sys / socket / pathlib``` 之类直接拒绝
- 禁止 ```open / eval / exec / compile / input```
- 单次执行：30 秒墙钟、64MB 内存、10 秒 CPU；单次 ctx 返回数据超过 20000 字符会被截断
- 返回值必须是 JSON 可序列化的 dict：成功 ```{"success": true, "result": "给模型看的一句话"}```，失败 ```{"success": false, "error": "..."}```
- 技能里拿不到宿主对象，一切 IO 都经 ctx 协议回到宿主做校验

### 2.5 并发与幂等

两个居民同时出手时，读-改-写会互相覆盖。写回前再读一次确认回合没变，或把版本号放进状态里（```state.version```），对不上就报「局面已变，请重看」；同一个行动重复到达要能识别（比对已执行记录），重复出牌比报错更难查。

## 3 一份骨架示例

玩法是两人对决，重点在结构：**入桌 / 出手 / 观局**三件套。换成任何玩法，形状不变——换掉状态结构与结算规则即可。

### 3.1 状态约定

世界数据 key：```duel.table```，结构：

```json
{
  "players": [{"name": "阿岚", "hp": 20}],
  "turn": 0,
  "log": ["阿岚 入桌"]
}
```

页面、main.py、技能都读这一个 key；页面只是把它渲染出来。

### 3.2 manifest × 3

入桌（```duel_join```）：

```json
{
  "name": "duel_join",
  "description": "加入双人对决：占一个座位并出现在当前局面里。想玩、被叫上桌时调用。返回是否坐下成功与当前人数。",
  "arguments": {
    "type": "object",
    "properties": {
      "name": {"type": "string", "description": "入桌名字（可选，缺省用你在本世界的名字）"}
    },
    "required": []
  },
  "permissions": ["data:read", "data:write"],
  "types": ["*"]
}
```

出手（```duel_act```）与观局（```duel_state```）同理，只是参数与权限不同：出手要 ```data:read + data:write```（参数 ```name```、```damage``` 1~6），观局只要 ```data:read```。

### 3.3 code.py

入桌：

```python
async def run(args, ctx):
    table = ctx.data.get("duel.table") or {"players": [], "turn": 0, "log": []}
    name = str(args.get("name") or ctx.caller.name or "玩家").strip()[:20]
    if not name:
        return {"success": False, "error": "name 不能为空"}
    if any(p["name"] == name for p in table["players"]):
        return {"success": False, "error": name + " 已经在这桌了"}
    if len(table["players"]) >= 2:
        return {"success": False, "error": "这桌已满（2 人），等下一局"}
    table["players"].append({"name": name, "hp": 20})
    table["log"].append(name + " 入桌")
    ctx.data.set("duel.table", table)
    left = 2 - len(table["players"])
    return {"success": True, "result": name + " 入桌；还差 " + str(left) + " 人可以开局"}
```

出手（规则判定全在这里，不靠前端）：

```python
async def run(args, ctx):
    table = ctx.data.get("duel.table")
    if not table or len(table.get("players") or []) < 2:
        return {"success": False, "error": "这桌还没凑齐 2 人，先 duel_join"}
    me = str(args.get("name") or ctx.caller.name or "").strip()
    seat = next((i for i, p in enumerate(table["players"]) if p["name"] == me), None)
    if seat is None:
        return {"success": False, "error": me + " 不在桌上；先 duel_join"}
    if table.get("turn") != seat:
        return {"success": False, "error": "还没轮到你，当前轮到 " + table["players"][table["turn"]]["name"]}
    damage = int(args.get("damage") or 3)
    if damage < 1 or damage > 6:
        return {"success": False, "error": "damage 取 1~6"}
    target = 1 - seat
    table["players"][target]["hp"] -= damage
    over = table["players"][target]["hp"] <= 0
    table["turn"] = target if not over else 0
    table["log"].append(me + " 造成 " + str(damage) + " 点伤害" + ("，对手倒下" if over else ""))
    ctx.data.set("duel.table", table)
    hp = table["players"][target]["hp"]
    return {"success": True, "result": ("获胜" if over else "命中") + "；对手剩 " + str(max(hp, 0)) + " HP"}
```

观局：读完把状态压成一句话或一张小表，别把整份 JSON 塞回给模型。

## 4 颁布后自检

- 用 ```run_world_skill``` 逐个试跑关键技能：造物主自己不持有世界侧技能，这是唯一的验证手段；试跑会真的读写世界数据，用测试局面跑、跑完清掉
- 边界：不是自己回合 / 不在桌上 / 重复出手 / 桌不存在 → 报错可读，且状态没被改动
- 对等：把页面上的推进动作逐条换成居民视角，每条都能找到技能（找不到就是缺口）
- 调用次数：一个意图一次调用；要调三步以上的，合并
- 权限最小化：```permissions``` 只声明真正用到的能力

## 5 常见坑

| 症状 | 原因 |
|---|---|
| 居民完全没有这个工具 | manifest 的 ```name``` 含空格/连字符，或目录里缺 ```code.py```，被静默跳过 |
| 调用时报 PermissionError | ```permissions``` 没声明对应能力 |
| 技能加载即失败 | 用了白名单外的 import，或 ```open / eval``` |
| AI 与玩家看到两个局面 | 各存了一份状态，没走同一批数据 key |
| 居民能作弊 | 校验写在前端，技能路径绕过了它 |
| AI 老是在同一个地方失败 | 一个技能干了太多事，参数太复杂 |
| 技能返回被截断 | 一次返回了整份大对象（超过 20000 字符） |
