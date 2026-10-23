"""世界侧技能的交付契约。

四件事要有守卫，都是「写的时候对，改的时候悄悄走形」的类型：
1. read_manual 的 doc 枚举 = 文档库的 DOC_DEFS（两份清单各写一遍，加文档必漏一处）
2. 世界 AI 设计技能所需的《世界 AI 技能参考》真的能读到
3. 颁布/试跑两个工具对「没给名字 / 没颁布过 / 参数写坏」给出可操作的回话
4. 写入时与加载时用同一份判定（否则又会出现「写了但居民看不见」）
"""
from __future__ import annotations

import shutil
from types import SimpleNamespace

from app.services.docs import library
from app.services.world.world_skill_runtime import validate_manifest, validate_skill_code
from app.tools.world.read_manual import ReadManualTool
from app.tools.world.run_world_skill import RunWorldSkillTool

_OK_CODE = 'async def run(args, ctx):\n    return {"success": True, "result": "ok"}\n'
_TMP_WORLD = 987650001  # 探针世界号：只在这个用例里建目录，finally 删干净


def test_read_manual_enum_matches_doc_defs():
    """工具参数枚举必须跟着文档库走：加一份文档只改一处"""
    assert set(ReadManualTool.parameters["doc"]["enum"]) == set(library.DOC_DEFS)


def test_world_skill_doc_readable():
    """《世界 AI 技能参考》在文档库里，且目录里能看到原则、协议、自检三节"""
    doc = library.read("world-skill")
    assert doc["ok"] is True, doc.get("error")
    toc = doc["result"]
    for section in ("## 1 设计原则", "## 3 ctx 能力与权限", "## 7 颁布后自检"):
        assert section in toc, section
    hit = library.read("world-skill", "能力对等 权限")
    assert hit["ok"] is True and "能力对等" in hit["result"]


def test_forced_prompt_points_at_skill_entry_points():
    """提示词必须点名颁布/试跑/排查三个入口，否则 AI 又回去手写两个文件"""
    from app.services.world.world_chat_service import build_forced_prompt

    text = build_forced_prompt()
    for needle in ("write_world_skill", "run_world_skill", "list_world_skills", "doc=world-skill"):
        assert needle in text, needle


def test_validate_manifest_catches_silent_failures():
    """写入与加载共用的判定：这些错误以前只会让技能静默消失"""
    assert validate_manifest({"name": "duel_join", "description": "入场"})[0] is True
    assert "name 非法" in validate_manifest({"name": "duel-join", "description": "x"})[1]
    assert "缺 description" in validate_manifest({"name": "duel_join"})[1]
    assert "未知权限" in validate_manifest(
        {"name": "duel_join", "description": "x", "permissions": ["data:全部"]})[1]
    assert "arguments 必须是" in validate_manifest(
        {"name": "duel_join", "description": "x", "arguments": []})[1]


def test_validate_skill_code_matches_sandbox_rules():
    """沙箱规则提前到写入时：import 越界 / run 不是 async / 语法错都在颁布前拦住"""
    assert validate_skill_code(_OK_CODE)[0] is True
    assert "os" in validate_skill_code("import os\n\nasync def run(args, ctx):\n    return {}\n")[1]
    assert "async" in validate_skill_code("def run(args, ctx):\n    return {}\n")[1]
    assert "语法" in validate_skill_code("async def run(args, ctx)\n")[1]
    assert validate_skill_code("x = 1\n")[0] is False


def test_write_world_skill_roundtrip_and_skipped_report():
    """一次调用写全两文件；坏目录与旧布局进 skipped 并带原因；被校验拦下的不落盘"""
    from app.paths import world_dir
    from app.services.world.world_skill_runtime import list_world_skills_detail, write_world_skill

    root = world_dir(_TMP_WORLD)
    try:
        out = write_world_skill(_TMP_WORLD, name="probe_join", description="探针入场",
                                code=_OK_CODE, permissions=["data:read"], types=["*"])
        assert out["success"] is True and out["name"] == "probe_join", out
        assert (root / "skills" / "probe_join" / "manifest.json").is_file()

        bad = root / "skills" / "bad-name"
        bad.mkdir(parents=True)
        (bad / "manifest.json").write_text('{"name": "bad-name", "description": "x"}', encoding="utf-8")
        (bad / "code.py").write_text(_OK_CODE, encoding="utf-8")
        (root / "skills" / "code.py").write_text(_OK_CODE, encoding="utf-8")  # 旧布局：直接放根下

        detail = list_world_skills_detail(_TMP_WORLD)
        assert [s["name"] for s in detail["skills"]] == ["probe_join"]
        reasons = {s["dir"]: s["reason"] for s in detail["skipped"]}
        assert "name 非法" in reasons["skills/bad-name"]
        assert "子目录" in reasons["skills/code.py"]

        rejected = write_world_skill(_TMP_WORLD, name="probe_bad", description="d",
                                     code=_OK_CODE, permissions=["data:全部"])
        assert rejected["success"] is False and "未知权限" in rejected["error"]
        assert not (root / "skills" / "probe_bad").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


class _Repo:
    session = None


class _Ctx:
    def __init__(self, args: dict):
        self.args = args
        self.world = SimpleNamespace(id=10 ** 9)
        self.world_repo = _Repo()

    async def progress(self, note: str) -> None:
        pass


async def test_run_world_skill_tells_you_what_is_missing():
    """试跑失败要能自我解释：没名字说没名字，没颁布过就把已颁布的列出来"""
    tool = RunWorldSkillTool()

    empty = await tool.execute(_Ctx({}))
    assert empty["success"] is False and "name" in empty["error"] and empty["available"] == []

    unknown = await tool.execute(_Ctx({"name": "duel_join"}))
    assert unknown["success"] is False and "没有颁布" in unknown["error"]
    assert isinstance(unknown["available"], list)


def test_run_world_skill_summary_is_human():
    """卡片折叠行说清通过与否，带结果或错误原文"""
    tool = RunWorldSkillTool()
    assert "试跑通过" in tool.summary({"success": True, "result": "阿岚 入桌"})
    assert "试跑失败" in tool.summary({"success": False, "error": "skill 执行超时（30s）"})
