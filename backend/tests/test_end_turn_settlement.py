"""轮末结算（end_turn）：思考留不留 + 留给后面自己的关键信息

设计见 docs/dev/conversation_history.md §5：参数都可省，省了按"不保留"处理；
兜底永不空（正文本身就是"干了什么"）。
"""
import pytest

pytestmark = pytest.mark.anyio


async def _call(arguments: dict) -> dict:
    from app.tools.self_management.end_turn import EndTurn

    return await EndTurn().execute(object(), 1, None, arguments, {})


async def test_end_turn_defaults_to_not_keeping_thinking():
    """省参数 = 不保留（省 token），且不是错误"""
    r = await _call({})

    assert r["end_turn"] is True and r["success"] is True
    assert r["keep_thinking"] is False and r["key_note"] == ""


async def test_end_turn_carries_the_settlement_decision():
    """显式保留 + 关键信息一起带回来（执行器据此封存账本条目）"""
    r = await _call({"keep_thinking": True, "key_note": "  改的是 world_chat_service 的阈值  "})

    assert r["keep_thinking"] is True
    assert r["key_note"] == "改的是 world_chat_service 的阈值", "去掉首尾空白，落库的是最终字节"
    assert "思考保留" in r["message"]
