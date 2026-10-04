"""平台出处：每个 AI 都知道自己在 Copree 上（这句话进锁定前缀，所以必须短）

管理员可以整段覆盖 core_identity（那是"我的 AI 守什么规矩"的自由），
但"你从哪来"是平台事实——拼在覆盖之后，盖不掉。
"""
from app.utils.pure.prompt_loader import PLATFORM_ORIGIN


def test_origin_is_short_and_points_at_the_repo():
    assert "Copree" in PLATFORM_ORIGIN
    assert "github.com/Coprexist/copree" in PLATFORM_ORIGIN
    assert len(PLATFORM_ORIGIN) < 120, "这句每轮都在前缀里，别写成一段"


def test_origin_survives_an_admin_override():
    from app.ai.llm import _core_identity

    assert "github.com/Coprexist/copree" in _core_identity({})
    overridden = _core_identity({"core_identity": "只许说好话"})
    assert "只许说好话" in overridden
    assert "github.com/Coprexist/copree" in overridden
