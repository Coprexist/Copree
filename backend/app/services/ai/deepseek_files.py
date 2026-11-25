"""DeepSeek Files API：图片上传、按内容去重、引用方隔离与卸载。

为什么不用 base64 内联（2026-11-25 定的）：
- 内联图片计入 **48 MiB** 请求体上限，且每轮把同一张图重新编码一次；
- 官方 Files API 只需上传一次，后续请求用 `{"type":"file","file_id":...}` 引用，
  单图上限也从 32 MiB 提到 **64 MiB**，且不占请求体。

口径（官方文档 + DSH 同款实现，别在这里自创）：
- 云端**不做内容去重**：同一张图传两次 = 两个 file_id、占两份配额。去重只能自己做。
- `expires_after` 可选 3600–2592000 秒；不传是**永久**，我们不那么做——默认 7 天，
  与 DSH 的 `DEFAULT_FILE_EXPIRY_SECONDS` 一致。
- 剩余寿命低于 `REFRESH_MARGIN_SECONDS` 时不再复用，重新上传（DSH 同款）。
- 配额（每把 key 10000 个文件 / 25 GiB）满时，删该作用域下最老的 `QUOTA_CLEANUP_BATCH`
  个再重试一次；只删自己登记过的，绝不按账号盲删。

引用方隔离（用户 2026-11-25 拍定）：
- `scope = sha256(base_url | api_key)`：不同 key 一律不共享。
- 官方文档说"文件归属于你的账号：同一账号下的所有 API key 共享文件"，但我们仍按 key 隔离：
  key 来源是混的（平台池 / 用户自备 / 管理员配置），**只有 key 本身可判，没有可靠办法确认
  两把 key 属于同一账号**；而拿 A 账号的 file_id 去 B 账号请求会 404/400，一次就丢整条消息的图。
  隔离的代价只是同一张图多传一次，正确性优先。
- 一行 = 一个引用方 `(scope, content_sha256, agent_id, context_ref)`，多行共用一个
  `remote_file_id`；某个 remote 的最后一行消失时才 `DELETE` 远端。
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select

from app.models.deepseek_file import DeepSeekUploadFile
from app.utils.pure.llm_endpoint import api_root
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)

# 与 DSH 默认值对齐：7 天过期、提前 1 小时视为不可复用
DEFAULT_EXPIRY_SECONDS = 7 * 24 * 3600
REFRESH_MARGIN_SECONDS = 3600
# 配额满时一次回收多少个（DSH 默认 100）
QUOTA_CLEANUP_BATCH = 100
# Files API 单图上限（官方：图片 64 MiB）
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
UPLOAD_TIMEOUT_SECONDS = 60.0

# 只在 DeepSeek 官方端点 + 视觉模型上启用；其余通道仍走 base64。
_DEEPSEEK_HOST = "deepseek.com"
_VISION_MODEL_PREFIX = "deepseek-flash"

# 配额错误的文本特征（官方没有专用错误码，只能按 detail 判；与 DSH 的正则同口径）
_QUOTA_HINTS = ("quota", "storage", "stored files", "file count", "too many files")


class _QuotaExceeded(Exception):
    """配额满：调用方回收一批后重试。"""


def file_scope(base_url: str, api_key: str | None) -> str:
    """作用域：sha256(base_url | api_key)。不同 key 永不共享。

    官方说同一账号下所有 key 共享文件，但「是否同账号」我们无从验证（池 key / 用户自带 key
    混用）；跨账号引用 file_id 只会 400。隔离的代价是多传一次，正确性优先。
    """
    raw = f"{(base_url or '').strip().rstrip('/')}|{(api_key or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def supports_files_api(base_url: str, model: str | None) -> bool:
    """这条通道能不能用 Files API：DeepSeek 官方端点 + 视觉模型。

    其他厂商 / 本地推理没有这个端点，硬发只会 404；宁可继续 base64，
    也不要让图片在"以为上传成功"的分支里丢掉。
    """
    return _DEEPSEEK_HOST in (base_url or "") and (model or "").startswith(_VISION_MODEL_PREFIX)


def _files_url(base_url: str, suffix: str = "") -> str:
    return f"{api_root(base_url)}/files{suffix}"


def _headers(api_key: str | None) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


async def _sha256_file(path: str) -> str | None:
    """物理文件的 sha256（分块读，不整份进内存）。文件不在返回 None。"""
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# 已被厂商判定为失效的 file_id（进程内）。命中后不再复用、重新上传一次。
# 为什么是进程内集合而不是删表：作废发生在 LLM 响应之后（那里拿不到 db session），
# 而"这张图要重传"只需要影响下一次注入；表行会在下一次 ensure_uploaded 时被覆盖。
_invalidated: set[str] = set()
MAX_INVALIDATED_IDS = 4096


def invalidate(remote_file_ids: list[str]) -> None:
    """标记这些远端 file_id 已失效（厂商回 400 说 file 不存在/过期/已删）。"""
    for fid in remote_file_ids:
        if len(_invalidated) >= MAX_INVALIDATED_IDS:
            _invalidated.clear()
        _invalidated.add(fid)


def _reusable(row: DeepSeekUploadFile, now: datetime) -> bool:
    return (row.expires_at is not None
            and (row.expires_at - now).total_seconds() > REFRESH_MARGIN_SECONDS
            and row.remote_file_id not in _invalidated)


async def _find_shared(db, scope: str, content_sha256: str, now: datetime):
    """同作用域内找一份内容相同、还没到刷新余量、也没被标记失效的远端文件。

    按到期时间从晚到早扫全部候选：只看最新一条时，一条被厂商作废的行会挡住后面可用的行，
    于是每轮都重传（2026-11-25）。
    """
    result = await db.execute(
        select(DeepSeekUploadFile)
        .where(
            DeepSeekUploadFile.scope == scope,
            DeepSeekUploadFile.content_sha256 == content_sha256,
        )
        .order_by(DeepSeekUploadFile.expires_at.desc())
    )
    for candidate in result.scalars().all():
        if _reusable(candidate, now):
            return candidate
    return None


async def _upload(base_url: str, api_key: str, filename: str, data: bytes,
                  mime_type: str | None, expires_seconds: int) -> dict | None:
    """POST /files（multipart）。成功返回响应体；配额满抛 _QuotaExceeded；其余失败返回 None。"""
    try:
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                _files_url(base_url),
                data={
                    "purpose": "user_data",
                    "expires_after[anchor]": "created_at",
                    "expires_after[seconds]": str(expires_seconds),
                },
                files={"file": (filename, data, mime_type or "application/octet-stream")},
                headers=_headers(api_key),
            )
    except Exception as e:  # 网络层失败一律降级：图片不能因此消失
        logger.warning(f"🖼️ Files API 上传异常（回退 base64）: {e}")
        return None

    if resp.status_code != 200:
        detail = resp.text[:400]
        if any(hint in detail.lower() for hint in _QUOTA_HINTS):
            raise _QuotaExceeded(detail)
        logger.warning(f"🖼️ Files API 上传失败 {resp.status_code}: {detail}（回退 base64）")
        return None
    try:
        body = resp.json()
    except Exception:
        logger.warning("🖼️ Files API 返回非 JSON（回退 base64）")
        return None
    if not body.get("id"):
        logger.warning(f"🖼️ Files API 响应缺少 id（回退 base64）: {str(body)[:200]}")
        return None
    return body


async def _delete_remote(base_url: str, api_key: str, remote_file_id: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as client:
            resp = await client.delete(_files_url(base_url, f"/{remote_file_id}"),
                                       headers=_headers(api_key))
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"🖼️ Files API 删除失败（远端到期后会自然回收）{remote_file_id}: {e}")
        return False


async def _reclaim_oldest(db, base_url: str, api_key: str, scope: str) -> int:
    """配额满时：删该作用域下最老的 N 个远端文件（连同所有引用行）。

    只动自己登记过的（表里有行才删），不按账号盲删——同账号下可能有别的程序的文件。
    """
    result = await db.execute(
        select(DeepSeekUploadFile)
        .where(DeepSeekUploadFile.scope == scope)
        .order_by(DeepSeekUploadFile.created_at.asc())
    )
    victims: dict[str, str] = {}
    for row in result.scalars().all():
        if row.remote_file_id in victims:
            continue
        victims[row.remote_file_id] = row.remote_file_id
        if len(victims) >= QUOTA_CLEANUP_BATCH:
            break
    for remote_file_id in victims:
        await _delete_remote(base_url, api_key, remote_file_id)
        await db.execute(delete(DeepSeekUploadFile).where(
            DeepSeekUploadFile.scope == scope,
            DeepSeekUploadFile.remote_file_id == remote_file_id,
        ))
    await db.flush()
    if victims:
        logger.warning(f"🖼️ Files API 配额回收：删了 {len(victims)} 个最老的远端文件")
    return len(victims)


async def _upsert_ref(db, own, *, scope: str, remote_file_id: str, expires_at: datetime,
                      agent_id: int, context_ref: str, att: dict, content_sha256: str) -> str:
    """登记/更新本引用方的行，返回可用的 remote_file_id。"""
    if own is None:
        file_id = att.get("file_id")
        size = att.get("size")
        db.add(DeepSeekUploadFile(
            scope=scope,
            agent_id=agent_id,
            context_ref=context_ref,
            local_file_id=file_id if isinstance(file_id, int) else None,
            content_sha256=content_sha256,
            remote_file_id=remote_file_id,
            mime_type=att.get("mime_type"),
            bytes=size if isinstance(size, int) else None,
            expires_at=expires_at,
        ))
    else:
        own.remote_file_id = remote_file_id
        own.expires_at = expires_at
    await db.flush()
    return remote_file_id


async def release_foreign_images(
    db, *, agent_id: int, context_ref: str, keep_scope: str,
    content_hashes: list[str],
) -> int:
    """换 key 时：把**本次请求要用的所有图**在旧作用域下的引用减掉。

    用户 2026-11-25 纠正过两次口径：
    1. 一把 key 可能被多个 AI、同一个 AI 的多个状态共用 → 只减`(本 AI, 本状态)`的行，
       不能清空整个作用域；
    2. 减的是**这个请求体原本要用的所有图**，不是"恰好正在处理的那一张"。

    所以这一层是**请求级**的：`content_hashes` 是本次请求要注入的全部图片内容。
    本次请求没用到的图（本状态以前发过、但这次不在请求里）保持不动——换回来时还能直接复用。

    旧 key 的远端文件删不动（没有旧 key 明文，也无法确认是否同一账号），所以只删引用行；
    某张图如果已经没人引用，远端会在 7 天后自然过期。
    """
    if not content_hashes:
        return 0
    result = await db.execute(
        select(DeepSeekUploadFile).where(
            DeepSeekUploadFile.agent_id == agent_id,
            DeepSeekUploadFile.context_ref == context_ref,
            DeepSeekUploadFile.content_sha256.in_(content_hashes),
            DeepSeekUploadFile.scope != keep_scope,
        )
    )
    rows = result.scalars().all()
    for row in rows:
        await db.delete(row)
    if rows:
        await db.flush()
        logger.info(f"🖼️ 换 key：本次请求用到的图在旧作用域减 {len(rows)} 条（ctx={context_ref}）")
    return len(rows)


async def request_image_hashes(attachments, data_dir: str, limit: int) -> list[str]:
    """本次请求会注入的图片内容 sha256（与注入层同一口径：先取 max_images 张）。"""
    hashes: list[str] = []
    for att in list(attachments)[:limit]:
        digest = await _sha256_file(os.path.join(data_dir, att.get("path") or ""))
        if digest and digest not in hashes:
            hashes.append(digest)
    return hashes


async def ensure_uploaded(
    db,
    *,
    agent_id: int,
    context_ref: str,
    base_url: str,
    api_key: str | None,
    data_dir: str,
    att: dict,
) -> str | None:
    """取（必要时上传）这张图的远端 file_id；不适用/失败返回 None（调用方回退 base64）。

    幂等：同一引用方、同一内容重复调用只会命中已有行。
    """
    physical = os.path.join(data_dir, att.get("path") or "")
    content_sha256 = await _sha256_file(physical)
    if content_sha256 is None:
        return None

    scope = file_scope(base_url, api_key)

    now = utc_now()

    # 1) 本引用方已登记过 → 直接复用
    result = await db.execute(
        select(DeepSeekUploadFile).where(
            DeepSeekUploadFile.scope == scope,
            DeepSeekUploadFile.content_sha256 == content_sha256,
            DeepSeekUploadFile.agent_id == agent_id,
            DeepSeekUploadFile.context_ref == context_ref,
        )
    )
    own = result.scalar_one_or_none()
    if own is not None and _reusable(own, now):
        return own.remote_file_id

    # 2) 同作用域内别人传过同一份内容 → 共用它，不再上传
    shared = await _find_shared(db, scope, content_sha256, now)
    if shared is not None:
        return await _upsert_ref(db, own, scope=scope, remote_file_id=shared.remote_file_id,
                                 expires_at=shared.expires_at, agent_id=agent_id,
                                 context_ref=context_ref, att=att,
                                 content_sha256=content_sha256)

    # 3) 真上传
    try:
        with open(physical, "rb") as f:
            data = f.read()
    except Exception as e:
        logger.warning(f"🖼️ 读取图片失败（回退 base64）: {e}")
        return None
    if len(data) > MAX_UPLOAD_BYTES:
        logger.warning(f"🖼️ 图片超过 Files API 上限（{len(data)} > {MAX_UPLOAD_BYTES}），回退 base64")
        return None

    filename = att.get("name") or os.path.basename(physical) or "image"
    try:
        uploaded = await _upload(base_url, api_key or "", filename, data,
                                 att.get("mime_type"), DEFAULT_EXPIRY_SECONDS)
    except _QuotaExceeded:
        if await _reclaim_oldest(db, base_url, api_key or "", scope) == 0:
            logger.warning("🖼️ Files API 配额满且无可回收文件（回退 base64）")
            return None
        try:
            uploaded = await _upload(base_url, api_key or "", filename, data,
                                     att.get("mime_type"), DEFAULT_EXPIRY_SECONDS)
        except _QuotaExceeded:
            logger.warning("🖼️ Files API 回收后仍配额不足（回退 base64）")
            return None
    if uploaded is None:
        return None

    raw_expires = uploaded.get("expires_at")
    expires_at = (datetime.fromtimestamp(int(raw_expires), tz=timezone.utc).replace(tzinfo=None)
                  if raw_expires else now)
    return await _upsert_ref(db, own, scope=scope, remote_file_id=uploaded["id"],
                             expires_at=expires_at, agent_id=agent_id, context_ref=context_ref,
                             att=att, content_sha256=content_sha256)


async def release_context_images(
    db, *, agent_id: int, context_ref: str, base_url: str, api_key: str | None,
) -> int:
    """压缩后卸载：删掉该会话登记的图片行；某个远端文件的最后一行消失时才真删远端。

    不按作用域过滤：会话可能中途换过 key，旧 key 的行也得一起减掉（否则它们永远
    没人碰，只能等 7 天过期）。但远端删除只用**当前 key**——旧作用域的文件多半
    删不动（我们没有旧 key），删失败无害，到期会自然回收。

    返回删掉的引用行数（不等于删掉的远端文件数——多行可能共用一个文件）。
    """
    current_scope = file_scope(base_url, api_key)
    result = await db.execute(
        select(DeepSeekUploadFile).where(
            DeepSeekUploadFile.agent_id == agent_id,
            DeepSeekUploadFile.context_ref == context_ref,
        )
    )
    rows = result.scalars().all()
    if not rows:
        return 0

    removed = 0
    for row in rows:
        remote_file_id = row.remote_file_id
        row_scope = row.scope
        await db.delete(row)
        await db.flush()
        removed += 1
        left = await db.execute(
            select(DeepSeekUploadFile.id).where(
                DeepSeekUploadFile.scope == row_scope,
                DeepSeekUploadFile.remote_file_id == remote_file_id,
            ).limit(1)
        )
        if left.scalar_one_or_none() is None and row_scope == current_scope:
            await _delete_remote(base_url, api_key or "", remote_file_id)
    logger.info(f"🖼️ 上下文压缩后卸载图片引用 {removed} 条（ctx={context_ref}）")
    return removed
