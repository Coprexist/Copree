"""文档库（AI 查文档）与尺时间工具：纯逻辑，不碰数据库。"""

import os
import shutil
import tempfile
from pathlib import Path

from app.services.docs import library

# 用例里现造一份最小文档：不依赖部署有没有把 docs 挂进容器
_MANUAL = """# 用户手册

## 3. 创建与管理 AI 角色

### 3.1 创建 AI

性格描述怎么写，见创建 AI 参考。

## 5. 消息格式

彩色文字用 <color=red>文本</color>。
"""

_GUIDE = """# 创建 AI 参考

## 5 怎么写

### 5.2 四层结构

身份、关系、说话方式、边界。

### 5.4 不要写什么

不要写「保持人设不崩」。
"""


def _make_docs(tmp: str) -> Path:
    root = Path(tmp) / "docs"
    (root / "guides").mkdir(parents=True)
    (root / "guides" / "用户手册.md").write_text(_MANUAL, encoding="utf-8")
    (root / "guides" / "创建AI参考.md").write_text(_GUIDE, encoding="utf-8")
    return root


def _use_root(root: Path):
    """把文档根指到临时目录；返回还原函数（用例结束必须调用）。"""
    old_env = os.environ.get("COPREE_DOCS_DIR")
    old_cands = library.candidates
    os.environ["COPREE_DOCS_DIR"] = str(root)
    library.candidates = lambda: [root] + old_cands()

    def restore():
        library.candidates = old_cands
        if old_env is None:
            os.environ.pop("COPREE_DOCS_DIR", None)
        else:
            os.environ["COPREE_DOCS_DIR"] = old_env

    return restore


def test_docs_read_toc_and_search():
    tmp = tempfile.mkdtemp()
    try:
        root = _make_docs(tmp)
        restore = _use_root(root)

        toc = library.read("manual")
        assert toc["ok"] and toc["title"] == "用户手册" and toc["keyword"] is None
        # 目录只给标题行，不把正文倒出来
        assert "用户手册" in toc["result"] and "彩色文字" not in toc["result"].split("\n")[0]

        hit = library.read("ai-guide", "边界")
        assert hit["ok"] and hit["keyword"] == "边界"
        assert "四层结构" in hit["result"] or "身份、关系" in hit["result"]

        miss = library.read("ai-guide", "根本不存在的词")
        assert miss["ok"] and "没有「根本不存在的词」" in miss["result"]

        restore()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_docs_unknown_id_and_missing_root():
    tmp = tempfile.mkdtemp()
    try:
        root = _make_docs(tmp)
        restore = _use_root(root)

        bad = library.read("nope")
        assert bad["ok"] is False and "没有这份文档" in bad["error"]

        restore()

        # 根目录找不到：报错要说清找过哪些位置，否则部署侧只能猜是不是没挂载
        old_cands = library.candidates
        library.candidates = lambda: [Path(tmp) / "missing"]
        try:
            err = library.read("manual")
            assert err["ok"] is False and "文档目录没找到" in err["error"]
        finally:
            library.candidates = old_cands
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ruler_time_keeps_last_reply_at():
    from app.ai.chat_chain import ChatChainManager

    m = ChatChainManager()
    m.register_ai(agent_id=7, group_id=11, ruler_time=120)
    m._last_reply[11][7]["last_reply_at"] = 1000.0

    assert m.update_ruler_time(7, 11, 300) is True
    assert m._last_reply[11][7]["ruler_time"] == 300
    # 改尺时间不该把当前这条链作废（否则 AI 会被自己的调整立刻唤醒一次）
    assert m._last_reply[11][7]["last_reply_at"] == 1000.0
    # 没接过话的人不注册：返回 False，交给工具层解释
    assert m.update_ruler_time(8, 11, 60) is False


async def test_update_ruler_time_tool():
    from app.ai.chat_chain import chat_chain_manager
    from app.tools.chat_social.update_ruler_time import UpdateRulerTime

    chat_chain_manager.register_ai(agent_id=21, group_id=31, ruler_time=120)
    tool = UpdateRulerTime()

    ok = await tool.execute(None, 21, 31, {"seconds": 45}, {})
    assert ok["success"] is True and "45" in ok["message"]
    assert chat_chain_manager._last_reply[31][21]["ruler_time"] == 45

    # 越界钳到 10-3600
    clamped = await tool.execute(None, 21, 31, {"seconds": 5}, {})
    assert clamped["success"] is True and chat_chain_manager._last_reply[31][21]["ruler_time"] == 10

    # 私信里没有聊天链
    dm = await tool.execute(None, 21, None, {"seconds": 60}, {})
    assert dm["success"] is False and "只对群聊" in dm["error"]
