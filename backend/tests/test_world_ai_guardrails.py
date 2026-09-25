"""世界 AI 安全护栏的契约守卫（2026-09-15 产品定）。

三件事必须有静态可验证的边界，不能只靠提示词自觉：
1. 禁用后缀：可执行文件/安装包/宿主脚本——创建即拒，遗留的兜底强删，AI 与用户同一套；
2. 下载：固定落点 downloads/，违规内容（色情/暴力/违法）命中即失败、不落盘；
3. 运行模式：自动放行、审阅弹窗、计划先挡；AI 没有改模式的工具（用户侧 API 才有）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile

from app.tools.world.base import WorldToolContext
from app.services.world import world_file_service as fs
from app.services.world.world_ai_mode import (
    DEFAULT_MODE,
    MODES,
    USER_NOTE_KEY,
    action_of,
    gate_tool_call,
    get_mode,
    pending_approvals,
    resolve_approval,
    with_user_note,
)
from app.services.world.world_turn import TurnBroadcast, _workers
from app.services.world.world_moderation import inspect

WORLD_ID = 987654


@contextlib.contextmanager
def _raises(*exc):
    """pytest.raises 的最小替身（无 pytest 运行器只提供 fixture/mark，不扩展它）"""
    try:
        yield
    except exc:
        return
    raise AssertionError(f"期望抛出 {exc}，但没有")


@contextlib.contextmanager
def _sandbox_world_dir():
    """把世界文件根指向临时目录（测试不碰真实 data/worlds）"""
    tmp = tempfile.mkdtemp(prefix="world-guard-")
    old = fs.WORLDS_ROOT
    fs.WORLDS_ROOT = __import__("pathlib").Path(tmp)
    try:
        yield fs._world_dir(WORLD_ID)
    finally:
        fs.WORLDS_ROOT = old
        shutil.rmtree(tmp, ignore_errors=True)


class _World:
    """世界替身：门禁只读 config（模式）与 id"""

    def __init__(self, mode: str | None = None):
        self.id = WORLD_ID
        self.config = {} if mode is None else {"ai_mode": mode}


def test_banned_extensions_are_rejected_at_creation():
    """AI 与用户共用同一条写入收口：禁用后缀创建即拒，普通世界代码照常"""
    with _sandbox_world_dir() as d:
        assert fs.write_file(WORLD_ID, "js/app.js", "console.log(1)")["path"] == "js/app.js"
        assert fs.write_file(WORLD_ID, "main.py", "print(1)")["path"] == "main.py"
        for name in ("tool.exe", "setup.msi", "run.sh", "hook.ps1", "lib.dll", "app.apk"):
            with _raises(fs.BannedFileError):
                fs.write_file(WORLD_ID, name, "x")
        with _raises(ValueError):              # 非禁用但也不在允许清单 → 同样拒绝
            fs.write_file(WORLD_ID, "notes.weird", "x")
        assert (d / "tool.exe").exists() is False


def test_sweep_removes_planted_binaries():
    """兜底强删：手动拷进目录的可执行文件，扫描一次就没了（返回被删清单）"""
    with _sandbox_world_dir() as d:
        fs.write_file(WORLD_ID, "index.html", "<html></html>")
        (d / "evil.exe").write_bytes(b"MZ")
        (d / "sub").mkdir()
        (d / "sub" / "agent.bat").write_text("del /f /q")
        assert sorted(fs.sweep_banned_files(WORLD_ID)) == ["evil.exe", "sub/agent.bat"]
        assert fs.sweep_banned_files(WORLD_ID) == []          # 幂等
        assert [f["path"] for f in fs.list_files(WORLD_ID)] == ["index.html"]


def test_move_and_copy_stay_inside_the_world():
    """移动/复制都走同一套越界与后缀校验"""
    with _sandbox_world_dir():
        fs.write_file(WORLD_ID, "css/a.css", "body{}")
        assert fs.move_file(WORLD_ID, "css/a.css", "assets/b.css")["path"] == "assets/b.css"
        assert fs.copy_file(WORLD_ID, "assets/b.css", "assets/c.css")["path"] == "assets/c.css"
        for call in (
            lambda: fs.move_file(WORLD_ID, "assets/b.css", "../escape.css"),
            lambda: fs.move_file(WORLD_ID, "assets/b.css", "assets/x.exe"),
            lambda: fs.copy_file(WORLD_ID, "assets/b.css", "assets/x.msi"),
            lambda: fs.move_file(WORLD_ID, "assets/missing.css", "assets/y.css"),
        ):
            with _raises(ValueError, FileNotFoundError):
                call()


def test_download_lands_in_fixed_dir():
    """下载位置固定：一律落在 downloads/ 下，越界直接拒绝"""
    from app.tools.world.shared import DOWNLOAD_DIR, download_target

    assert download_target("") == DOWNLOAD_DIR
    assert download_target("vue/vue.js") == f"{DOWNLOAD_DIR}/vue/vue.js"
    assert download_target("/vue/vue.js") == f"{DOWNLOAD_DIR}/vue/vue.js"
    assert download_target(f"{DOWNLOAD_DIR}/vue/vue.js") == f"{DOWNLOAD_DIR}/vue/vue.js"  # 不叠前缀
    with _raises(ValueError):
        download_target("../outside.js")


def test_code_urls_are_normalized_to_raw():
    """GitHub 页面链接自动转 raw 直链（AI 常直接贴浏览器地址）"""
    from app.tools.world.shared import normalize_code_url as norm

    assert norm("https://github.com/a/b/blob/main/src/x.ts") == \
        "https://raw.githubusercontent.com/a/b/main/src/x.ts"
    assert norm("https://gist.github.com/u/abc123") == "https://gist.github.com/u/abc123/raw"
    plain = "https://example.com/x.css"
    assert norm(plain) == plain


def test_moderation_blocks_flagged_url_and_body():
    """违规内容拦截：链接/文件名在下载前查，文本正文在下载后查"""
    assert inspect("https://pornhub.com/x.js") is not None
    assert inspect("https://example.com/a.js", "nsfw-pack.js") is not None
    assert inspect("https://example.com/a.js", "a.js", "…gore…".encode()) is not None
    assert inspect("https://example.com/a.js", "assets/a.js", b"body{color:red}") is None
    # 短词整词匹配：不能被 gorgeous / java 这类误伤
    assert inspect("https://example.com/gorgeous.css") is None
    assert inspect("https://example.com/java/App.java") is None


def test_action_classes_are_conservative():
    """只读工具明列；未登记的工具一律按「改动机制」——宁可多问一次"""
    assert action_of("file_read") is None
    assert action_of("web_fetch") is None
    assert action_of("web_download") == "download"
    assert action_of("file_delete") == "delete"
    assert action_of("file_write") == "modify"
    assert action_of("some_future_tool") == "modify"
    assert action_of("world_stats") == "modify"          # 世界自定义 skill 同样受管


def test_mode_defaults_to_review_and_ignores_garbage():
    assert DEFAULT_MODE in MODES
    assert get_mode(_World()) == DEFAULT_MODE
    assert get_mode(_World("auto")) == "auto"
    assert get_mode(_World("nonsense")) == DEFAULT_MODE


async def test_gate_auto_allows_plan_blocks_review_needs_a_human():
    """三种模式的门禁行为（没有可交互前端时审阅按「不同意」处理，不空等）"""
    from app.services.world import world_ai_mode as wam

    # _WAIT_FOR_VIEWER 是产品行为（留 20 秒给页面接上），可测试里根本没有页面，
    # 白等满 20 秒才走「无人应答」分支。这里测的就是"没有前端"，所以压到 0
    # （同文件 test_no_viewer_follows_unattended_policy 同款）。
    args = {"url": "https://example.com/a.js"}
    old_wait = wam._WAIT_FOR_VIEWER
    wam._WAIT_FOR_VIEWER = 0
    try:
        allowed, approved, _ = await gate_tool_call(_World("auto"), WORLD_ID, "web_download", args, {})
        assert allowed and approved

        allowed, _, reason = await gate_tool_call(_World("plan"), WORLD_ID, "file_write", {"path": "a.js"}, {})
        assert not allowed and "计划" in reason
        allowed, approved, _ = await gate_tool_call(
            _World("plan"), WORLD_ID, "file_write", {"path": "a.js"}, {"plan_approved": True},
        )
        assert allowed and approved

        state: dict = {}
        allowed, _, reason = await gate_tool_call(_World("review"), WORLD_ID, "file_delete", {"path": "a.js"}, state)
        assert not allowed and "没有同意" in reason
        assert state.get("approved_classes", set()) == set()

        # 只读工具在任何模式下都不设卡
        for mode in MODES:
            allowed, approved, _ = await gate_tool_call(_World(mode), WORLD_ID, "file_read", {"path": "a.js"}, {})
            assert allowed and not approved
    finally:
        wam._WAIT_FOR_VIEWER = old_wait


def test_unattended_policy_follows_mode():
    """没人应答怎么办由模式决定：只有自动档才「问不到就自己继续」"""
    from app.services.world import world_ai_mode as wam

    assert wam.unattended_policy(_World("auto")) == (True, wam._ASK_TIMEOUT)
    for mode in ("review", "plan"):
        assert wam.unattended_policy(_World(mode)) == (False, wam._APPROVAL_TIMEOUT)


async def test_no_viewer_follows_unattended_policy():
    """连前端都没连着：审阅直接不放行；自动档立即放行（不空等）"""
    from app.services.world import world_ai_mode as wam

    old = wam._WAIT_FOR_VIEWER
    wam._WAIT_FOR_VIEWER = 0
    try:
        ap = await wam.request_approval(987656, "", kind="other", title="t", on_timeout=False)
        assert ap.approved is False and ap.attended is False and "无人应答" in ap.reason
        assert ap.note == "" and ap.instruction == ""
        ap = await wam.request_approval(987656, "", kind="other", title="t", on_timeout=True)
        assert ap.approved is True and ap.attended is False and "自动档" in ap.reason
    finally:
        wam._WAIT_FOR_VIEWER = old


async def test_user_silence_denies_review_but_continues_auto():
    """前端连着、用户不吭声：审阅超时 = 不通过；自动档提问超时 = 放行继续"""
    from app.services.world import world_ai_mode as wam

    wid_turn = 987657
    tb = TurnBroadcast("t_timeout")
    tb.subscribe()                                   # 模拟前端连着
    _workers[wid_turn] = _LiveWorker(tb)
    try:
        ap = await wam.request_approval(
            wid_turn, "t_timeout", kind="download", title="t", timeout=1, on_timeout=False)
        assert ap.approved is False and ap.attended is False and "超时" in ap.reason
        ap = await wam.request_approval(
            wid_turn, "t_timeout", kind="other", title="t", timeout=1, on_timeout=True)
        assert ap.approved is True and ap.attended is False and "自动档" in ap.reason
    finally:
        _workers.pop(wid_turn, None)


async def test_ask_user_tool_follows_mode_when_nobody_answers():
    """ask_user 全链路：自动档没人应答 → 放行继续（对齐 DSH）；审阅/计划档 → 不通过"""
    from app.services.world import world_ai_mode as wam
    from app.tools.world.ask_user import AskUserTool

    old = wam._WAIT_FOR_VIEWER
    wam._WAIT_FOR_VIEWER = 0
    try:
        tool = AskUserTool()
        for mode, expect in (("auto", True), ("review", False), ("plan", False)):
            ctx = WorldToolContext(
                world_repo=None, world=_World(mode), arguments="{}",
                args={"kind": "other", "question": "继续吗？"},
            )
            r = await tool.execute(ctx)
            assert r["success"] is True and r["approved"] is expect, f"{mode}: {r}"
            assert r["answered"] is False and r["answer"] == "未回复"
            assert r[USER_NOTE_KEY] == ""
            if mode == "auto":
                assert "自动档" in r["summary"]
    finally:
        wam._WAIT_FOR_VIEWER = old


def test_resolve_approval_rejects_unknown_id():
    """点了已失效/不存在的审批项 → 明确返回 False（端点据此回 404）"""
    assert resolve_approval("no-such-approval", True) is False


class _LiveWorker:
    """活着的 world worker 替身：只提供门禁要用的两件事（turns / subscribe）"""

    def __init__(self, tb: TurnBroadcast):
        self.turns = {tb.turn_id: tb}
        self.task = asyncio.get_running_loop().create_future()   # 未完成 = worker 还活着

    def subscribe(self, turn_id: str):
        return self.turns.get(turn_id)


async def _take_pending(queue):
    """取下一条 pending 审批（事件流里夹着的 resolved 回执照旧跳过）"""
    while True:
        raw = await asyncio.wait_for(queue.get(), timeout=2)
        payload = json.loads(raw.removeprefix("data: [APPROVAL]").strip())
        if payload.get("status") == "pending":
            return payload


async def test_review_popup_round_trip():
    """审阅模式弹窗的完整闭环：门禁广播 → 用户点同意 → 工具被放行（不碰 LLM、不碰网络）"""
    wid_turn = 987655
    tb = TurnBroadcast("t_test")
    queue = tb.subscribe()                       # 模拟前端连着这条 SSE（没订阅者就不弹窗）
    _workers[wid_turn] = _LiveWorker(tb)
    turn_state = {"turn_id": "t_test"}
    try:
        task = asyncio.create_task(gate_tool_call(
            _World("review"), wid_turn, "web_download",
            {"url": "https://example.com/a.js"}, turn_state,
        ))
        # 弹窗事件必须带着 approval_id + 事件类型关键词到达前端
        raw = await asyncio.wait_for(queue.get(), timeout=2)
        payload = json.loads(raw.removeprefix("data: [APPROVAL]").strip())
        assert payload["status"] == "pending" and payload["kind"] == "download"
        assert payload["approval_id"]
        # 弹窗说人话：标题不能出现"机制/请求"这类内部术语（用户 2026-09-15 反馈）
        assert payload["title"].startswith("AI 想") and "机制" not in payload["title"]
        assert [a["approval_id"] for a in pending_approvals(wid_turn)] == [payload["approval_id"]]

        # 等待期间工具没被放行（还没点按钮）
        assert not task.done()

        assert resolve_approval(payload["approval_id"], True) is True
        allowed, approved, feedback = await asyncio.wait_for(task, timeout=2)
        assert allowed and approved and not feedback     # 用户没写话 → 没什么要转达的
        assert pending_approvals(wid_turn) == []
        assert "download" in turn_state["approved_classes"]     # 同类操作本轮不再问

        # resolved 回执：前端据此关掉弹窗
        resolved = json.loads((await asyncio.wait_for(queue.get(), timeout=2))
                              .removeprefix("data: [APPROVAL]").strip())
        assert resolved["status"] == "resolved" and resolved["approved"] is True

        # 已批准的同类操作不再弹窗（第二次直接放行）
        await gate_tool_call(_World("review"), wid_turn, "web_download",
                             {"url": "https://example.com/b.js"}, turn_state)
        assert queue.qsize() == 0
    finally:
        _workers.pop(wid_turn, None)


async def test_approval_note_is_handed_back_to_the_ai():
    """用户点同意/不同意时写的理由或补充要求，必须回到 AI 手里（2026-09-15 用户要求）。

    吞掉它 = 用户明明说了、AI 没听见：AI 会照自己原来的打算做完，还怪用户没提醒。
    """
    wid_turn = 987651
    tb = TurnBroadcast("t_note")
    queue = tb.subscribe()
    _workers[wid_turn] = _LiveWorker(tb)
    try:
        # ① 同意 + 补充要求 → 放行，但要求随结果交给 AI
        task = asyncio.create_task(gate_tool_call(
            _World("review"), wid_turn, "file_write",
            {"path": "a.js", "content": "1"}, {"turn_id": "t_note"},
        ))
        payload = await _take_pending(queue)
        assert resolve_approval(payload["approval_id"], True, "改用蓝色，标题换成两行") is True
        allowed, approved, feedback = await asyncio.wait_for(task, timeout=2)
        assert allowed and approved
        assert "同意" in feedback and "改用蓝色" in feedback
        # 合并进工具结果（唯一键约定）后，AI 才真的能看到
        assert with_user_note({"success": True}, feedback)[USER_NOTE_KEY] == feedback
        assert with_user_note({"success": True}, "") == {"success": True}

        # ② 不同意 + 理由 → 不执行，理由原样回给 AI（它要据此改方案，而不是换个方式绕过）
        task = asyncio.create_task(gate_tool_call(
            _World("review"), wid_turn, "file_delete", {"path": "a.js"}, {"turn_id": "t_note"},
        ))
        payload = await _take_pending(queue)
        assert resolve_approval(payload["approval_id"], False, "先别删，我还要用") is True
        allowed, _, reason = await asyncio.wait_for(task, timeout=2)
        assert not allowed and "不同意" in reason and "先别删，我还要用" in reason
    finally:
        _workers.pop(wid_turn, None)


async def test_ask_user_carries_the_users_own_words():
    """ask_user 的原话与「有没有人应答」都来自 Approval 字段，不再抠"未回复"字样判断"""
    from app.tools.world.ask_user import AskUserTool

    wid_turn = 987652
    tb = TurnBroadcast("t_ask")
    queue = tb.subscribe()
    _workers[wid_turn] = _LiveWorker(tb)
    tool = AskUserTool()
    # ask_user 用的是 ctx.world.id（门禁那条路是显式传 world_id），所以替身得跟着轮次走
    world = _World("auto")
    world.id = wid_turn
    ctx = WorldToolContext(
        world_repo=None, world=world, arguments="{}",
        args={"kind": "other", "question": "用哪套配色？"}, turn_state={"turn_id": "t_ask"},
    )
    try:
        task = asyncio.create_task(tool.execute(ctx))
        payload = await _take_pending(queue)
        assert resolve_approval(payload["approval_id"], True, "紫色那套，别太亮") is True
        r = await asyncio.wait_for(task, timeout=2)
        assert r["approved"] is True and r["answered"] is True and r["answer"] == "同意"
        assert r[USER_NOTE_KEY] == "紫色那套，别太亮"
        assert "紫色那套，别太亮" in tool.summary(r)      # 卡片上也看得见用户说了什么
    finally:
        _workers.pop(wid_turn, None)

