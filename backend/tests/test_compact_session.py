"""会话压缩的契约守卫（2026-09-16 用户反馈：20+ 轮工具调用却说"无需压缩"）。

两个病根各要能验证：
1. 取样口径错——按"最后 200 行"取，再过滤 tool/note，真实对话只剩几条（实测某会话真实 30~68 条、
   这个口径只数得到 3~10 条），于是永远"没超出保留窗口"；
2. 现在：可压内容 = **保留窗口之外**的 user/ai 消息；窗口内原样保留、旧摘要并入不丢历史。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.memory import context_compression_service as ccs
from app.services.world import world_chat_compact as wcc
from app.services.world import world_chat_service as wcs


class _Result:
    """假结果集：只会被用来查 WorldAI（查不到 → None）"""

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class _Repo:
    """假 repo：只提供 flush / session / execute（取样已被替换，execute 只服务 WorldAI 那一次查询）"""

    def __init__(self):
        self.session = None
        self.flushed = 0

    async def flush(self):
        self.flushed += 1

    async def execute(self, *a, **k):
        return _Result()


def _world(session: str | None = "w1:m:abc", config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=1, config={"current_session": session, **(config or {})})


def _rows(n: int) -> list[dict]:
    """替身必须和 collect_real_messages 的契约一致：真实采样**带 DB id**。

    少了 id，compact 写 chat_summary_bounds 时会 KeyError——那是替身失真，不是产品 bug。
    """
    return [
        {"id": i + 1, "role": "user" if i % 2 == 0 else "ai", "content": f"第{i}条"}
        for i in range(n)
    ]


def test_window_plan_counts_only_what_the_model_carries():
    assert wcc.window_plan(13, 10) == {"real_messages": 13, "keep_last": 10, "compressible": 3}
    assert wcc.window_plan(6, 10)["compressible"] == 0          # 全在窗口里 = 没东西可压
    assert wcc.window_plan(0, 10)["compressible"] == 0


def test_build_input_keeps_previous_summary_and_skips_the_window():
    rows = _rows(13)                                            # 13 条，保留最近 10 条
    payload = wcc.build_compact_input(rows, "更早的事都在这里", keep_last=10)
    assert payload[0]["role"] == "system"
    assert payload[1]["content"].startswith("【此前的上下文摘要】")   # 旧摘要必须并入，否则丢历史
    body = payload[2:]
    assert [m["content"] for m in body] == ["第0条", "第1条", "第2条"]  # 只有窗口外那 3 条
    assert [m["role"] for m in body] == ["user", "assistant", "user"]   # ai → assistant 映射
    assert all("第10条" != m["content"] for m in body)                  # 窗口内的不进摘要

    capped = wcc.build_compact_input(_rows(1000), "", keep_last=10, cap=50)
    assert len(capped) == 1 + 50                                # 超长会话有上限，别把摘要调用顶爆
    assert capped[-1]["content"] == "第989条"                    # 取窗口外"最近"的那些


async def test_compact_skips_with_honest_numbers_when_nothing_is_outside():
    """全在窗口里 → 不花 LLM 调用，并把真实条数说清楚（用户看到的工具卡片不算）"""
    original = wcc.collect_real_messages
    wcc.collect_real_messages = lambda repo, world: _async(_rows(6))
    try:
        got = await wcc.compact_session(_Repo(), _world())
    finally:
        wcc.collect_real_messages = original
    assert got["success"] and got["skipped"] and got["reason"] == "window"
    assert got["real_messages"] == 6 and got["keep_last"] == wcs.WORLD_CHAT_KEEP_LAST


async def test_compact_summarizes_outside_window_and_merges_previous_summary():
    seen: dict = {}
    original_rows, original_cred, original_model = wcc.collect_real_messages, wcs._resolve_world_credentials, wcs.resolve_world_chat_model
    original_compress = ccs.compress_messages

    async def fake_compress(messages, **kw):
        seen["messages"], seen["kw"] = messages, kw
        return ([{"role": "system", "content": "[上下文摘要 — 以下是之前对话的压缩版本]\n旧事已办"}],
                {"compressed": True, "before_tokens": 1200, "after_tokens": 300, "compression_ratio_pct": 75})

    wcc.collect_real_messages = lambda repo, world: _async(_rows(13))
    wcs._resolve_world_credentials = lambda repo, world: _async(("key", "https://api"))
    wcs.resolve_world_chat_model = lambda repo, world, base, wai: _async("m")
    ccs.compress_messages = fake_compress
    repo, world = _Repo(), _world(config={"chat_summaries": {"w1:m:abc": "更早的摘要"}})
    try:
        got = await wcc.compact_session(repo, world)
    finally:
        wcc.collect_real_messages, wcs._resolve_world_credentials = original_rows, original_cred
        wcs.resolve_world_chat_model, ccs.compress_messages = original_model, original_compress

    assert got["success"] and not got.get("skipped")
    assert got["compressible"] == 3 and got["merged_previous"] is True
    assert got["before_tokens"] == 1200 and got["compression_ratio_pct"] == 75
    assert seen["kw"]["keep_last_n"] == 0                        # 传进来的都该被摘要（窗口内不在其中）
    assert seen["messages"][1]["content"].startswith("【此前的上下文摘要】")
    assert [m["content"] for m in seen["messages"][2:]] == ["第0条", "第1条", "第2条"]
    assert world.config["chat_summaries"]["w1:m:abc"].startswith("[上下文摘要")
    assert repo.flushed == 1                                     # 摘要落盘（调用方 commit）


async def test_compact_reports_failure_instead_of_pretending():
    original_rows, original_cred, original_model = wcc.collect_real_messages, wcs._resolve_world_credentials, wcs.resolve_world_chat_model
    original_compress = ccs.compress_messages

    async def fake_compress(messages, **kw):
        return (messages, {"compressed": False, "reason": "摘要生成失败: 超时"})

    wcc.collect_real_messages = lambda repo, world: _async(_rows(30))
    wcs._resolve_world_credentials = lambda repo, world: _async(("key", "https://api"))
    wcs.resolve_world_chat_model = lambda repo, world, base, wai: _async("m")
    ccs.compress_messages = fake_compress
    try:
        got = await wcc.compact_session(_Repo(), _world())
    finally:
        wcc.collect_real_messages, wcs._resolve_world_credentials = original_rows, original_cred
        wcs.resolve_world_chat_model, ccs.compress_messages = original_model, original_compress
    assert got["success"] is False and "超时" in got["error"]


async def test_command_and_tool_share_one_summary_text():
    """/compact 的文案必须等于工具 own summary 的输出（唯一展示入口）"""
    from app.services.world.world_chat_commands import CmdResult, _cmd_compact
    from app.tools.world import tool_result_summary

    result = {"success": True, "skipped": True, "real_messages": 6, "keep_last": 10, "compressible": 0}
    original = wcc.compact_session
    wcc.compact_session = lambda repo, world: _async(result)
    try:
        out = await _cmd_compact(SimpleNamespace(world_repo=None, world=None, cmd_text="/compact", user_id=1, args=""))
    finally:
        wcc.compact_session = original
    assert isinstance(out, CmdResult) and out.ok is True
    assert out.text == tool_result_summary("compact_context", result)
    assert "6 条" in out.text and "工具卡片与思考不进模型上下文" in out.text


async def test_summary_call_disables_thinking_and_retries_when_budget_is_eaten():
    """2026-09-17 用户报「上下文压缩失败：摘要生成失败: LLM 返回空摘要」的契约守卫。

    病根：DeepSeek v4 默认思考，思考 token 与正文抢**同一个** max_tokens 预算——同一份 22k tokens
    输入实测 budget=800 时 completion=800/reasoning=800/finish_reason=length/content 空。
    契约：① 摘要调用必须**显式关思考**；② 首答为空要**加大预算重试**；③ 两次都空才报错（带 finish_reason）。
    """
    import app.ai.llm as llm

    calls: list[dict] = []

    async def fake_chat_completion(**kw):
        calls.append(kw)
        if len(calls) == 1:
            return {"content": "  ", "finish_reason": "length", "usage": {"completion_tokens": 1500}}
        return {"content": "摘要正文", "finish_reason": "stop", "usage": {"completion_tokens": 200}}

    original = llm.chat_completion
    llm.chat_completion = fake_chat_completion
    try:
        summary = await ccs._request_summary(
            [{"role": "user", "content": "请总结"}],
            model="m", api_base_url="https://api", api_key="k",
        )
    finally:
        llm.chat_completion = original

    assert summary == "摘要正文"
    assert [c["thinking_enabled"] for c in calls] == [False, False]       # 每次都显式关思考
    assert calls[1]["max_tokens"] > calls[0]["max_tokens"]                # 空摘要 → 加大预算


async def test_summary_call_reports_finish_reason_when_still_empty():
    import app.ai.llm as llm

    async def always_empty(**kw):
        return {"content": "", "finish_reason": "length", "usage": {"completion_tokens": 6000}}

    original = llm.chat_completion
    llm.chat_completion = always_empty
    try:
        try:
            await ccs._request_summary([{"role": "user", "content": "请总结"}],
                                       model="m", api_base_url="https://api", api_key="k")
            raise AssertionError("空摘要必须报错，不能当成压缩成功")
        except ValueError as e:
            assert "空摘要" in str(e) and "finish_reason=length" in str(e)   # 报错要能诊断
    finally:
        llm.chat_completion = original


def test_thinking_flag_is_three_state():
    """None=不表态、True=显式开、False=显式关（关掉才不会被思考吃满短输出预算）"""
    from app.ai.llm import _build_chat_payload

    base = dict(messages=[], model="m", temperature=0.3, top_p=0.9, max_tokens=800,
                provider_supports_thinking=True)
    assert "thinking" not in _build_chat_payload(**base, thinking_enabled=None)
    assert _build_chat_payload(**base, thinking_enabled=True)["thinking"] == {"type": "enabled"}
    assert _build_chat_payload(**base, thinking_enabled=False)["thinking"] == {"type": "disabled"}


async def _async(value):
    return value
