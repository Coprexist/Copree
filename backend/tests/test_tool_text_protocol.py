"""文本协议：渲染确定性、解析不猜、结果投影成正文。

这段文字每轮进 system 前缀，所以"同样的工具集必得同样的字节"和解析正确性一样重要。
"""
from app.utils.pure.tool_text_protocol import (
    parse_tool_calls, project_for_text_tools, render_call_blocks, render_tool_protocol,
)


def _tool(name, description, properties=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": required or []},
        },
    }


SEND_DM = _tool(
    "send_dm", "私信某个成员",
    {"target_id": {"type": "integer", "description": "收信人 id"},
     "content": {"type": "string", "description": "消息正文"}},
    ["target_id", "content"],
)
END_TURN = _tool("end_turn", "本轮结束")


def test_render_is_byte_stable_regardless_of_registry_order():
    """注册顺序会随插件加载变化，渲染结果不能跟着抖。"""
    forward = render_tool_protocol([SEND_DM, END_TURN])
    backward = render_tool_protocol([END_TURN, SEND_DM])
    assert forward == backward
    assert forward == render_tool_protocol([SEND_DM, END_TURN])


def test_render_carries_format_but_not_the_tool_list():
    """工具清单在【技能目录】里，这里只补"调用怎么表达"——重复一遍就是两处来源。"""
    text = render_tool_protocol([SEND_DM])
    assert '<tool_call>{"name": "工具名"' in text
    assert "<tool_response>" in text
    assert "技能目录" in text
    assert "send_dm" not in text


def test_render_without_tools_is_empty():
    """没有工具就不该多一段空协议——空串让调用方原样不发。"""
    assert render_tool_protocol(None) == ""
    assert render_tool_protocol([]) == ""


def test_parse_reads_calls_and_strips_tags():
    text = '好的，我这就发。\n<tool_call>{"name":"send_dm","arguments":{"target_id":163,"content":"你好"}}</tool_call>'
    calls, rest = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "send_dm"
    assert '"target_id": 163' in calls[0]["function"]["arguments"]
    assert calls[0]["type"] == "function"
    assert rest == "好的，我这就发。"


def test_parse_ignores_malformed_and_nameless_blocks():
    """坏块不猜，也不吞：宁可这一轮按"只说了话"处理，也不能替它编参数发出去。"""
    calls, rest = parse_tool_calls(
        '<tool_call>{"name":"send_dm","arguments":{</tool_call>'
        '<tool_call>{"arguments":{"a":1}}</tool_call>'
    )
    assert calls == []
    assert "arguments" in rest, "解析失败的块留在正文里给账本，不能被静默吞掉"


def test_parse_accepts_markdown_fence():
    """7B 蒸馏模型更习惯把调用写成代码块——多认一种承载，比让它复述系统提示有用。"""
    text = '```json\n{"name":"send_dm","arguments":{"target_id":1,"content":"你好"}}\n```'
    calls, rest = parse_tool_calls(text)
    assert [c["function"]["name"] for c in calls] == ["send_dm"]
    assert rest == ""


def test_parse_accepts_bare_json_object():
    calls, rest = parse_tool_calls('{"name":"end_turn","arguments":{}}')
    assert [c["function"]["name"] for c in calls] == ["end_turn"]
    assert rest == ""


def test_parse_leaves_unresolvable_json_in_place():
    """没有 name 的 JSON 不是调用：正文一个字都不能吞。"""
    text = '{"foo": "bar"}'
    calls, rest = parse_tool_calls(text)
    assert calls == []
    assert rest == text


def test_parse_multiple_calls_keeps_order():
    calls, _ = parse_tool_calls(
        '<tool_call>{"name":"a","arguments":{}}</tool_call>'
        '<tool_call>{"name":"b","arguments":{}}</tool_call>'
    )
    assert [c["function"]["name"] for c in calls] == ["a", "b"]
    assert [c["id"] for c in calls] == ["call_txt_0", "call_txt_1"]


def test_render_call_blocks_roundtrips_through_parse():
    original = [{"id": "x", "type": "function",
                 "function": {"name": "send_dm",
                              "arguments": '{"target_id": 163, "content": "你好"}'}}]
    calls, rest = parse_tool_calls(render_call_blocks(original))
    assert rest == ""
    assert calls[0]["function"]["name"] == "send_dm"
    assert '"content": "你好"' in calls[0]["function"]["arguments"]


def test_project_turns_tool_results_into_text():
    """r1 的模板丢弃 role:"tool"，结果必须落到正文里才看得见。"""
    calls = [{"id": "call_txt_0", "type": "function",
              "function": {"name": "send_dm", "arguments": "{}"}}]
    projected = project_for_text_tools([
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": None, "tool_calls": calls},
        {"role": "tool", "tool_call_id": "call_txt_0", "content": '{"ok": true}'},
    ], "PROTOCOL")

    assert projected[0] == {"role": "system", "content": "PROTOCOL"}
    assistant = projected[2]
    assert "tool_calls" not in assistant
    assert "<tool_call>" in assistant["content"]
    tool_message = projected[3]
    assert tool_message["role"] == "user"
    assert tool_message["content"].startswith("<tool_response>")


def test_project_keeps_plain_messages_untouched():
    """普通对话不该被改写：没有工具消息时投影只多一段协议。"""
    messages = [{"role": "user", "content": "嗨"}, {"role": "assistant", "content": "在"}]
    projected = project_for_text_tools(messages, "")
    assert projected == messages
