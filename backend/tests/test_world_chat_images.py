"""群视界发图链路：调用真实的 _prepare_world_chat，不产生 LLM 请求。

覆盖 HTTP 入参 -> ChatItem -> 落库 -> LLM payload 的完整装配。2026-09-13 的线上事故
出在 image_attachments()：它迭代了契约允许为 None 的返回值，仅当历史中存在无附件的
消息时触发。纯函数级测试覆盖不到这条路径。

不变量：
- 无附件消息不得抛异常（含历史非空的情况）；
- 带图消息只有最后一条携带真实 data URL，其余降级为 [图片]；
- 「本轮附图」便签数量等于实际注入数，无图时不得出现。
"""
from __future__ import annotations

import base64
import contextlib
import os
import shutil
import tempfile

import pytest

from app.utils.multimodal import (
    IMAGE_NOTE_PREFIX,
    VISION_UNSUPPORTED_HINT,
    injected_image_count,
    messages_have_images,
    strip_image_parts,
)

pytestmark = pytest.mark.anyio

USER_ID = 9001

# 1x1 透明 PNG
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@contextlib.contextmanager
def _temp_data_dir():
    """把 settings.data_dir 指向临时目录。

    _prepare_world_chat 从 settings.data_dir 读附件。写真实数据目录会污染生产数据，
    且在 CI 上不可写（/app 属另一用户，PermissionError）。data_dir 是只读 property，
    因此替换类上的描述符，退出时还原。
    """
    from app.config import settings

    tmp = tempfile.mkdtemp(prefix="world-chat-test-")
    settings_cls = type(settings)
    original = settings_cls.data_dir
    settings_cls.data_dir = property(lambda self: tmp)
    try:
        yield tmp
    finally:
        settings_cls.data_dir = original
        shutil.rmtree(tmp, ignore_errors=True)


async def _seed_world(db, *, with_image: bool) -> tuple[int, dict]:
    """建一个临时世界；with_image 时再落一张真实图片文件。返回 (world_id, 附件)。"""
    from sqlalchemy import text

    from app.config import settings
    from app.models.user import User
    from app.models.world import World

    from db_reset import clear
    await clear(db, "worlds", "users")
    await db.commit()

    db.add(User(id=USER_ID, username="smoke-image", password_hash="x", type="human"))
    world = World(name="test-world", owner_id=USER_ID)
    db.add(world)
    await db.commit()
    await db.refresh(world)

    attachment: dict = {}
    if with_image:
        rel = "1px.png"
        with open(os.path.join(settings.data_dir, rel), "wb") as fh:
            fh.write(PNG_1PX)
        attachment = {
            "file_id": 1, "path": rel, "name": "1px.png",
            "size": len(PNG_1PX), "mime_type": "image/png",
        }
    return world.id, attachment


async def _prepare(db, world_id: int, items: list):
    """走真实链路。_prepare_world_chat 是 stream_world_chat 的准备阶段。"""
    from app.repositories.world_repo import SQLAlchemyWorldRepository
    from app.services.world.world_chat_service import _prepare_world_chat

    return await _prepare_world_chat(SQLAlchemyWorldRepository(db), world_id, USER_ID, items)


def _last_user(messages: list[dict]) -> dict:
    """最后一条 user 消息。尾部还有时间/访客等 system 段，不能取 messages[-1]。"""
    return [m for m in messages if m.get("role") == "user"][-1]


def _notes(messages: list[dict]) -> list[dict]:
    """尾部「本轮附图」便签。"""
    return [
        m for m in messages
        if isinstance(m.get("content"), str) and m["content"].startswith(IMAGE_NOTE_PREFIX)
    ]


async def test_plain_text_turn_survives_the_image_path(migrated_db):
    """无附件消息在历史非空时不得抛异常。

    首轮历史为空，None 不会出现，必须跑到第二轮才能覆盖该分支。
    """
    from app.database import async_session
    from app.services.world.world_chat_items import ChatItem

    with _temp_data_dir():
        async with async_session() as db:
            world_id, _ = await _seed_world(db, with_image=False)
            await _prepare(db, world_id, [ChatItem(text="第一轮")])
            ctx = await _prepare(db, world_id, [ChatItem(text="你好")])

    messages = ctx["messages"]
    assert any("第一轮" in str(m.get("content")) for m in messages), "第二轮未带上历史"
    assert _last_user(messages)["content"] == "你好"
    assert not messages_have_images(messages)
    assert _notes(messages) == []
    assert ctx["cmd_text"] == "你好"


async def test_image_turn_injects_multimodal_parts_and_note(migrated_db):
    """带图消息生成多模态 parts，便签数量等于实际注入数。"""
    from app.database import async_session
    from app.services.world.world_chat_items import ChatItem

    with _temp_data_dir():
        async with async_session() as db:
            world_id, attachment = await _seed_world(db, with_image=True)
            ctx = await _prepare(
                db, world_id,
                [ChatItem(text="这是什么？", attachments=(attachment,))],
            )

    messages = ctx["messages"]
    body = _last_user(messages)
    assert isinstance(body["content"], list), "图片被丢弃，content 应为 parts 列表"
    assert body["content"][0] == {"type": "text", "text": "这是什么？"}
    urls = [p["image_url"]["url"] for p in body["content"] if p.get("type") == "image_url"]
    assert len(urls) == 1
    assert urls[0].startswith("data:image/png;base64,")

    assert injected_image_count(body["content"]) == 1
    notes = _notes(messages)
    assert len(notes) == 1, "缺少「本轮附图」便签"
    assert "1 张图片" in notes[0]["content"], "便签数量应为实际注入数"
    assert ctx["cmd_text"] == ""


async def test_image_turn_persists_attachments(migrated_db):
    """附件随消息落库，前端刷新后据此渲染缩略图。"""
    from sqlalchemy import select

    from app.database import async_session
    from app.models.world import WorldChatMessage
    from app.services.world.world_chat_items import ChatItem

    with _temp_data_dir():
        async with async_session() as db:
            world_id, attachment = await _seed_world(db, with_image=True)
            await _prepare(db, world_id, [ChatItem(text="看图", attachments=(attachment,))])
            rows = (await db.execute(
                select(WorldChatMessage).where(WorldChatMessage.world_id == world_id)
            )).scalars().all()

    stored = [r for r in rows if r.role == "user"][-1]
    assert stored.content == "看图"
    assert stored.attachments and stored.attachments[0]["path"] == attachment["path"]


async def test_history_image_degrades_to_placeholder(migrated_db):
    """历史中的图片降级为 [图片]，只有最新一条携带字节。"""
    from app.database import async_session
    from app.services.world.world_chat_items import ChatItem

    with _temp_data_dir():
        async with async_session() as db:
            world_id, attachment = await _seed_world(db, with_image=True)
            await _prepare(
                db, world_id,
                [ChatItem(text="这是什么？", attachments=(attachment,))],
            )
            ctx = await _prepare(db, world_id, [ChatItem(text="谢谢")])

    messages = ctx["messages"]
    assert any("[图片]" in str(m.get("content")) for m in messages), "历史缺少 [图片] 占位"
    assert not messages_have_images(messages), "历史消息不得携带字节"
    assert _notes(messages) == []


async def test_vision_degrade_strips_images_and_note_together(migrated_db):
    """降级时图片与便签必须同时移除。

    只剥图片会留下"你可以直接查看"，对纯文本模型构成误导。
    """
    from app.database import async_session
    from app.services.world.world_chat_items import ChatItem

    with _temp_data_dir():
        async with async_session() as db:
            world_id, attachment = await _seed_world(db, with_image=True)
            ctx = await _prepare(
                db, world_id,
                [ChatItem(text="这是什么？", attachments=(attachment,))],
            )

    degraded, removed = strip_image_parts(ctx["messages"])
    assert removed == 1
    assert not messages_have_images(degraded)
    assert _notes(degraded) == []
    assert any(VISION_UNSUPPORTED_HINT in str(m.get("content")) for m in degraded)
