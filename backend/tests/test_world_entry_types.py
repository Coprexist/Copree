"""入口类型：群聊类型与 AI 角色分开（applies_to）。

守的是「配置层悄悄走形」的四件事：
1. 旧数据没有 applies_to → 按群聊类型（历史上类型只有这一个含义），不会把群类型当角色列出来
2. 入口列表按适用对象过滤：群聊只给 group/both，AI 只给 ai/both，both 两边都在；不传 = 全部
3. 返回形状恒定：群/AI 两侧占用（bound_groups / bound_ais）永远都给，调用方不按参数猜字段
4. 服务端同样校验：拿群聊类型去绑 AI（或反之）直接拒——前端藏起来不算规则

语义见 docs/group_world/design/group_world_design.md §8.5。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager

import pytest

from app.services.world.group_type_service import (
    APPLIES_LABEL,
    GROUP_TYPES_FILE,
    applies_to_of,
    bind_entry_with_type,
    list_group_types,
    save_group_types_config,
    serves,
)

pytestmark = pytest.mark.anyio

WORLD = 987650002          # 探针世界号：只在这个文件里建目录，用例结束删干净
OWNER = 1
AI_USER = 40

_TYPES = {"types": [
    {"slug": "guild", "name": "冒险团", "applies_to": "group", "bind_limit": -1},
    {"slug": "referee", "name": "裁判", "applies_to": "ai", "bind_limit": 1},
    {"slug": "resident", "name": "居民", "applies_to": "both", "bind_limit": -1},
    {"slug": "legacy", "name": "旧数据类型", "bind_limit": 3},          # 没有 applies_to
]}


@contextmanager
def _types_root():
    """临时世界根目录（WORLD_TYPES_ROOT 覆盖）；退出时清环境变量与目录。"""
    root = tempfile.mkdtemp(prefix="copree-types-")
    os.environ["WORLD_TYPES_ROOT"] = root
    try:
        yield root
    finally:
        os.environ.pop("WORLD_TYPES_ROOT", None)
        shutil.rmtree(root, ignore_errors=True)


async def _seed(db, root: str) -> None:
    """世界 + 号主 + 一个 AI，类型文件写进临时根目录。"""
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "world_bindings", "agents", "worlds", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (40, '裁判AI', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES (24, 1, '裁判AI', 40, true)"))
    await db.execute(text("INSERT INTO worlds (id, name, owner_id) VALUES (:w, '入口类型测试世界', 1)"),
                     {"w": WORLD})
    await db.commit()

    world_dir = os.path.join(root, str(WORLD))
    os.makedirs(world_dir, exist_ok=True)
    with open(os.path.join(world_dir, GROUP_TYPES_FILE), "w", encoding="utf-8") as f:
        json.dump(_TYPES, f, ensure_ascii=False)


def _read_types(root: str) -> dict:
    with open(os.path.join(root, str(WORLD), GROUP_TYPES_FILE), encoding="utf-8") as f:
        return json.load(f)


def test_legacy_config_counts_as_group_type():
    """没有 applies_to 的旧配置按群聊类型；认不出的值也不许变成"两边都能用"。"""
    assert applies_to_of({}) == "group"
    assert applies_to_of({"applies_to": "ai"}) == "ai"
    assert applies_to_of({"applies_to": "whatever"}) == "group"
    assert serves({"applies_to": "ai"}, "agent") and not serves({"applies_to": "ai"}, "group")
    assert serves({}, "group") and not serves({}, "agent")
    assert serves({"applies_to": "both"}, "group") and serves({"applies_to": "both"}, "agent")
    assert set(APPLIES_LABEL.values()) == {"群聊类型", "AI 角色", "两者"}


def test_prompt_and_tool_state_the_two_kinds():
    """提示词与工具描述必须写明 applies_to 与「角色不是头衔」——否则又写出一批"宿敌"式的角色"""
    from app.services.world.world_chat_service import FORCED_PROMPT_SEGMENTS
    from app.tools.world.get_group_types import GetGroupTypesTool
    from app.tools.world.update_group_types import UpdateGroupTypesTool

    prompt = "".join(FORCED_PROMPT_SEGMENTS)
    assert "applies_to" in prompt and "AI 角色" in prompt and "头衔" in prompt
    for tool in (GetGroupTypesTool, UpdateGroupTypesTool):
        assert "AI 角色" in tool.description, tool.name
    assert "applies_to" in UpdateGroupTypesTool.description
    # 入口参数只有两个真值；「全部」由不传表达，不再往枚举里塞 all
    assert GetGroupTypesTool.parameters["entity_type"]["enum"] == ["group", "agent"]


async def test_lists_are_split_by_entry(migrated_db):
    """群聊 / AI 拿到两份列表；both 两边都在、legacy 只算群聊；返回形状恒定"""
    from app.database import async_session

    with _types_root() as root:
        async with async_session() as db:
            await _seed(db, root)
            groups = await list_group_types(db, WORLD, entity_type="group")
            ais = await list_group_types(db, WORLD, entity_type="agent")
            everything = await list_group_types(db, WORLD)

        assert [t["slug"] for t in groups] == ["guild", "resident", "legacy"]
        assert [t["slug"] for t in ais] == ["referee", "resident"]
        assert {t["slug"] for t in everything} == {"guild", "referee", "resident", "legacy"}
        # 旧数据不写字段也知道自己是谁
        assert next(t for t in groups if t["slug"] == "legacy")["applies_to"] == "group"
        # 三种查法同一种形状：两侧占用都在，调用方不用按参数猜字段
        for t in groups + ais + everything:
            assert "bound_groups" in t and "bound_ais" in t, t
            assert "bound_count" not in t, t


async def _expect_value_error(awaitable, match: str) -> None:
    """断言抛出含 match 的 ValueError（容器里没有 pytest.raises，见 test_group_join_approval.py）"""
    try:
        await awaitable
    except ValueError as e:
        assert match in str(e), f"错误文案不含「{match}」：{e}"
        return
    raise AssertionError(f"预期抛 ValueError（含「{match}」），实际没有抛")


async def test_bind_rejects_type_for_the_other_entry(migrated_db):
    """服务端把关：选错适用对象直接拒（前端藏起来不算规则）"""
    from app.database import async_session

    with _types_root() as root:
        async with async_session() as db:
            await _seed(db, root)
            await _expect_value_error(
                bind_entry_with_type(db, WORLD, OWNER, "agent", AI_USER, "guild"), "群聊类型")
            await _expect_value_error(
                bind_entry_with_type(db, WORLD, OWNER, "group", 7, "referee"), "AI 角色")


async def test_saved_types_keep_audience(migrated_db):
    """保存一次就把适用对象落进文件；新类型不写默认群聊，不会偷偷变成两边都能用"""
    from app.database import async_session

    with _types_root() as root:
        async with async_session() as db:
            await _seed(db, root)
            await save_group_types_config(db, WORLD, OWNER, [
                {"name": "店员", "applies_to": "ai", "bind_limit": 2},
                {"name": "商会", "bind_limit": 3},
            ])

        by_name = {t["name"]: t for t in _read_types(root)["types"]}
        assert set(by_name) == {"店员", "商会"}
        assert by_name["店员"]["applies_to"] == "ai"
        assert by_name["商会"]["applies_to"] == "group"
