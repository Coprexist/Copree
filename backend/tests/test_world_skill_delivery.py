"""世界侧技能的交付契约。

三件事要有守卫，都是「写的时候对，改的时候悄悄走形」的类型：
1. read_manual 的 doc 枚举 = 文档库的 DOC_DEFS（两份清单各写一遍，加文档必漏一处）
2. 世界 AI 设计技能所需的《世界 AI 技能参考》真的能读到
3. 试跑工具对「没给名字 / 没颁布过」给出可操作的回话（造物主唯一验证手段，不能哑）
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.docs import library
from app.tools.world.read_manual import ReadManualTool
from app.tools.world.run_world_skill import RunWorldSkillTool


def test_read_manual_enum_matches_doc_defs():
    """工具参数枚举必须跟着文档库走：加一份文档只改一处"""
    assert set(ReadManualTool.parameters["doc"]["enum"]) == set(library.DOC_DEFS)


def test_world_skill_doc_readable():
    """《世界 AI 技能参考》在文档库里，且目录里能看到原则与协议两节"""
    doc = library.read("world-skill")
    assert doc["ok"] is True, doc.get("error")
    toc = doc["result"]
    for section in ("## 1 设计原则", "## 3 ctx 能力与权限", "## 7 颁布后自检"):
        assert section in toc, section
    hit = library.read("world-skill", "能力对等 权限")
    assert hit["ok"] is True and "能力对等" in hit["result"]


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
