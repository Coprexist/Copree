"""
群发现与入群/邀请审批契约测试（真库 ai_group_chat_test）

三件最容易回归的事：
1. 审批权＝可见性/可批性——非群主管理员既批不了也看不到；
2. 入群两条路径——群开了审批就落申请，没开就直接进；
3. 成员邀请两条路径——群开了审批要群主/管理员先批，群主/管理员的邀请免审。

跑法（后端容器没有 pytest，官方跑法是自带运行器）：
    docker exec -w /app -e TEST_DATABASE_URL=<测试库> ai_group_backend \
      python tests/run_without_pytest.py test_group_join_approval
"""
import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.anyio

GROUP_ID = 1


async def expect_value_error(awaitable, match: str) -> None:
    """断言抛出含 match 的 ValueError。

    不用 pytest.raises：容器里没有 pytest，官方跑法（tests/run_without_pytest.py）
    只桩了 fixture 与 mark。
    """
    try:
        await awaitable
    except ValueError as e:
        assert match in str(e), f"错误文案不含「{match}」：{e}"
        return
    raise AssertionError(f"预期抛 ValueError（含「{match}」），实际没有抛")


async def _seed(db, *, auto_approve_join: bool = False, approve_invites: bool = True):
    """1=群主 2=普通成员 3=申请人(非成员) 5=管理员 7=被邀请人(非成员)"""
    await db.execute(text("TRUNCATE groups CASCADE"))
    await db.execute(text("TRUNCATE users CASCADE"))
    await db.execute(text("""
        INSERT INTO users (id, username, password_hash, type) VALUES
        (1, '群主', 'x', 'human'),
        (2, '普通成员', 'x', 'human'),
        (3, '申请人', 'x', 'human'),
        (5, '管理员', 'x', 'human'),
        (7, '被邀请人', 'x', 'human')
    """))
    await db.execute(text("""
        INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar,
                            searchable, auto_approve_join, approve_invites)
        VALUES (:gid, '审批自测群', 'human', 1, 'default', true, true, :auto, :invites)
    """), {"gid": GROUP_ID, "auto": auto_approve_join, "invites": approve_invites})
    await db.execute(text("""
        INSERT INTO group_members (group_id, member_type, member_id, role) VALUES
        (1, 'human', 1, 'owner'),
        (1, 'human', 2, 'member'),
        (1, 'human', 5, 'admin')
    """))
    await db.commit()


async def _is_member(db, user_id: int) -> bool:
    from app.models.group import GroupMember
    row = (await db.execute(select(GroupMember).where(
        GroupMember.group_id == GROUP_ID,
        GroupMember.member_type == "human",
        GroupMember.member_id == user_id,
    ))).scalar_one_or_none()
    return row is not None


async def test_join_request_lands_when_approval_required(migrated_db):
    """群关了自动审批：申请落库等审批，不直接进群；重复申请被挡"""
    from app.database import async_session
    from app.models.group import GroupJoinRequest
    from app.services.social.group_join_service import request_join

    async with async_session() as db:
        await _seed(db)
        result = await request_join(db, GROUP_ID, 3, "想进来看看")
        assert result["status"] == "pending" and result["auto_approved"] is False

        row = await db.get(GroupJoinRequest, result["request_id"])
        assert row.status == "pending" and row.message == "想进来看看"
        assert not await _is_member(db, 3), "申请阶段不该入群"

        await expect_value_error(request_join(db, GROUP_ID, 3), "已提交过申请")
        await db.rollback()


async def test_join_is_direct_when_auto_approved(migrated_db):
    """群没关自动审批：申请即入群（保持旧行为）"""
    from app.database import async_session
    from app.services.social.group_join_service import request_join

    async with async_session() as db:
        await _seed(db, auto_approve_join=True)
        result = await request_join(db, GROUP_ID, 3)
        assert result["status"] == "joined" and result["auto_approved"] is True
        assert await _is_member(db, 3)

        await expect_value_error(request_join(db, GROUP_ID, 3), "已经在该群中")
        await db.rollback()


async def test_only_approver_can_resolve_join_request(migrated_db):
    """普通成员与申请人本人都批不了；管理员同意后才入群"""
    from app.database import async_session
    from app.services.social.group_join_service import request_join, resolve_join_request

    async with async_session() as db:
        await _seed(db)
        request_id = (await request_join(db, GROUP_ID, 3))["request_id"]

        await expect_value_error(
            resolve_join_request(db, request_id, 2, approve=True), "仅群主或管理员可审批")
        await expect_value_error(
            resolve_join_request(db, request_id, 3, approve=True), "仅群主或管理员可审批")

        resolved = await resolve_join_request(db, request_id, 5, approve=True)
        assert resolved["status"] == "approved"
        assert await _is_member(db, 3), "同意后才入群"

        await expect_value_error(
            resolve_join_request(db, request_id, 1, approve=True), "已处理")
        await db.rollback()


async def test_rejected_join_request_keeps_user_out(migrated_db):
    """拒绝：状态终态且不入群"""
    from app.database import async_session
    from app.services.social.group_join_service import request_join, resolve_join_request

    async with async_session() as db:
        await _seed(db)
        request_id = (await request_join(db, GROUP_ID, 3))["request_id"]
        resolved = await resolve_join_request(db, request_id, 1, approve=False)
        assert resolved["status"] == "rejected"
        assert not await _is_member(db, 3)
        await db.rollback()


async def test_needs_approval_exempts_group_managers(migrated_db):
    """纯函数：群开关 + 邀请人角色 → 是否需要审批"""
    from types import SimpleNamespace
    from app.services.social.invitation_service import needs_approval

    on = SimpleNamespace(approve_invites=True)
    off = SimpleNamespace(approve_invites=False)
    assert needs_approval(on, "member") is True
    assert needs_approval(on, None) is True          # 连成员都不是（异常数据）也拦
    assert needs_approval(on, "admin") is False      # 群管理的邀请免审
    assert needs_approval(on, "owner") is False
    assert needs_approval(off, "member") is False    # 群没开审批


async def test_member_invite_waits_for_approval(migrated_db):
    """群开了邀请审批：普通成员的邀请只落记录，被邀请人此时收不到卡片"""
    from app.database import async_session
    from app.repositories.invitation_repo import SQLAlchemyInvitationRepository
    from app.services.social.invitation_service import (
        create_group_invitation, list_pending_invitations,
    )

    async with async_session() as db:
        await _seed(db)
        repo = SQLAlchemyInvitationRepository(db)
        invitation = await create_group_invitation(repo, GROUP_ID, 2, 7, "一起来")
        assert invitation.approval_status == "pending"
        assert invitation.dm_message_id is None, "未获批不该先发卡片"

        pending_for_invitee = await list_pending_invitations(repo, 7)
        assert pending_for_invitee == [], "未获批的邀请不该出现在被邀请人那里"
        await db.rollback()


async def test_owner_invite_skips_approval(migrated_db):
    """群主/管理员的邀请免审：建记录即获批并发卡片"""
    from app.database import async_session
    from app.repositories.invitation_repo import SQLAlchemyInvitationRepository
    from app.services.social.invitation_service import send_group_invitation

    async with async_session() as db:
        await _seed(db)
        repo = SQLAlchemyInvitationRepository(db)
        result = await send_group_invitation(repo, GROUP_ID, 1, 7)
        assert result["pending_approval"] is False
        assert result["dm_message_id"], "免审邀请应当立刻发出卡片"
        await db.rollback()


async def test_approve_then_deny_invitation(migrated_db):
    """审批人批/驳；驳回后同一人可以再次被邀请（记录不能一直占着防重位）"""
    from app.database import async_session
    from app.repositories.invitation_repo import SQLAlchemyInvitationRepository
    from app.services.social.invitation_service import (
        approve_group_invitation, create_group_invitation, deny_group_invitation,
    )

    async with async_session() as db:
        await _seed(db)
        repo = SQLAlchemyInvitationRepository(db)

        invited = await create_group_invitation(repo, GROUP_ID, 2, 7)
        await expect_value_error(
            approve_group_invitation(repo, invited.id, 2), "仅群主或管理员可审批")

        # 驳回：终态 + 不打扰被邀请人 + 防重位释放（同一人可以重新邀请）
        denied = await deny_group_invitation(repo, invited.id, 5)
        assert denied["status"] == "rejected"
        assert invited.status == "rejected", "驳回要终结记录，否则防重位永远占着"
        assert invited.dm_message_id is None, "驳回不打扰被邀请人"

        invited_again = await create_group_invitation(repo, GROUP_ID, 2, 7, "再试一次")
        assert invited_again.approval_status == "pending"

        approved = await approve_group_invitation(repo, invited_again.id, 1)
        assert approved["status"] == "approved" and approved["dm_message_id"]
        await expect_value_error(
            approve_group_invitation(repo, invited_again.id, 1), "无需审批")
        # 已送达但对方还没点的邀请仍是活记录，不能重复邀请
        await expect_value_error(
            create_group_invitation(repo, GROUP_ID, 2, 7), "已有待处理的群邀请")
        await db.rollback()


async def test_pending_request_item_contract(migrated_db):
    """申请列表条目字段口径：三种申请共用同一组字段，前端只按 kind 分区"""
    from app.routers.requests import build_request_item

    item = build_request_item(
        "group_join", 9, 3, user_name="申请人", message="想进来看看",
        group_id=GROUP_ID, group_name="审批自测群",
    )
    assert set(item) == {
        "kind", "id", "user_id", "user_name", "avatar_url", "message", "created_at",
        "group_id", "group_name", "target_id", "target_name",
    }
    assert item["kind"] == "group_join" and item["target_id"] is None

async def test_group_search_only_returns_searchable(migrated_db):
    """搜索只返回群主开了「可被搜索」的群；关着的群连名字都查不到"""
    from sqlalchemy import text

    from app.database import async_session
    from app.repositories.search_repo import SQLAlchemySearchRepository
    from app.services.social.search_service import search_groups

    async with async_session() as db:
        await _seed(db)
        await db.execute(text("""
            INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar,
                                searchable, auto_approve_join, approve_invites)
            VALUES (2, '审批自测群（隐藏）', 'human', 1, 'default', true, false, true, false)
        """))
        await db.commit()

        repo = SQLAlchemySearchRepository(db)
        found = await search_groups(repo, "审批自测群", current_user_id=3)

        assert [g["id"] for g in found] == [GROUP_ID], "没开搜索的群不该出现"
        assert found[0]["member_count"] == 3
        assert found[0]["is_member"] is False
        assert found[0]["auto_approve_join"] is False
        await db.rollback()


async def test_group_settings_accept_discovery_flags(migrated_db):
    """PATCH 链路：三开关进得了白名单（且只有群主/管理员改得动）"""
    from app.chat.gm import update_group_settings
    from app.database import async_session

    async with async_session() as db:
        await _seed(db, auto_approve_join=True, approve_invites=False)
        await expect_value_error(
            update_group_settings(db, GROUP_ID, 3, {"searchable": True}), "仅群主或管理员")

        group = await update_group_settings(db, GROUP_ID, 5, {
            "searchable": True, "auto_approve_join": False, "approve_invites": True,
        })
        assert (group.searchable, group.auto_approve_join, group.approve_invites) == (True, False, True)
        await db.rollback()

