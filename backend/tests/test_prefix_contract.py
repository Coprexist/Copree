"""前缀契约：段清单、唯一出口、守卫。

这三样是"不靠后来者自觉"的部分：

1. `SECTIONS` 必须覆盖 `ContextConfig` 的全部可配置段——加了一段却漏登记，这里当场红；
2. `locked()` 是唯一出口：首见钉住，之后一律旧字节（锁定态换不了）；
3. `guard()` 在"有人绕过 locked 改写前缀"时抛错，而不是悄悄让所有人缓存失效。

2026-11-14 现场：一天里 send_voice / file_fetch / 大肥鱼表情包三次改动，版本号与工具名单都没变，
前缀却从第 0 字节失效 7 次、多付约 283k 未命中。锁"输入"不够，得锁"输出"。
"""
import pytest

pytestmark = pytest.mark.anyio


def test_sections_cover_every_configurable_segment():
    from app.ai.prefix import SECTIONS
    from app.models.context_config import ContextConfig

    cfg = ContextConfig()
    assert set(SECTIONS) == set(cfg.segment_order) == set(cfg.segments), (
        "可配置段与 ai/prefix.SECTIONS 必须一一对应：新增段要在这里声明它属于"
        '"source"（有版本源、变更走通知）还是 "locked"（仅靠快照保证字节不动），'
        "否则它进了前缀也没人管")


def test_notice_sources_cover_every_sourced_section():
    """有版本源的段，必须都在通知的源清单里——否则改了它没人被告知"""
    from app.ai.prefix import SECTIONS, notice_sources

    assert {"personality", "tools", "injected_skills"} <= {
        s for s, kind in SECTIONS.items() if kind == "source"}

    sources = notice_sources(agent_id=1, prompt_source="agent-prompt-1")
    assert "agent-prompt-1" in sources        # 人格（本体/覆盖由调用方给）
    assert "memory-index-1" in sources        # 记忆索引
    assert "emoji-packs" in sources           # 表情包清单（2026-11-14 补上的缺口）
    assert "platform" in sources              # 平台工具


def test_locked_pins_the_bytes_and_guard_catches_a_bypass():
    from app.ai.prefix import PrefixDrift, guard, locked

    holder = {}
    assert locked(holder, "group:1", "agent-prompt-1", "v1") == "v1"
    assert locked(holder, "group:1", "agent-prompt-1", "v2（平台加了工具）") == "v1", (
        "锁定态一律发旧字节：新内容要等 compact/clear 解锁")

    guard(holder, "group:1", "agent-prompt-1", "v1")   # 一致 → 放行
    with pytest.raises(PrefixDrift):
        guard(holder, "group:1", "agent-prompt-1", "v1 + 偷偷追加的一段")


def test_snapshots_are_bounded_per_agent():
    """一个 AI 进很多群：快照不能无限长（旧的按最近使用丢弃，丢弃不影响正确性）"""
    from app.services import capability_versioning as cv

    holder = {}
    for i in range(cv.PREFIX_SNAPSHOT_LIMIT + 10):
        cv.freeze_prefix(holder, f"group:{i}", f"v{i}", variant="agent-prompt-1")
    stored = holder["cap_effective_prefix"]
    assert len(stored) == cv.PREFIX_SNAPSHOT_LIMIT
    # 最旧的被丢、最新的还在
    assert "group:0|prefix:agent-prompt-1" not in stored
    assert f"group:{cv.PREFIX_SNAPSHOT_LIMIT + 9}|prefix:agent-prompt-1" in stored


def test_persona_variants_do_not_share_a_snapshot():
    """群里有人的 per-user 覆盖、有人没有：两个版本各钉一份，B 不能读到 A 的"""
    from app.ai.prefix import locked

    holder = {}
    assert locked(holder, "group:1", "agent-prompt-1", "本体") == "本体"
    assert locked(holder, "group:1", "agent-prompt-1-u7", "用户7的覆盖") == "用户7的覆盖"
    assert locked(holder, "group:1", "agent-prompt-1", "本体（又改了）") == "本体"
