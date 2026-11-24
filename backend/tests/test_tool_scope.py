"""工具集范围与「轻聊档」预设：档位只是组合，两个开关都是 AI 设置项。

2026-11-24 用户要求：不提供很多工具 + 模型返回的文字直接发，做成 **AI 档位**；
同时「所有有差异的配置都要落到设置项，档位只是自由组合」。于是：

  tool_scope         all / minimal / none   —— 一次给模型多少个工具
  plain_text_action  silent / send          —— 只回文字时发不发

轻聊档（chat）只是这两个设置项 + 其它参数的一组默认值，用户可单独覆盖。
"""


def test_tool_scope_all_returns_everything():
    from app.services.tool_registry import get_allowed_tools

    tools = get_allowed_tools('active', thinking_enabled=False, delay_reply_allowed=True,
                              tool_scope="all")
    assert len(tools) > 50


def test_tool_scope_minimal_keeps_only_reply_tools():
    from app.services.tool_registry import get_allowed_tools
    from app.tools.base import MINIMAL_TOOL_NAMES

    tools = get_allowed_tools('active', thinking_enabled=False, delay_reply_allowed=True,
                              tool_scope="minimal")
    names = {t["function"]["name"] for t in tools}
    assert names and names <= MINIMAL_TOOL_NAMES
    assert "send_gm" in names and "send_dm" in names
    assert "send_gm" in MINIMAL_TOOL_NAMES


def test_tool_scope_none_gives_nothing():
    from app.services.tool_registry import get_allowed_tools

    assert get_allowed_tools('active', tool_scope='none') == []


def test_minimal_tools_are_much_smaller_than_all():
    """这不是审美问题：77 个 schema 实测 5.2 万字符 ≈ 1.75 万 token。"""
    import json
    from app.services.tool_registry import get_allowed_tools

    def size(scope):
        return len(json.dumps(get_allowed_tools('active', tool_scope=scope), ensure_ascii=False))

    assert size("minimal") * 5 < size("all")


def test_chat_profile_is_the_light_one():
    """轻聊档 = 少工具 + 文字直发；档位只是这两个设置项的预设。"""
    from app.services.agent.agent_service import CONFIG_PROFILES

    chat = CONFIG_PROFILES["chat"]
    assert chat["tool_scope"] == "minimal"
    assert chat["plain_text_action"] == "send"
    # 其它档位不指定 → 取列默认（all / silent），差异全部落在设置项上
    assert "tool_scope" not in CONFIG_PROFILES["immersive"]
    assert "plain_text_action" not in CONFIG_PROFILES["digital_life"]