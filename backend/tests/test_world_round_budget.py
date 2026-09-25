"""世界对话的**双预算**契约（用户 2026-09-23）。

用户要求：读的预算单独一本账（搜文件不该吃改动额度）；要提示 AI 读的预算；
上限我定；不够时 AI 可以向用户申请增加；并且要告诉它还剩多少。

判定复用 world_ai_mode.action_of（SAFE_TOOLS = 无副作用）——只读名单全仓只有一份。
"""
import inspect

import pytest

from app.services.world import world_chat_service as wcs


def test_read_budget_is_twice_the_write_budget_with_a_ceiling():
    assert wcs.resolve_read_budget({"max_tool_rounds": 50}) == 100
    assert wcs.resolve_read_budget({"max_tool_rounds": 200}) == wcs.READ_ROUND_CEILING
    assert wcs.resolve_read_budget({}) == wcs.DEFAULT_MAX_TOOL_ROUNDS * wcs.READ_ROUND_FACTOR
    assert wcs.resolve_read_budget({"max_tool_rounds": "abc"}) == 100


def test_current_budgets_lets_the_turn_state_override_win():
    """提额写在 turn_state 上 → 每轮读它，用户批准当轮生效（旧写法循环外算一次，白批）。"""
    assert wcs.current_budgets({}, {"max_tool_rounds": 50}) == (50, 100)
    assert wcs.current_budgets({"round_budget": 120}, {"max_tool_rounds": 50}) == (120, 100)
    assert wcs.current_budgets({"read_round_budget": 300}, {"max_tool_rounds": 50}) == (50, 300)
    # 两项都封顶
    assert wcs.current_budgets({"round_budget": 999, "read_round_budget": 9999}, {}) == (
        wcs.ROUND_BUDGET_CEILING, wcs.READ_ROUND_CEILING,
    )


def test_granted_read_budget_adds_and_caps():
    assert wcs.granted_read_budget(100, 50) == 150
    assert wcs.granted_read_budget(100, 0) == 101        # 申请量至少算 1，别让"批准了却没变"
    assert wcs.granted_read_budget(wcs.READ_ROUND_CEILING, 100) == wcs.READ_ROUND_CEILING


def test_round_is_a_read_round_only_when_nothing_writes():
    assert wcs.is_write_round(["file_read", "file_grep"]) is False
    assert wcs.is_write_round(["view_api_doc", "request_read_budget", "present_plan", "ask_user"]) is False
    assert wcs.is_write_round(["file_read", "file_write"]) is True    # 夹了一个改动类 → 算改动轮
    assert wcs.is_write_round(["file_edit"]) is True
    assert wcs.is_write_round([]) is False


def test_budget_error_only_fires_for_the_exhausted_account():
    state = {"write_rounds": 3, "read_rounds": 7}
    assert wcs.round_budget_error("file_read", state, 5, 10) is None
    assert wcs.round_budget_error("file_write", state, 5, 10) is None
    assert "改动预算已用尽" in wcs.round_budget_error("file_write", {"write_rounds": 5}, 5, 10)
    read_err = wcs.round_budget_error("file_grep", {"read_rounds": 10}, 5, 10)
    assert "只读预算已用尽" in read_err and "request_read_budget" in read_err
    # 只读见底不影响改动；改动见底不影响只读（两本账互不牵连）
    assert wcs.round_budget_error("file_read", {"write_rounds": 99, "read_rounds": 0}, 5, 10) is None
    assert wcs.round_budget_error("file_write", {"write_rounds": 0, "read_rounds": 99}, 5, 10) is None


def test_budget_prompt_tells_the_ai_both_accounts():
    text = wcs.build_budget_prompt(50, 100)
    assert "改动预算 50 轮" in text and "读取预算 100 轮" in text
    assert "request_read_budget" in text and "不占改动预算" in text


def test_tool_loop_recomputes_budgets_every_round():
    """回归守卫：预算不能在循环外算一次 —— 那样用户批准提额当轮不生效（旧 bug）。"""
    src = inspect.getsource(wcs._run_tool_loop)
    assert "for _r in range(max_rounds)" not in src
    assert src.count("current_budgets(turn_state, cfg)") >= 2
    assert '"round_budgets"' in src          # 当前预算下发，供 request_read_budget 读
