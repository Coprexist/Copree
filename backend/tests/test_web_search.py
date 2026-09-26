"""web_search 的规划、相关性闸门与排序契约（纯函数 + 假后端，不发真实请求）。"""
import pytest

from app.tools.file_operations.search.authority import authority, domain_of
from app.tools.file_operations.search.plan import plan_queries
from app.tools.file_operations.search.rank import dedupe, rank, relevant

pytestmark = pytest.mark.anyio


def test_plan_splits_aliases_and_glued_names_out_of_a_sentence():
    """模型只给原话时靠机械改写兜底：去括号、拆粘连词、单拉丁词元垫底"""
    got = plan_queries("Copree（AIsChat）类似平台的发展和发布")
    assert got[0] == "Copree AIsChat 类似平台的发展和发布"
    assert "Copree AIsChat" in got
    assert "AIsChat" in got
    assert "CopreeAIsChat 发布" in plan_queries("CopreeAIsChat 发布") or         "Copree AIsChat 发布" in plan_queries("CopreeAIsChat 发布")


async def _with_fake_fetch(fetcher, coro_factory):
    """编排层测试不碰网络：把取数函数换掉，其余（闸门/去重/排序/提示）走真实代码。"""
    from app.tools.file_operations.search import orchestrator

    original = orchestrator._fetch_one
    orchestrator._fetch_one = fetcher
    try:
        return await coro_factory()
    finally:
        orchestrator._fetch_one = original


async def test_orchestrator_drops_engine_filler_and_hints_when_empty():
    """搜索引擎对冷门词返回无关填充——判为 0 结果并给下一步，而不是端给模型"""
    from app.tools.file_operations.search import orchestrator

    async def fake_fetch(client, backend, query, count, exclude, budget, require_adjacent=False):
        items = [
            {"title": "狼蛛电竞产品在线驱动", "url": "https://aula.example/x", "snippet": "驱动下载"},
            {"title": "Copree 官方发布", "url": "https://copree.ai/", "snippet": "Copree 是一个 AI 群聊平台"},
        ]
        kept = [dict(i, provider="fake") for i in items
                if relevant(query, i["title"], i["snippet"])]
        return kept, None, {"provider": "fake", "query": query, "parsed": len(items), "kept": len(kept)}

    async def junk_fetch(client, backend, query, count, exclude, budget, require_adjacent=False):
        return [], None, {"provider": "fake", "query": query, "parsed": 0, "kept": 0}

    out = await _with_fake_fetch(fake_fetch, lambda: orchestrator.search(["Copree AIsChat"], limit=5))
    assert [r["url"] for r in out["results"]] == ["https://copree.ai/"], out

    out_excluded = await _with_fake_fetch(
        fake_fetch, lambda: orchestrator.search(["Copree AIsChat"], exclude=["群聊"], limit=5))
    assert out_excluded["count"] == 0 and "hint" in out_excluded, out_excluded

    empty = await _with_fake_fetch(junk_fetch, lambda: orchestrator.search(["Copree AIsChat"], limit=5))
    assert empty["success"] and empty["count"] == 0 and "hint" in empty, empty
    # 每一轮的实况要留在结果里：搜不到时能分清「解析为 0」和「解析到但不相关」
    assert empty["attempts"] and empty["attempts"][0]["parsed"] == 0, empty["attempts"]
    # 结果是从阶梯的哪一级捞到的要看得出来（严格候选 0 条，单词元兜底 1 条）
    assert out["attempts"][0]["kept"] == 0 and any(a["kept"] == 1 for a in out["attempts"]), out["attempts"]


def test_relevance_gate_and_authority():
    """相关性靠字面重合（拉丁词元 / 中文二元组）；权威只决定相关结果之间的先后"""
    assert relevant("Copree AIsChat", "狼蛛电竞产品在线驱动", "AULA 驱动下载") is False
    assert relevant("清华大学 官网", "清华大学", "学校概况") is True
    assert relevant("Copree AIsChat", "Copree 官方文档", "AIsChat 群聊平台") is True
    assert authority(domain_of("https://copree.ai/docs"), ["Copree"]) == 1.0
    assert authority("github.com", ["Copree"]) == 0.85
    assert authority("hao123.com", ["Copree"]) == 0.1


def test_relevance_requires_every_entity_term():
    """只命中一个词不算相关：AIs 会命中 AIS 船舶系统，github 会命中任意仓库页（实测踩过）"""
    assert relevant("AIs Chat", "船讯网-AIS", "AIS 信号收发能力，船舶自动识别系统") is False
    assert relevant("AIsChat github", "GitHub - foo/bar: 三毛机场", "本仓库用于整理机场节点信息") is False
    assert relevant("AIsChat", "AIsChat 官方文档", "AIsChat 是一个 AI 群聊平台") is True
    # 机械拆词派生的候选要相邻命中：AIs Chat 钓到过 watchaichat 这种同名站
    assert relevant("AIs Chat", "Watch AI Chat - Two AIs Talking 24/7 Live",
                    "Chat with AIs", require_adjacent=True) is False
    assert relevant("AIs Chat", "AIs Chat 官方文档", "AIs Chat 是一个 AI 群聊平台",
                    require_adjacent=True) is True


def test_rank_puts_official_first_and_caps_each_domain():
    items = [
        {"title": "Copree 报道一", "url": "https://news.example.com/a", "snippet": "Copree 平台"},
        {"title": "Copree 官方", "url": "https://copree.ai/", "snippet": "Copree 平台"},
        {"title": "Copree 报道二", "url": "https://news.example.com/b", "snippet": "Copree 平台"},
        {"title": "Copree 报道三", "url": "https://news.example.com/c", "snippet": "Copree 平台"},
        {"title": "Copree 报道四", "url": "https://news.example.com/d", "snippet": "Copree 平台"},
    ]
    got = rank(items, entity_terms=["Copree"], per_domain=3, limit=8)
    assert got[0]["url"] == "https://copree.ai/", got
    assert got[0]["official"] is True and got[1]["official"] is False, got
    assert sum(1 for i in got if "news.example.com" in i["url"]) == 3, got


def test_platform_host_pages_count_as_official_sources():
    """官网之外，官方仓库/发布页也算官方来源——AIsChat 的官方来源就是那个 GitHub 仓库"""
    items = [{"title": "GitHub - Coprexist/Copree: AIsChat 是一个开源 AI 群聊框架",
              "url": "https://github.com/Coprexist/Copree", "snippet": "AIsChat：让 AI 拥有自己的状态"}]
    got = rank(items, entity_terms=["AIsChat"], per_domain=3, limit=8)
    assert got[0]["official"] is True, got


def test_dedupe_collapses_reposts_by_content_fingerprint():
    """同一篇稿子被转载：URL 不同、标题带尾巴，靠标题/指纹判重"""
    a = {"title": "Copree 发布 AI 群聊平台", "url": "https://a.example/x",
         "snippet": "Copree 今天发布了一个 AI 群聊平台，支持多智能体协作与群视界。"}
    b = {"title": "Copree 发布 AI 群聊平台（转载）", "url": "https://b.example/y",
         "snippet": "Copree 今天发布了一个 AI 群聊平台，支持多智能体协作与群视界。"}
    assert len(dedupe([a, b])) == 1, dedupe([a, b])
