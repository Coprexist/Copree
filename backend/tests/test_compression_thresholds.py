"""三档压缩阈值：T_post < T_idle < T_hot（1/e 插值 + 区间约束）

设计见 docs/dev/conversation_history.md §6：体积是「该不该压」的必要条件，
空闲只决定「什么时候压最划算」；冷路径用 T_idle、热路径用 T_hot。
"""
import math

import pytest

pytestmark = pytest.mark.anyio

TOL = 1e-9


def test_idle_sits_strictly_inside_the_band():
    """贴两端就失去意义：贴 T_post 白跑、贴 T_hot 退化成只有一个阈值"""
    from app.services.memory.context_compression_service import compression_thresholds

    t = compression_thresholds(0.60)

    assert t.post < t.idle < t.hot
    assert abs(t.post - 0.12) < TOL, "T_post = T_hot × COMPRESSION_TARGET_MAX"
    assert abs(t.idle - (t.post + (t.hot - t.post) / math.e)) < TOL
    assert abs(t.idle - 0.2966) < 1e-4, "128K 窗口下 ≈ 38.0K tokens，约等于热阈值的一半"


def test_idle_tracks_the_hot_threshold():
    """三档只有一个自变量：热阈值一动，冷阈值跟着走（调用点不各自乘系数）"""
    from app.services.memory.context_compression_service import compression_thresholds

    for hot in (0.40, 0.60, 0.90):
        t = compression_thresholds(hot)
        assert t.hot == hot
        assert t.post < t.idle < t.hot, "插值系数在 (0,1) 内 → 区间约束自动成立"
        assert t.hot - t.idle < t.hot - t.post, "ΔT_idle < T_hot − T_post（等价写法）"


def test_should_compress_boundary_is_the_threshold_it_is_given():
    """同一个请求体：阈值高一线就不压、低一线就压——门槛就是传进去的那个数"""
    from app.services.memory.context_compression_service import should_compress, estimate_tokens

    messages = [{"role": "user", "content": "上下文压缩阈值边界测试" * 8} for _ in range(9)]
    estimated = estimate_tokens(messages)
    window = 128_000

    assert should_compress(messages, threshold=(estimated + 1) / window) is False
    assert should_compress(messages, threshold=(estimated - 1) / window) is True

def test_coefficients_are_parameters_not_globals():
    """两个系数是可传参的（默认 1/e + 20%），不是全局状态——纯函数、互不影响"""
    from app.services.memory.context_compression_service import compression_thresholds

    d = compression_thresholds(0.60)
    c = compression_thresholds(0.60, idle_fraction=0.5, post_fraction=0.10)

    assert abs(c.post - 0.06) < TOL, "压缩目标 10% → T_post = 0.06"
    assert abs(c.idle - (0.06 + 0.5 * (0.60 - 0.06))) < TOL
    assert d.idle < c.idle, "系数调大 → 冷阈值贴近热阈值"
    assert abs(compression_thresholds(0.60).idle - d.idle) < TOL, "别人传过参数不影响默认调用"


def test_context_window_follows_the_model():
    """窗口按模型取（认不出退回保守默认）——不能再拿一个常量套所有模型"""
    from app.utils.pure.model_window import DEFAULT_CONTEXT_WINDOW, context_window_for

    assert context_window_for("deepseek-v4-flash") == 128_000
    assert context_window_for("GPT-4.1-preview") == 1_000_000, "大小写与后缀无关"
    assert context_window_for("some-unknown-model") == DEFAULT_CONTEXT_WINDOW
    assert context_window_for(None) == DEFAULT_CONTEXT_WINDOW


async def test_db_knobs_override_and_clamp(migrated_db):
    """库里的系数生效；写坏了（越界）回默认，不能变成「永不压缩」"""
    from sqlalchemy import text

    from app.database import async_session
    from app.services.memory.context_compression_service import compression_thresholds, get_compression_thresholds

    async with async_session() as db:
        await db.execute(text(
            "INSERT INTO conversation_log_config (id, compression_threshold, idle_threshold_percent, compress_target_percent) "
            "VALUES (1, 60, 50, 10) ON CONFLICT (id) DO UPDATE SET "
            "compression_threshold=60, idle_threshold_percent=50, compress_target_percent=10"
        ))
        await db.commit()
        t = await get_compression_thresholds(db)
        assert abs(t.post - 0.06) < TOL and abs(t.idle - 0.33) < TOL

        await db.execute(text(
            "UPDATE conversation_log_config SET idle_threshold_percent=120, compress_target_percent=0 WHERE id=1"
        ))
        await db.commit()
        bad = await get_compression_thresholds(db)
        good = compression_thresholds(0.60)
        assert abs(bad.post - good.post) < TOL and abs(bad.idle - good.idle) < TOL, "越界 → 回默认"

