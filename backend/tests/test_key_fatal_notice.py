"""Key 不可用的说法：401 与 402 是两件事。

线上事故（2026-10-28）：owner 的全局 Key 里存的是个占位符（8 位、不是 Key 的样子），
接口回 401。系统却按"余额不足"通知他，还发了两条一模一样的——用户去查了余额，
钱明明还在。这里钉住"说什么"和"说几条"。
"""
import pytest

pytestmark = pytest.mark.anyio


def test_401_is_a_broken_key_not_a_short_balance():
    from app.ai.executor import _key_fatal_reason

    assert _key_fatal_reason(401) == "API Key 无效（401）"
    assert _key_fatal_reason(402) == "余额不足（402）"
    assert _key_fatal_reason(500) == "Key 不可用（500）"


def test_notice_names_which_key_tailed_four():
    """同一账号下常存着好几把 Key：通知要点名是哪一把，否则用户只能挨个试"""
    from app.ai.executor import _key_tail_suffix

    assert _key_tail_suffix("sk-0123456789abcdef0123456789abcdef1234") == "（末尾 1234）"
    # 太短的不点名：八个字符的"Key"本身就说明填错了，再露四位等于把垃圾也亮出来
    assert _key_tail_suffix("Aa123456") == ""
    assert _key_tail_suffix(None) == ""

def test_notice_says_what_to_do_per_scenario():
    """四种场景四句话：别再出现"用户看提示去做的事其实解决不了问题"这种"""
    from app.ai.executor import key_fatal_notice

    long_key = "sk-" + "a" * 32 + "1234"

    # 自己的 Key 坏了且没有下一级：说清是哪把 Key、什么问题，并指向个人设置
    mine = key_fatal_notice(agent_name="甲", source="user_key", status_code=401,
                            api_key=long_key, has_fallback=False, tier_name="你的 API")
    assert "API Key 无效（401）" in mine and "末尾 1234" in mine and "无可用系统额度" in mine
    assert "/settings" in mine

    # 同样的路，402 才说余额
    broke = key_fatal_notice(agent_name="甲", source="user_key", status_code=402,
                             api_key=long_key, has_fallback=False, tier_name="你的 API")
    assert "余额不足（402）" in broke

    # 还有下一级：只告知"正在切换"，不要给用户派活
    switching = key_fatal_notice(agent_name="甲", source="user_key", status_code=401,
                                 api_key=long_key, has_fallback=True, tier_name="你的 API")
    assert "正在自动切换" in switching and "/settings" not in switching

    # 系统额度坏了：先说明这轮不计入用户的额度
    pool = key_fatal_notice(agent_name="甲", source="pool_key", status_code=402,
                            api_key=long_key, has_fallback=False, tier_name="系统额度")
    assert "系统额度暂不可用" in pool and "不记入你的额度使用情况" in pool

    # AI 自带的 Key：指 AI 设置页那边，别把用户往个人设置绕
    own = key_fatal_notice(agent_name="化学老师", source="agent_key", status_code=401,
                           api_key=long_key, has_fallback=False, tier_name="AI 自有")
    assert "化学老师" in own and "请检查 API 配置" in own
