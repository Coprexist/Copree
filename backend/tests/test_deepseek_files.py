"""DeepSeek Files API 的索引与生命周期

覆盖 2026-11-25 定的四条硬口径：
1. 作用域 = endpoint + key：**不同 key 不共享**（无法判断是否同一账号）；
2. 同一作用域内按内容 sha256 去重：同一张图只传一次，多行共用一个 remote_file_id；
3. 换 key：只减**这次请求用到的图**在旧作用域的引用，别的 AI / 别的状态 / 别的图不动；
4. 卸载：某个 remote_file_id 的最后一行消失时才真的 DELETE 远端。
"""
import os
import tempfile
import time
from datetime import datetime, timedelta

import httpx
import pytest

from app.utils.pure.image_tokens import DEFAULT_IMAGE_TOKENS  # noqa: F401  确认模块可导入

pytestmark = pytest.mark.anyio

BASE = "https://api.deepseek.com"
KEY_A = "sk-aaa"
KEY_B = "sk-bbb"


def _write_image(data_dir: str, rel: str, size: int = 8) -> dict:
    path = os.path.join(data_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(bytes([size]) * 1024)
    return {"path": rel, "mime_type": "image/png", "name": os.path.basename(rel),
            "file_id": 7, "size": 1024}


class _FakeApi:
    """替掉 httpx.AsyncClient：POST /files 发 id，DELETE /files/{id} 记账。"""

    def __init__(self):
        self.uploads: list[str] = []
        self.deletes: list[str] = []
        self.fail_upload = False

    def factory(self, real_client):
        def build(**kwargs):
            return real_client(transport=httpx.MockTransport(self.handler), **kwargs)
        return build

    def handler(self, request):
        if request.method == "POST":
            if self.fail_upload:
                return httpx.Response(500, text="boom")
            fid = f"file-api-{len(self.uploads) + 1}"
            self.uploads.append(fid)
            return httpx.Response(200, json={
                "id": fid, "bytes": 1024,
                "expires_at": int(time.time()) + 7 * 24 * 3600,
            })
        if request.method == "DELETE":
            self.deletes.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(404)


async def _clean(db):
    from sqlalchemy import delete

    from app.models.deepseek_file import DeepSeekUploadFile

    await db.execute(delete(DeepSeekUploadFile))
    await db.commit()


def _patch_api(monkeypatch, fake: _FakeApi):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", fake.factory(real))


def test_file_scope_separates_keys():
    from app.services.ai.deepseek_files import file_scope

    assert file_scope(BASE, KEY_A) != file_scope(BASE, KEY_B)
    assert file_scope(BASE, KEY_A) == file_scope(BASE + "/", KEY_A), "尾部斜杠不该换作用域"


def test_supports_files_api_only_deepseek_vision_model():
    from app.services.ai.deepseek_files import supports_files_api

    assert supports_files_api(BASE, "deepseek-flash")
    assert not supports_files_api(BASE, "deepseek-v4-pro"), "只有 flash 支 vision"
    assert not supports_files_api("https://api.openai.com", "deepseek-flash")


async def test_uploads_once_then_reuses(migrated_db, monkeypatch):
    from app.database import async_session
    from app.services.ai.deepseek_files import ensure_uploaded

    fake = _FakeApi()
    _patch_api(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as data_dir:
        att = _write_image(data_dir, "attachments/a.png")
        async with async_session() as db:
            await _clean(db)
            first = await ensure_uploaded(db, agent_id=1, context_ref="group:1", base_url=BASE,
                                          api_key=KEY_A, data_dir=data_dir, att=att)
            second = await ensure_uploaded(db, agent_id=1, context_ref="group:1", base_url=BASE,
                                           api_key=KEY_A, data_dir=data_dir, att=att)
            await db.commit()

    assert first == second == "file-api-1"
    assert fake.uploads == ["file-api-1"], "同引用方同内容不该重传"


async def test_same_key_other_session_shares_one_remote_file(migrated_db, monkeypatch):
    from app.database import async_session
    from app.services.ai.deepseek_files import ensure_uploaded

    fake = _FakeApi()
    _patch_api(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as data_dir:
        att = _write_image(data_dir, "attachments/a.png")
        async with async_session() as db:
            await _clean(db)
            one = await ensure_uploaded(db, agent_id=1, context_ref="group:1", base_url=BASE,
                                        api_key=KEY_A, data_dir=data_dir, att=att)
            two = await ensure_uploaded(db, agent_id=2, context_ref="dm_x", base_url=BASE,
                                        api_key=KEY_A, data_dir=data_dir, att=att)
            await db.commit()

    assert one == two == "file-api-1", "同 key 内按内容共享"
    assert len(fake.uploads) == 1, "云端不去重，客户端必须自己去重"


async def test_switch_key_releases_only_request_images(migrated_db, monkeypatch):
    from sqlalchemy import select

    from app.database import async_session
    from app.models.deepseek_file import DeepSeekUploadFile
    from app.services.ai.deepseek_files import ensure_uploaded, release_foreign_images

    fake = _FakeApi()
    _patch_api(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as data_dir:
        a = _write_image(data_dir, "attachments/a.png", size=1)
        b = _write_image(data_dir, "attachments/b.png", size=2)
        async with async_session() as db:
            await _clean(db)
            # 旧 key 下：同一个状态传过 a 和 b，另一个 AI 也传过 a
            await ensure_uploaded(db, agent_id=1, context_ref="group:1", base_url=BASE,
                                  api_key=KEY_A, data_dir=data_dir, att=a)
            await ensure_uploaded(db, agent_id=1, context_ref="group:1", base_url=BASE,
                                  api_key=KEY_A, data_dir=data_dir, att=b)
            await ensure_uploaded(db, agent_id=2, context_ref="group:1", base_url=BASE,
                                  api_key=KEY_A, data_dir=data_dir, att=a)
            await db.commit()

            # 换 key 后这次请求只用到 a：只该减 (agent1, group:1, a) 这一行
            rows = (await db.execute(select(DeepSeekUploadFile))).scalars().all()
            assert len(rows) == 3

            from app.services.ai.deepseek_files import file_scope

            released = await release_foreign_images(
                db, agent_id=1, context_ref="group:1",
                keep_scope=file_scope(BASE, KEY_B), content_hashes=["no-such-hash"],
            )
            assert released == 0, "这次请求没用到的图不该被减"

            hash_a = [r.content_sha256 for r in rows if r.agent_id == 1 and r.local_file_id == 7][0]
            released2 = await release_foreign_images(
                db, agent_id=1, context_ref="group:1",
                keep_scope=file_scope(BASE, KEY_B), content_hashes=[hash_a],
            )
            await db.commit()
            left = (await db.execute(select(DeepSeekUploadFile))).scalars().all()

    assert released2 == 1
    # agent1 的 a 没了；agent1 的 b 和 agent2 的 a 都还在
    assert sorted((r.agent_id, r.local_file_id) for r in left) == [(1, 7), (2, 7)]


async def test_release_deletes_remote_only_when_last_ref_gone(migrated_db, monkeypatch):
    from app.database import async_session
    from app.services.ai.deepseek_files import ensure_uploaded, release_context_images

    fake = _FakeApi()
    _patch_api(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as data_dir:
        att = _write_image(data_dir, "attachments/a.png")
        async with async_session() as db:
            await _clean(db)
            await ensure_uploaded(db, agent_id=1, context_ref="group:1", base_url=BASE,
                                  api_key=KEY_A, data_dir=data_dir, att=att)
            await ensure_uploaded(db, agent_id=2, context_ref="group:9", base_url=BASE,
                                  api_key=KEY_A, data_dir=data_dir, att=att)
            await db.commit()

            # 压掉 agent1 的群 1：还有 agent2 在用 → 远端不删
            removed = await release_context_images(db, agent_id=1, context_ref="group:1",
                                                   base_url=BASE, api_key=KEY_A)
            await db.commit()
            assert removed == 1
            assert fake.deletes == [], "别人还在用，不能删远端"

            # 压掉 agent2 的群 9：最后一行 → 删远端
            removed2 = await release_context_images(db, agent_id=2, context_ref="group:9",
                                                    base_url=BASE, api_key=KEY_A)
            await db.commit()

    assert removed2 == 1
    assert fake.deletes == ["file-api-1"]


async def test_upload_failure_returns_none_for_base64_fallback(migrated_db, monkeypatch):
    from app.database import async_session
    from app.services.ai.deepseek_files import ensure_uploaded

    fake = _FakeApi()
    fake.fail_upload = True
    _patch_api(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as data_dir:
        att = _write_image(data_dir, "attachments/a.png")
        async with async_session() as db:
            await _clean(db)
            remote = await ensure_uploaded(db, agent_id=1, context_ref="group:1", base_url=BASE,
                                           api_key=KEY_A, data_dir=data_dir, att=att)
            await db.commit()

    assert remote is None, "上传失败必须让调用方回退 base64，不能把图吞掉"


def test_expiry_is_seven_days():
    from app.services.ai.deepseek_files import DEFAULT_EXPIRY_SECONDS

    assert DEFAULT_EXPIRY_SECONDS == 7 * 24 * 3600, "与 DSH 的默认有效期一致"
    assert timedelta(seconds=DEFAULT_EXPIRY_SECONDS) > timedelta(hours=1)
