"""会话命名（world_ai_mode 之外的小工具函数）契约守卫。

对话名是用户在多会话列表里唯一的导航线索：AI 起过名字就显示名字，没有才回落编号。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.world.world_chat_service import (
    SESSION_TITLE_MAX,
    normalize_session_title,
    set_session_title,
)


def _world(sid: str | None = None, sessions: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=1, config={"current_session": sid, "sessions": sessions or {}})


def test_normalize_trims_and_caps():
    assert normalize_session_title("  造卡牌对战界面  ") == "造卡牌对战界面"
    assert normalize_session_title("修地缝\n掉落\t问题") == "修地缝 掉落 问题"
    assert normalize_session_title("  ") is None
    assert normalize_session_title(None) is None
    assert len(normalize_session_title("名" * 50)) == SESSION_TITLE_MAX


def test_set_title_writes_current_session_only():
    w = _world("w1:m:aaa", {"w1:m:aaa": {"created_at": "t"}, "w1:m:bbb": {}})
    assert set_session_title(w, "造卡牌对战界面") == "造卡牌对战界面"
    assert w.config["sessions"]["w1:m:aaa"]["title"] == "造卡牌对战界面"
    assert w.config["sessions"]["w1:m:aaa"]["created_at"] == "t"      # 其它元信息不被抹掉
    assert "title" not in w.config["sessions"]["w1:m:bbb"]           # 别的会话不受影响


def test_set_title_falls_back_to_default_session():
    w = _world(None)
    assert set_session_title(w, "默认会话的名字") == "默认会话的名字"
    assert w.config["sessions"]["default"]["title"] == "默认会话的名字"


def test_set_title_can_target_any_session_in_the_list():
    """前端会话列表要能改"列表里的任意一场"，不只是当前会话"""
    w = _world("w1:m:aaa", {"w1:m:aaa": {}, "w1:m:bbb": {}})
    assert set_session_title(w, "旧的界面实验", "w1:m:bbb") == "旧的界面实验"
    assert w.config["sessions"]["w1:m:bbb"]["title"] == "旧的界面实验"
    assert "title" not in w.config["sessions"]["w1:m:aaa"]
    assert set_session_title(w, "   ", "w1:m:bbb") is None           # 清空 = 回落编号
    assert "title" not in w.config["sessions"]["w1:m:bbb"]
    assert set_session_title(w, "默认会话", "default") == "默认会话"  # default 落到默认会话条目
    assert w.config["sessions"]["default"]["title"] == "默认会话"


def test_empty_title_clears_it():
    w = _world("w1:m:aaa", {"w1:m:aaa": {"title": "旧名字"}})
    assert set_session_title(w, "   ") is None
    assert "title" not in w.config["sessions"]["w1:m:aaa"]
