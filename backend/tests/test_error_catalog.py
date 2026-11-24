"""错误文案目录的契约守卫（app/ai/error_catalog.py）。

为什么单独测：文案是唯一来源，任何调用点都不能现拼（C5）。这张表错了，
用户看到的就是错的——而它不会抛异常，只会默默显示一段错话。
"""
from __future__ import annotations

from app.ai.error_catalog import CATALOG, render, spec_for


def test_unknown_code_falls_back_to_all_failed():
    """未知 code 不许抛：发通知的路径自己炸掉比不发更糟。"""
    text = render("this_code_does_not_exist", agent_name="小明", agent_id=7)
    assert "小明" in text
    assert spec_for("nope").code == "all_failed"


def test_empty_detail_does_not_raise():
    text = render("all_failed", agent_name="小明", agent_id=7, detail="")
    assert "小明" in text


def test_api_errors_keep_their_solution_links():
    """原有四种文案是产品化过的（带解决链接），搬家时不许丢。"""
    for code in ("no_api_key", "insufficient_balance", "auth_error"):
        text = render(code, agent_name="小明", agent_id=7)
        assert "/agents/7" in text or "/settings" in text, code
        assert "解决方法" in text, code


def test_outward_failure_names_the_tool_and_reason():
    text = render("tool_outward_failed", agent_name="小明", agent_id=7,
                  detail="你不是该群成员", tool="send_gm")
    assert "send_gm" in text
    assert "你不是该群成员" in text
    assert "没有发出去" in text


def test_outward_spec_is_marked_outward():
    assert spec_for("tool_outward_failed").outward is True
    assert spec_for("no_api_key").outward is False


def test_tool_timeout_spec_is_retryable():
    assert spec_for("tool_timeout").retryable is True


def test_every_spec_code_matches_its_key():
    """表里的 key 与 spec.code 必须一致：日志/计数器/前端共用这个键。"""
    for key, spec in CATALOG.items():
        assert spec.code == key
