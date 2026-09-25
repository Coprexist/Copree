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
        assert t.hot - t.idle < t.hot - t.post, "ΔT_idle < T_hot − T_post（用户定的等价写法）"


def test_should_compress_boundary_is_the_threshold_it_is_given():
    """同一个请求体：阈值高一线就不压、低一线就压——门槛就是传进去的那个数"""
    from app.services.memory.context_compression_service import should_compress, estimate_tokens

    messages = [{"role": "user", "content": "上下文压缩阈值边界测试" * 8} for _ in range(9)]
    estimated = estimate_tokens(messages)
    window = 128_000

    assert should_compress(messages, threshold=(estimated + 1) / window) is False
    assert should_compress(messages, threshold=(estimated - 1) / window) is True