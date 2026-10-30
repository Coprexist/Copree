"""色牌 · 抢灯 —— 积木引擎（data/world_blocks/color-cards/main.py）的规则测试

只测纯逻辑：发牌、跟牌、跳、＋2、变色、摸牌、抢灯、命令解析、视图。
handle() 那层的 IO（受控 API / publish / 叫醒 AI）留给集成测试，这里不碰网络。

用例签名一律零参数：后端的 run_without_pytest.py 只从 conftest 解析 fixture，
本文件的引擎在模块级加载，pytest 与本项目的最小运行器都能直接跑。
"""
import importlib.util
from pathlib import Path

def _block_path() -> Path:
    """引擎在仓库根的 data/world_blocks/color-cards/main.py。

    这个文件有三种跑法：仓库根（本地 run_without_pytest.py）、backend/（CI 里
    cd backend 之后跑 pytest）、容器里的 /app。按 cwd 拼相对路径只有第一种成立，
    CI 就是这么红的，所以改成从本文件位置逐级往上找——三种都成立。
    """
    here = Path(__file__).resolve()
    for root in here.parents:
        candidate = root / "data" / "world_blocks" / "color-cards" / "main.py"
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        f"找不到积木引擎：从 {here} 往上逐级都没找到 data/world_blocks/color-cards/main.py"
    )


BLOCK = _block_path()

_spec = importlib.util.spec_from_file_location("color_cards_main", BLOCK)
blk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(blk)

HUMAN = {"key": "human:1", "name": "甲", "kind": "human"}
AI = {"key": "ai:4", "name": "乙", "kind": "ai"}


def _table():
    """两个人坐下、已经发好牌的一张桌子"""
    t = blk.new_table(1)
    blk.join(t, HUMAN)
    blk.join(t, AI)
    assert t["phase"] == "playing"
    return t


def _staged(hand0, top="R7", color="R", hand1=None):
    """把桌子摆成指定残局：免得为了测规则去凑随机手牌"""
    t = _table()
    t["seats"][0]["hand"] = list(hand0)
    t["seats"][1]["hand"] = list(hand1 if hand1 is not None else ["B3", "B4", "B5", "B6", "B8", "B9", "G3"])
    t["pile"] = [top]
    t["color"] = color
    t["turn"] = 0
    return t


# ── 牌与牌型 ────────────────────────────────────────────────
def test_deck_has_four_colors_and_four_wilds():
    deck = blk.new_deck()
    assert len(deck) == 52
    assert deck.count(blk.WILD) == 4
    for color in blk.COLORS:
        assert len([c for c in deck if blk.card_color(c) == color]) == 12
    assert set(deck) == set(blk.new_deck())      # 同一副牌，只是顺序不同


def test_card_text_reads_like_a_person_would_say_it():
    assert blk.card_text("R5") == "红5"
    assert blk.card_text("YS") == "黄跳"
    assert blk.card_text("BD") == "蓝+2"
    assert blk.card_text("WW") == "★变色"


def test_match_is_same_color_or_same_rank():
    assert blk.can_play("R3", "R7", "R")
    assert blk.can_play("B7", "R7", "R")
    assert blk.can_play("WW", "R7", "G")
    assert not blk.can_play("B3", "R7", "R")


def test_legal_cards_only_lists_what_fits():
    assert blk.legal_cards(["R5", "B7", "B3", "WW"], "R7", "R") == ["R5", "B7", "WW"]


# ── 发牌与入座 ──────────────────────────────────────────────
def test_two_seats_deal_seven_each_and_never_open_with_wild():
    t = _table()
    assert t["round"] == 1
    assert [len(s["hand"]) for s in t["seats"]] == [7, 7]
    assert len(t["deck"]) == 52 - 15
    assert t["pile"][-1] != blk.WILD
    assert t["color"] == blk.card_color(t["pile"][-1])


def test_a_third_person_cannot_take_a_chair():
    t = _table()
    ok, say = blk.join(t, {"key": "human:9", "name": "丙", "kind": "human"})
    assert ok is False and "两把" in say


def test_one_seat_waits_for_the_other():
    t = blk.new_table(1, 1)
    ok, _ = blk.join(t, HUMAN)
    assert ok and t["phase"] == "waiting" and t["seats"][0]["hand"] == []
    ok, say = blk.new_round(t)
    assert ok is False and "差一个人" in say


# ── 出牌与功能牌 ────────────────────────────────────────────
def test_skip_keeps_the_turn():
    t = _staged(["RS", "B3"])
    ok, say = blk.play(t, HUMAN, "RS")
    assert ok and t["turn"] == 0 and t["seats"][0]["hand"] == ["B3"]
    assert "跳过" in say


def test_draw_two_makes_the_other_draw_and_keeps_the_turn():
    t = _staged(["RD", "B3"])
    ok, say = blk.play(t, HUMAN, "RD")
    assert ok and t["turn"] == 0
    assert len(t["seats"][1]["hand"]) == 9
    assert "摸 2 张" in say


def test_wild_needs_a_color_and_then_updates_the_table_color():
    t = _staged(["WW", "B3"])
    ok, say = blk.play(t, HUMAN, "WW")
    assert ok is False and "指定颜色" in say
    assert len(t["seats"][0]["hand"]) == 2        # 没出成，牌还在手里
    ok, _ = blk.play(t, HUMAN, "WW", "G")
    assert ok and t["color"] == "G" and t["pile"][-1] == blk.WILD


def test_a_card_that_does_not_match_is_refused():
    t = _staged(["B3", "R5"])
    ok, say = blk.play(t, HUMAN, "B3")
    assert ok is False and "不相干" in say
    assert len(t["seats"][0]["hand"]) == 2


def test_playing_out_of_turn_is_refused():
    t = _staged(["R5"])
    t["turn"] = 1
    ok, say = blk.play(t, HUMAN, "R5")
    assert ok is False and "回合" in say


def test_drawing_passes_the_turn():
    t = _staged(["B3", "B4"])
    ok, say = blk.draw(t, HUMAN)
    assert ok and t["turn"] == 1
    assert len(t["seats"][0]["hand"]) == 3
    assert "回合交给" in say


def test_empty_hand_wins_a_lantern():
    t = _staged(["R5"])
    ok, say = blk.play(t, HUMAN, "R5")
    assert ok and t["phase"] == "over" and t["winner"] == HUMAN["key"]
    assert t["seats"][0]["lanterns"] == 1
    assert "一盏灯" in say
    ok, _ = blk.play(t, HUMAN, "R5")
    assert ok is False                            # 局已经结束，再出牌无效


def test_forfeit_hands_the_lantern_to_the_other_seat():
    t = _staged(["R5", "R6"])
    ok, _ = blk.forfeit(t, HUMAN)
    assert ok and t["winner"] == AI["key"] and t["seats"][1]["lanterns"] == 1


def test_new_round_redeals_and_keeps_lanterns():
    t = _staged(["R5"])
    blk.play(t, HUMAN, "R5")
    ok, _ = blk.new_round(t)
    assert ok and t["round"] == 2 and t["phase"] == "playing"
    assert [len(s["hand"]) for s in t["seats"]] == [7, 7]
    assert t["seats"][0]["lanterns"] == 1


def test_draw_reshuffles_the_pile_when_the_deck_runs_out():
    t = _staged(["B3", "B4"])
    t["deck"] = []
    t["pile"] = ["R5", "B7"]
    card = blk._draw_one(t)
    assert card != "B7"                           # 顶牌留在台面
    assert t["pile"] == ["B7"]


# ── 命令解析 ────────────────────────────────────────────────
def test_commands_are_recognised():
    cases = {"坐下": "sit", "入座": "sit", "摸牌": "draw", "再来一局": "new",
             "认输": "quit", "看桌": "look", "规则": "rules", "坐。": "sit"}
    for text, action in cases.items():
        assert blk.parse_command(text) == (action, ()), text


def test_sit_can_name_a_table():
    assert blk.parse_command("坐下 2") == ("sit", ("2",))
    assert blk.parse_command("2 号桌") == (None, ())
    assert blk.parse_command("坐 3") == ("sit", ("3",))


def test_pick_table_fills_the_waiting_half_table_first():
    tables = {1: blk.new_table(1, 1), 2: blk.new_table(1, 2)}
    blk.join(tables[2], HUMAN)
    assert blk.pick_table(tables, None, AI) == 2
    assert blk.pick_table(tables, "1", AI) == 1          # 指名优先


def test_a_player_only_sits_at_one_table():
    tables = {1: blk.new_table(1, 1), 2: blk.new_table(1, 2)}
    ok, _ = blk._sit(tables, 1, HUMAN)
    assert ok
    ok, say = blk._sit(tables, 2, HUMAN)
    assert ok is False and "已经在 1 号桌" in say


def test_chatter_is_not_a_command():
    assert blk.parse_command("今天天气不错") == (None, ())
    assert blk.parse_command("") == (None, ())


def test_play_command_keeps_the_card_spec():
    assert blk.parse_command("出 红5") == ("play", ("红5",))
    assert blk.parse_command("出 变色 绿") == ("play", ("变色 绿",))


def test_card_spec_resolves_against_the_hand():
    hand = ["R5", "B7", "WW"]
    assert blk.resolve_play(hand, "红5") == ("R5", None)
    assert blk.resolve_play(hand, "5") == ("R5", None)
    assert blk.resolve_play(hand, "R5") == ("R5", None)
    assert blk.resolve_play(hand, "变色 绿") == ("WW", "G")
    code, err = blk.resolve_play(hand, "变色")
    assert code is None and "指定颜色" in err
    code, err = blk.resolve_play(hand, "红9")
    assert code is None and "红9" in err
    code, err = blk.resolve_play(["R5", "R5"], "红5")
    assert code is None and "不止一张" in err


# ── 视图 ────────────────────────────────────────────────────
def test_public_view_never_shows_hands():
    t = _staged(["R5", "B3"])
    view = blk.public_view(t)
    assert [s["count"] for s in view["seats"]] == [2, 7]
    assert all("hand" not in s for s in view["seats"])
    assert view["top"]["text"] == "红7"
    assert view["deck"] == len(t["deck"])


def test_own_view_lists_only_the_cards_that_can_be_played_now():
    t = _staged(["R5", "B3", "WW"])
    me = blk.own_view(t, HUMAN["key"])
    assert me["i"] == 0 and me["turn"] is True
    assert me["legal"] == ["R5", "WW"]
    assert [c["text"] for c in me["hand"]] == ["红5", "蓝3", "★变色"]
    assert blk.own_view(t, "human:404") is None


def test_turn_say_hands_the_ai_ready_made_commands():
    t = _staged(["R5", "B3", "WW"])
    say = blk.turn_say(t, HUMAN["key"])
    assert "轮到你" in say and "出 红5" in say and "出 变色" in say
    t["turn"] = 1
    assert "等 乙 出牌" in blk.turn_say(t, HUMAN["key"])
