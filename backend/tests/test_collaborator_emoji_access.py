"""合作者表情包权限：主人 / 管理员 / 获主人授权的 can_edit 合作者

表情包与通道是两件事：包本身是创建者的资产；这里管的是「这个 AI 能拿哪些包发图」。
"""
from types import SimpleNamespace


def _agent(**kw):
    base = dict(owner_id=1, collaborators_manage_emoji=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _collab(can_edit: bool):
    return SimpleNamespace(can_edit=can_edit)


def test_owner_and_admin_always_pass():
    from app.routers.agents import _can_manage_agent_emoji

    assert _can_manage_agent_emoji(_agent(), {"user_id": 1}) is True
    assert _can_manage_agent_emoji(_agent(), {"user_id": 9, "role": "admin"}) is True


def test_collaborator_needs_edit_right_and_the_switch():
    from app.routers.agents import _can_manage_agent_emoji

    # 没开授权：有编辑权也不能改
    agent = _agent()
    agent._collaborator = _collab(True)
    assert _can_manage_agent_emoji(agent, {"user_id": 9}) is False

    # 开了授权 + 有编辑权：可以改
    agent = _agent(collaborators_manage_emoji=True)
    agent._collaborator = _collab(True)
    assert _can_manage_agent_emoji(agent, {"user_id": 9}) is True

    # 开了授权但没有编辑权：不能改
    agent = _agent(collaborators_manage_emoji=True)
    agent._collaborator = _collab(False)
    assert _can_manage_agent_emoji(agent, {"user_id": 9}) is False
