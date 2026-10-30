"""色牌 · 抢灯 —— 世界程序入口（handle）

一桌两把交椅：先把手里七张牌打光的人提灯走。台面红色就跟红、跟点、跟符号；
没得打就摸一张，回合交给对面。

三个动作面共用这一个解析器，所以人和 AI 看到的是同一张桌子：
  · 页面：POST /world/{id}/api/event  —— {action: sit|play|draw|new|look, code, color}
  · 群 AI：world_command 发「坐下 / 出 红5 / 摸牌 / 再来一局」
  · 群消息：同样是自由文本，锚定匹配才认（聊天里出现「红5」不会误触）

桌面状态按群存世界数据库 bj 之外的一把钥匙：color.table.<group_id>；
每次动作都 publish 一份公开视图（页面 SSE 直接收），并写 <key>.view 给只读技能。

环境变量（沙箱自动注入）：WORLD_API_BASE / WORLD_API_TOKEN / WORLD_ID
"""
from __future__ import annotations

import json
import os
import random
import re
import secrets
import time
import urllib.parse
import urllib.request

# ── 桌规（改这里就是改玩法）──────────────────────────────────
HAND_SIZE = 7
COLORS = ("R", "Y", "B", "G")
COLOR_NAMES = {"R": "红", "Y": "黄", "B": "蓝", "G": "绿"}
COLOR_FROM_NAME = {v: k for k, v in COLOR_NAMES.items()}
RANKS = [str(i) for i in range(10)] + ["S", "D"]     # S=跳一轮  D=对手摸二
WILD = "WW"
LANTERN_GOAL = 3          # 连灯目标（只用于文案，不强制结束）
LOG_KEEP = 12
MAX_TABLES = 4            # 一个群里能开几张桌子（大厅列表就是这几张）

# ── 受控 API（世界代码 → 平台）────────────────────────────────
def _base() -> str:
    return os.environ.get("WORLD_API_BASE", "")


def _token() -> str:
    return os.environ.get("WORLD_API_TOKEN", "")


def _world_id() -> str:
    return os.environ.get("WORLD_ID", "")


def api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(
        _base() + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get_data(key: str):
    try:
        return api("/data/" + urllib.parse.quote(key, safe="")).get("value")
    except Exception:
        return None


def put_data(key: str, value) -> None:
    try:
        api("/data/" + urllib.parse.quote(key, safe=""), "PUT", {"value": value})
    except Exception:
        pass


def publish(state: dict) -> None:
    try:
        api("/state", "POST", state)
    except Exception:
        pass


def _first_group() -> int:
    """AI 的命令不带群号：落到本世界绑定的第一个群（一世界多群 = 多桌）

    /groups 里每条是 {group_id, name, ...}——没有 id 这个键，缺了它整张桌子都找不到落脚处。
    """
    try:
        groups = api("/groups").get("groups") or []
    except Exception:
        return 0
    if not groups:
        return 0
    first = groups[0]
    return int(first.get("group_id") or first.get("id") or 0)


# ── 牌与牌型 ────────────────────────────────────────────────
def new_deck() -> list[str]:
    deck = [c + r for c in COLORS for r in RANKS] + [WILD] * 4
    random.shuffle(deck)
    return deck


def card_color(code: str) -> str:
    return "" if code == WILD else code[0]


def card_rank(code: str) -> str:
    return "" if code == WILD else code[1:]


def card_text(code: str) -> str:
    """人话写法：红5 / 蓝跳 / 绿+2 / ★变色（AI 也照着这个回）"""
    if code == WILD:
        return "★变色"
    name = COLOR_NAMES.get(code[0], code[0])
    rank = code[1:]
    if rank == "S":
        return name + "跳"
    if rank == "D":
        return name + "+2"
    return name + rank


def can_play(code: str, top: str, color: str) -> bool:
    if code == WILD:
        return True
    return card_color(code) == color or card_rank(code) == card_rank(top)


def legal_cards(hand: list[str], top: str, color: str) -> list[str]:
    return [c for c in hand if can_play(c, top, color)]


def _draw_one(table: dict) -> str:
    """从牌堆顶抽一张；牌堆见底就把台面除顶牌洗回去"""
    if not table["deck"]:
        top = table["pile"][-1]
        table["deck"] = table["pile"][:-1]
        random.shuffle(table["deck"])
        table["pile"] = [top]
        _log(table, "摸牌堆见底，把台面洗了回去")
    return table["deck"].pop()


def _log(table: dict, text: str) -> None:
    table.setdefault("log", []).append({"at": int(time.time()), "text": text})
    del table["log"][:-LOG_KEEP]


# ── 桌面 ────────────────────────────────────────────────────
def new_table(gid: int, tid: int = 1) -> dict:
    return {"group_id": gid, "tid": tid, "round": 0, "seats": [], "turn": 0, "phase": "waiting",
            "deck": [], "pile": [], "color": "", "winner": None,
            "seq": 0, "signaled": -1, "log": []}


def seat_of(table: dict, key: str) -> int | None:
    for i, s in enumerate(table["seats"]):
        if s["key"] == key:
            return i
    return None


def _open_round(table: dict) -> None:
    """洗牌、发牌、翻台面：一条路，开局和再来一局都走它"""
    table["deck"] = new_deck()
    table["pile"] = []
    table["winner"] = None
    table["round"] += 1
    for i, seat in enumerate(table["seats"]):
        seat["hand"] = [_draw_one(table) for _ in range(HAND_SIZE)]
    top = _draw_one(table)
    while top == WILD:                      # 开局第一张不是变色牌，免得一上来就要报色
        table["deck"].insert(0, top)
        top = _draw_one(table)
    table["pile"] = [top]
    table["color"] = card_color(top)
    table["phase"] = "playing" if len(table["seats"]) == 2 else "waiting"
    table["turn"] = 0
    _log(table, f"第 {table['round']} 局开局，台面 {card_text(top)}")


def seat_by_key(table: dict, key: str) -> dict | None:
    i = seat_of(table, key)
    return table["seats"][i] if i is not None else None


def join(table: dict, who: dict) -> tuple[bool, str]:
    """入座：两张椅子，先来先坐；坐满就只旁观"""
    if seat_by_key(table, who["key"]):
        return True, f"你已经在座（{who['name']}）。"
    if len(table["seats"]) >= 2:
        return False, "这把椅子已经有人了——两把都满了，你可以先看着。"
    table["seats"].append({"key": who["key"], "name": who["name"], "kind": who["kind"],
                           "hand": [], "lanterns": 0})
    _log(table, f"{who['name']} 入座")
    for seat in table["seats"]:
        seat.setdefault("lanterns", 0)
    if len(table["seats"]) == 2:
        _open_round(table)
        return True, f"{who['name']} 入座，人齐了。第 {table['round']} 局发牌：台面 {card_text(table['pile'][-1])}。"
    return True, f"{who['name']} 入座。还差一个人开牌——另一半坐下就发牌。"


def _turn_seat(table: dict) -> dict | None:
    if table["phase"] != "playing" or not table["seats"]:
        return None
    return table["seats"][table["turn"] % len(table["seats"])]


def _end_round(table: dict, winner_key: str) -> None:
    table["phase"] = "over"
    table["winner"] = winner_key
    seat = seat_by_key(table, winner_key)
    if seat:
        seat["lanterns"] = int(seat.get("lanterns", 0)) + 1
        _log(table, f"{seat['name']} 抢到一盏灯（共 {seat['lanterns']} 盏）")


def play(table: dict, who: dict, code: str, color: str | None = None) -> tuple[bool, str]:
    idx = seat_of(table, who["key"])
    if idx is None:
        return False, "你还没坐下。先发「坐下」。"
    if table["phase"] != "playing":
        return False, "现在没有在打的一局。发「再来一局」重新发牌。"
    if idx != table["turn"]:
        other = table["seats"][table["turn"] % len(table["seats"])]
        return False, f"现在是 {other['name']} 的回合，不是你的。"
    seat = table["seats"][idx]
    if code not in seat["hand"]:
        return False, f"你手里没有这张牌：{card_text(code)}。"
    top = table["pile"][-1]
    if not can_play(code, top, table["color"]):
        return False, f"{card_text(code)} 跟台面 {card_text(top)}（当前色 {COLOR_NAMES[table['color']]}）不相干。"
    if code == WILD and color not in COLORS:
        return False, "变色牌要指定颜色：出 变色 红（黄/蓝/绿同理）。"

    seat["hand"].remove(code)
    table["pile"].append(code)
    table["color"] = color if code == WILD else card_color(code)
    _log(table, f"{seat['name']} 出 {card_text(code)}" + (f"，改色为 {COLOR_NAMES[table['color']]}" if code == WILD else ""))

    rank = card_rank(code)
    if not seat["hand"]:
        _end_round(table, seat["key"])
        return True, f"你出完了最后一张（{card_text(code)}）——抢到一盏灯！共 {seat['lanterns']} 盏。"

    other_idx = 1 - idx
    other = table["seats"][other_idx]
    extra = ""
    if rank == "D":                          # +2：对面摸两张，回合回到自己
        got = [_draw_one(table) for _ in range(2)]
        other["hand"].extend(got)
        _log(table, f"{other['name']} 摸了两张（+2）")
        extra = f"{other['name']} 摸 2 张，还是你的回合。"
        table["turn"] = idx
    elif rank == "S":                        # 跳：对面跳过，回合回到自己
        extra = f"{other['name']} 被跳过，还是你的回合。"
        table["turn"] = idx
    else:
        table["turn"] = other_idx
        extra = f"轮到 {other['name']}。"
    if not seat["hand"]:
        _end_round(table, seat["key"])
        extra += "你出完了最后一张——抢到一盏灯！"
    return True, f"你出 {card_text(code)}，{extra}"


def draw(table: dict, who: dict) -> tuple[bool, str]:
    idx = seat_of(table, who["key"])
    if idx is None:
        return False, "你还没坐下。先发「坐下」。"
    if table["phase"] != "playing":
        return False, "现在没有在打的一局。发「再来一局」重新发牌。"
    if idx != table["turn"]:
        other = table["seats"][table["turn"] % len(table["seats"])]
        return False, f"现在是 {other['name']} 的回合，不是你的。"
    seat = table["seats"][idx]
    card = _draw_one(table)
    seat["hand"].append(card)
    _log(table, f"{seat['name']} 摸了一张")
    table["turn"] = 1 - idx
    other = table["seats"][table["turn"]]
    return True, f"你摸到 {card_text(card)}。回合交给 {other['name']}。"


def new_round(table: dict) -> tuple[bool, str]:
    if len(table["seats"]) < 2:
        return False, "还差一个人，凑齐两把椅子才发牌。"
    _open_round(table)
    return True, f"第 {table['round']} 局发牌，台面 {card_text(table['pile'][-1])}。"


def forfeit(table: dict, who: dict) -> tuple[bool, str]:
    idx = seat_of(table, who["key"])
    if idx is None or table["phase"] != "playing":
        return False, "现在没有你可以认输的一局。"
    other = table["seats"][1 - idx]
    _end_round(table, other["key"])
    return True, f"你认输了，{other['name']} 拿走这一盏灯。"


# ── 视图与人话 ──────────────────────────────────────────────
def card_view(code: str, hidden: bool = False) -> dict:
    if hidden:
        return {"code": "", "text": "", "color": "", "rank": "", "hidden": True}
    return {"code": code, "text": card_text(code), "color": card_color(code),
            "rank": card_rank(code), "hidden": False}


def public_view(table: dict) -> dict:
    """页面 SSE 用的公开视图：看不到别人的手牌，只看张数"""
    top = table["pile"][-1] if table["pile"] else ""
    seats = [{"i": i, "name": s["name"], "kind": s["kind"], "count": len(s["hand"]),
              "lanterns": int(s.get("lanterns", 0)), "key": s["key"]}
             for i, s in enumerate(table["seats"])]
    return {
        "round": table["round"], "phase": table["phase"], "turn": table["turn"],
        "color": table["color"], "color_name": COLOR_NAMES.get(table["color"], ""),
        "top": card_view(top) if top else None,
        "deck": len(table["deck"]), "pile": len(table["pile"]),
        "seats": seats, "winner": table["winner"],
        "log": table["log"][-6:], "seq": table["seq"],
        "goal": LANTERN_GOAL,
    }


def own_view(table: dict, key: str) -> dict | None:
    """自己那把椅子：手牌 + 这手能打哪几张（合不合法只由引擎判，页面照着点亮）"""
    seat = seat_by_key(table, key)
    if seat is None:
        return None
    idx = seat_of(table, key)
    top = table["pile"][-1] if table["pile"] else ""
    legal = legal_cards(seat["hand"], top, table["color"]) if (top and table["phase"] == "playing") else []
    return {"i": idx, "name": seat["name"], "kind": seat["kind"], "key": seat["key"],
            "hand": [card_view(c) for c in seat["hand"]],
            "legal": legal, "turn": idx == table["turn"] and table["phase"] == "playing",
            "lanterns": int(seat.get("lanterns", 0)), "count": len(seat["hand"])}


def turn_say(table: dict, key: str) -> str:
    """轮到某人时给他的一段话：能出的牌直接写成可复制的命令，AI 照着发就行"""
    seat = seat_by_key(table, key)
    if seat is None:
        return "你还没坐下：发「坐下」上桌。"
    if table["phase"] == "waiting":
        return "还差一个人开牌——等等对手，或者叫上谁坐上另一把椅子。"
    if table["phase"] == "over":
        winner = seat_by_key(table, table["winner"] or "")
        return (f"这一局 {winner['name'] if winner else '有人'} 赢了。发「再来一局」重新发牌。"
                if winner else "这一局结束了。发「再来一局」重新发牌。")
    idx = seat_of(table, key)
    top = table["pile"][-1]
    hand = " ".join(card_text(c) for c in seat["hand"]) or "（空）"
    plays = legal_cards(seat["hand"], top, table["color"])
    if plays:
        cmds = []
        for c in plays[:6]:
            if c == WILD:
                cmds.append("出 变色 " + _wild_hint(seat["hand"]))
            else:
                cmds.append("出 " + card_text(c))
        opts = " ／ ".join(dict.fromkeys(cmds))
    else:
        opts = "摸牌"
    if idx != table["turn"]:
        other = table["seats"][table["turn"] % len(table["seats"])]
        return (f"现在等 {other['name']} 出牌（台面 {card_text(top)}，当前色 "
                f"{COLOR_NAMES[table['color']]}）。你的牌：{hand}")
    return (f"第 {table['round']} 局，轮到你。\n"
            f"台面：{card_text(top)}（当前色 {COLOR_NAMES[table['color']]}）\n"
            f"你的牌：{hand}\n"
            f"能出：{opts}\n"
            f"没有能出的牌就发「摸牌」。用 world_command 发出去。")


def _wild_hint(hand: list[str]) -> str:
    """变色牌给个建议色：手上最多的那门（没有就红）"""
    counts = {c: 0 for c in COLORS}
    for code in hand:
        if code != WILD and code[0] in counts:
            counts[code[0]] += 1
    return COLOR_NAMES[max(counts, key=lambda c: counts[c])]


# ── 命令解析（人话 → 动作）──────────────────────────────────
_SIT = re.compile(r"^(?:坐下|入座|上桌|坐|sit)(?:\s*(\d+)\s*号?(?:桌)?)?$")
_DRAW = re.compile(r"^(?:摸牌|摸一张|抽牌|要一张|draw|pass)$")
_NEW = re.compile(r"^(?:再来一局|再来|重开|新一局|开局|new|restart)$")
_QUIT = re.compile(r"^(?:认输|投降|不玩了|forfeit)$")
_LOOK = re.compile(r"^(?:看桌|看牌|看看|状态|桌况|我的牌|table|state)$")
_RULES = re.compile(r"^(?:规则|玩法|怎么玩|帮助|help|rules)$")
_PLAY = re.compile(r"^(?:出|打|下|放)\s*(.+)$")

RULES_TEXT = (
    "色牌 · 抢灯：两把椅子，一人七张牌，先打光的人抢到一盏灯。\n"
    "· 跟着台面出：同色，或同数字/同符号（红5、蓝跳、绿+2）。\n"
    "· 跳＝对手跳过一轮；+2＝对手摸两张再跳过；★变色＝任意时刻能出，出时指定颜色。\n"
    "· 没牌可跟就摸一张，回合交给对面（摸完这张不补打）。\n"
    "· 先出完手牌的人拿走一盏灯；三盏灯是这一晚的目标。发「再来一局」重新发牌。\n"
    "· 命令：坐下 / 出 红5 / 出 变色 绿 / 摸牌 / 看桌 / 再来一局"
)


def parse_command(text: str) -> tuple[str | None, tuple]:
    """一句话 → (动作, 参数)。认不出来返回 (None, ())——调用方只需判空"""
    t = (text or "").strip().strip("。！？!?，,~").strip()
    if not t:
        return None, ()
    m = _SIT.match(t)
    if m:
        return "sit", ((m.group(1),) if m.group(1) else ())
    if _DRAW.match(t):
        return "draw", ()
    if _NEW.match(t):
        return "new", ()
    if _QUIT.match(t):
        return "quit", ()
    if _LOOK.match(t):
        return "look", ()
    if _RULES.match(t):
        return "rules", ()
    m = _PLAY.match(t)
    if m:
        return "play", (m.group(1).strip(),)
    return None, ()


def resolve_play(hand: list[str], spec: str) -> tuple[str | None, str | None]:
    """「红5」/「5」/「变色 绿」/「R5」→ (牌, 指定色)；说不清就 (None, 原因)"""
    spec = (spec or "").strip().replace(" ", "").replace("，", "")
    color = None
    wild_asked = bool(re.search(r"变色|万能|wild|★", spec))
    for name, letter in COLOR_FROM_NAME.items():
        if name in spec or (letter.lower() in spec.lower() and not wild_asked):
            color = letter
            break
    if wild_asked:
        if color is None:
            return None, "出变色牌要指定颜色：出 变色 红（黄/蓝/绿同理）。"
        if WILD not in hand:
            return None, "你手里没有变色牌。"
        return WILD, color

    rank = None
    if re.search(r"跳|跳过|⊘", spec):
        rank = "S"
    elif re.search(r"\+2|加二|加2|摸二", spec):
        rank = "D"
    else:
        m = re.search(r"\d+", spec)
        if m:
            rank = m.group(0)
    if rank is None:
        return None, "没看懂要出哪张——写成「出 红5」这样。"

    candidates = [c for c in hand if card_rank(c) == rank and (color is None or card_color(c) == color)]
    if not candidates:
        shown = card_text(color + rank) if color else (COLOR_NAMES.get(color or "R", "") and rank)
        return None, f"你手里没有 {card_text(color + rank) if color else rank}。"
    if len(candidates) > 1:
        opts = " ／ ".join("出 " + card_text(c) for c in candidates)
        return None, f"有不止一张能这么写：{opts}"
    return candidates[0], None


# ── 一次动作 = 桌面 + 视图 + 是否需要叫醒对面的 AI ────────────
def apply_action(table: dict, who: dict, action: str, arg=None) -> tuple[bool, str]:
    if action == "sit":
        return join(table, who)
    if action == "play":
        code, color = arg if isinstance(arg, tuple) else (arg, None)
        return play(table, who, code, color)
    if action == "draw":
        return draw(table, who)
    if action == "new":
        return new_round(table)
    if action == "quit":
        return forfeit(table, who)
    if action == "rules":
        return True, RULES_TEXT
    if action == "look":
        return True, turn_say(table, who["key"])
    return False, "这句没看懂。能发：坐下 / 出 红5 / 摸牌 / 看桌 / 再来一局"


def _emit_event(name: str, title: str, payload: dict, gid: int) -> None:
    """世界事件 → 平台按群找 AI → 叫醒本体（叫不醒也不该让这一步失败：牌桌照常开）"""
    try:
        api("/ai_event", "POST", {"name": name, "title": title,
                                  "payload": payload, "targets": {"group_id": gid}})
    except Exception:
        pass


def _turn_payload(table: dict, seat: dict, gid: int) -> dict:
    """轮到某人时给它的全部信息：台面、手牌、以及一段直接能抄的命令"""
    return {
        "world_id": _world_id(), "table": table.get("tid", 1), "group": gid, "seat": seat["name"],
        "top": card_text(table["pile"][-1]), "color": COLOR_NAMES.get(table["color"], ""),
        "hand": [card_text(c) for c in seat["hand"]],
        "say": turn_say(table, seat["key"]),
        "hint": f"轮到你出牌。没进世界就先 enter_world（world_id={_world_id()}），"
                f"再用 world_command 发「出 红5」这样的命令；把上面 say 里给你的命令挑一条发出去。",
    }


def _wake_ai(table: dict, gid: int) -> None:
    """轮到 AI 时叫它：世界事件 → 平台筛选 → 叫醒本体，它自己用 world_command 出牌。

    一次动作只叫一次（signaled 记 seq）：AI 连出时（跳/+2）也不会连发事件。
    """
    seat = _turn_seat(table)
    if seat is None or seat["kind"] != "ai" or gid <= 0:
        return
    if table.get("signaled") == table["seq"]:
        return
    table["signaled"] = table["seq"]
    _emit_event("your_turn", f"色牌：轮到 {seat['name']} 出牌",
                _turn_payload(table, seat, gid), gid)


def _nudge(table: dict, who: dict, action: str, gid: int) -> tuple[bool, str]:
    """页面上那两个"喊人"的按钮：空椅子叫 AI 上桌 / 轮次卡住时催它一下

    poke 不自己发事件，只把 signaled 清掉，让 _wake_ai 走那条唯一的路——
    叫醒逻辑只该有一份，否则"催"和"轮到我"迟早会说出两套话。
    """
    if gid <= 0:
        return False, "这个牌桌还没绑定群聊。"
    if action == "invite":
        if len(table["seats"]) >= 2:
            return False, "两把椅子都有人了。"
        _emit_event("table_open", f"色牌：{who['name']} 等你上桌", {
            "world_id": _world_id(), "table": table.get("tid", 1), "group": gid,
            "host": who["name"],
            "say": f"{who['name']} 已经在牌桌上等你：先 enter_world（world_id={_world_id()}），"
                   f"再用 world_command 发「坐下」。两把椅子坐满就自动发牌。",
            "hint": "坐满两人立刻发牌，一人七张。",
        }, gid)
        return True, "喊了一声——等对面坐下（1/2）。"
    seat = _turn_seat(table)
    if seat is None or seat["kind"] != "ai":
        return False, "现在没有在等谁。"
    table["signaled"] = -1
    return True, f"催了 {seat['name']} 一下，等它出牌。"


def _tables_key(gid: int) -> str:
    return f"color.tables.{gid}"


def load_tables(gid: int) -> dict:
    """一个群里的所有桌子：{桌号: 桌子}

    旧版本只有一张桌子、存在 color.table.<群号> 里；这里顺手把它当成 1 号桌搬过来，
    免得升级之后原来那局莫名其妙消失。
    """
    raw = get_data(_tables_key(gid)) or {}
    if not raw:
        legacy = get_data(f"color.table.{gid}")
        if legacy:
            raw = {"1": legacy}
    out = {}
    for tid in range(1, MAX_TABLES + 1):
        table = raw.get(str(tid)) if isinstance(raw, dict) else None
        if not isinstance(table, dict) or "seats" not in table:
            table = new_table(gid, tid)
        table["group_id"] = gid
        table["tid"] = tid
        out[tid] = table
    return out


def _store(tables: dict, gid: int) -> None:
    put_data(_tables_key(gid), {str(tid): t for tid, t in tables.items()})


def seat_table(tables: dict, key: str) -> tuple[int, dict] | None:
    """这人（或这个 AI）现在坐哪张桌子——一个人同一时间只坐一张"""
    for tid, table in tables.items():
        if seat_by_key(table, key):
            return tid, table
    return None


def _open_table(tables: dict) -> int:
    """挑一张该坐的桌子：先补"已经有人等着的"半桌，再找空桌，全满就回 1 号

    先补半桌是有意的：人在 3 号桌坐下喊了一声，AI 的「坐下」就该落在 3 号桌，
    而不是飘去没人的 1 号桌——否则每张桌子都要人肉报桌号。
    """
    for want in (1, 0):
        for tid, table in tables.items():
            if len(table["seats"]) == want:
                return tid
    return 1


def pick_table(tables: dict, hint, who: dict) -> int:
    """页面可以指名桌号；群里的命令认"他坐的那张桌子"；都没有就找空桌"""
    try:
        tid = int(hint)
    except (TypeError, ValueError):
        tid = 0
    if tid in tables:
        return tid
    seated = seat_table(tables, who["key"])
    if seated:
        return seated[0]
    return _open_table(tables)


def lobby_view(tables: dict, gid: int, key: str | None = None) -> dict:
    """大厅：几张桌子、谁坐着、各有几盏灯——页面第一屏就是它"""
    mine = seat_table(tables, key) if key else None
    rows = []
    for tid, table in sorted(tables.items()):
        rows.append({
            "tid": tid, "phase": table["phase"], "round": table["round"],
            "seats": [{"name": s["name"], "kind": s["kind"],
                       "lanterns": int(s.get("lanterns", 0)), "count": len(s["hand"])}
                      for s in table["seats"]],
            "open": max(0, 2 - len(table["seats"])),
        })
    return {"tables": rows, "goal": LANTERN_GOAL, "max_tables": MAX_TABLES,
            "me_table": mine[0] if mine else None, "group_id": gid}


def _sit(tables: dict, tid: int, who: dict) -> tuple[bool, str]:
    """入座：一人一张桌子，坐满两人自动发牌"""
    elsewhere = seat_table(tables, who["key"])
    if elsewhere and elsewhere[0] != tid:
        return False, f"你已经在 {elsewhere[0]} 号桌坐着了——一次只坐一张，先在那儿打完。"
    return join(tables[tid], who)


# ── 入口 ────────────────────────────────────────────────────
def _who(sender_type: str, sender_id, sender_name: str) -> dict:
    kind = "ai" if sender_type == "ai" else "human"
    return {"key": f"{kind}:{sender_id}", "name": sender_name or f"#{sender_id}", "kind": kind}


_PURE_ACTIONS = ("look", "rules", "lobby")   # 不改牌桌的动作不该惊动对手


def handle(event: dict) -> dict:
    """平台入口：页面命令 / 群消息 / AI 命令都落到这里

    一个群 = 一间牌室，里面 MAX_TABLES 张桌子：页面在大厅选桌，
    群里的命令自动认"他坐的那张桌子"，所以 AI 出牌不需要额外报桌号。
    """
    event = event or {}
    etype = event.get("type")
    gid = int(event.get("group_id") or 0) or _first_group()
    if gid <= 0:
        return {"ok": False, "say": "这个世界还没绑定群聊，牌桌没有落脚的地方。"}
    tables = load_tables(gid)

    results: list[tuple[bool, str]] = []
    done: list[str] = []
    who_for_view = None
    tid = None

    if etype == "page_command":
        payload = dict(event.get("payload") or {})
        who = _who("human", payload.get("user_id"), payload.get("user_name") or "")
        who_for_view = who
        action = str(payload.get("action") or "look").strip()
        tid = pick_table(tables, payload.get("table"), who)
        table = tables[tid]
        done.append(action)
        if action == "lobby":
            seat = seat_table(tables, who["key"])
            results.append((True, f"你在 {seat[0]} 号桌。" if seat else "挑一张桌子坐下。"))
        elif action == "play":
            code = str(payload.get("code") or "").strip()
            results.append((False, "缺少要出的牌。") if not code
                           else play(table, who, code, payload.get("color") or None))
        elif action == "sit":
            results.append(_sit(tables, tid, who))
        elif action in ("invite", "poke"):
            results.append(_nudge(table, who, action, gid))
        else:
            results.append(apply_action(table, who, action))
    elif etype == "group_message":
        for msg in event.get("messages") or []:
            text = (msg.get("content") or "").strip()
            action, arg = parse_command(text)
            if not action:
                continue
            who = _who(msg.get("sender_type") or "human", msg.get("sender_id"), msg.get("sender_name") or "")
            # 「坐下 3」可以指名桌子；不指名就补那张有人等着的半桌
            tid = pick_table(tables, arg[0] if (action == "sit" and arg) else None, who)
            table = tables[tid]
            done.append(action)
            if action == "sit":
                results.append(_sit(tables, tid, who))
            elif action == "play":
                hand = (seat_by_key(table, who["key"]) or {}).get("hand") or []
                code, err = resolve_play(hand, arg[0] if arg else "")
                if code is None:
                    results.append((False, err or "没看懂"))
                else:
                    results.append(play(table, who, code, err if code == WILD else None))
            else:
                results.append(apply_action(table, who, action))
            who_for_view = who_for_view or who
    else:
        return {"ok": True, "say": "这个事件不归牌桌管。"}

    table = tables.get(tid) if tid in tables else None
    mutated = any(a not in _PURE_ACTIONS for a in done)
    if mutated and table is not None:
        table["seq"] = int(table.get("seq", 0)) + 1
        _wake_ai(table, gid)          # 先叫醒再落盘：signaled 一起存下去，同一回合不重复叫
        _store(tables, gid)
        publish({"lobby": lobby_view(tables, gid, (who_for_view or {}).get("key")),
                 "table": tid, "state": public_view(table)})

    ok, say = results[-1] if results else (True, "没什么变化。")
    if etype != "page_command":
        say = "\n".join(s for _o, s in results) if results else say

    out = {"ok": ok, "say": say, "table": tid,
           "lobby": lobby_view(tables, gid, (who_for_view or {}).get("key"))}
    if table is not None:
        out.update({"turn": table["turn"], "phase": table["phase"], "view": public_view(table)})
    if who_for_view is not None and table is not None:
        out["me"] = own_view(table, who_for_view["key"])
    return out
