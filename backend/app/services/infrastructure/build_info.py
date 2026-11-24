"""构建标识与数据库迁移版本 —— 定位一次反馈所需的两个指纹。

为什么需要：`app_version` 只说明「哪一代」，不说明「哪一刻」。2026-11-25 有个实例自称
0.4.16，实际拉的是 11-22 的 1.0.0 预览 —— 同一代版本在两个快照之间差了 34 个提交，
按版本号排查会把已修的当成未修、把未修的当成已修。构建标识（commit）与迁移号（库在哪一步）
补上「哪一刻」，三者合起来才能把一次反馈对到具体代码。

取值优先级（构建标识）：
    BUILD_ID 环境变量（镜像 / CI 注入，最权威）
      → source 部署读 git（短哈希；工作区有未提交改动时追加 -dirty）
        → 空串。容器里没有 .git 是正常情况，此时只认 BUILD_ID。

两个值都在首次读取时算一次并缓存：git 是子进程、迁移号是一次数据库查询，
都不该挂在每个请求上。`/health` 会被监控高频轮询，这条尤其重要。
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_build: str | None = None
_migration: str | None = None
_migration_attempt_at: float = 0.0
# 迁移号取不到时（库没起、迁移没建表）隔一分钟再试，别让 /health 每次轮询都查一次库
_MIGRATION_RETRY_SECONDS = 60.0


def _repo_root() -> Path | None:
    """从后端目录往上找 .git；容器里（/app bind-mount）通常找不到，返回 None。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def _git_build_id() -> str:
    root = _repo_root()
    if root is None:
        return ""
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2.0, check=True,
        ).stdout.strip()
        if not head:
            return ""
        dirty = subprocess.run(
            ["git", "-C", str(root), "diff", "--quiet", "--ignore-submodules", "HEAD"],
            capture_output=True, timeout=2.0,
        ).returncode != 0
        return head + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("读取 git 构建标识失败：%s", e)
        return ""


def build_id() -> str:
    """这一次构建的标识：BUILD_ID 环境变量 → git 短哈希（+ dirty）→ 空串。"""
    global _build
    if _build is None:
        _build = (os.environ.get("BUILD_ID") or "").strip() or _git_build_id()
    return _build


async def migration_revision() -> str:
    """库当前所在的迁移版本（alembic_version.version_num）；取不到返回空串。

    成功一次就永久缓存：进程活着的时候迁移不会自己变（手工跑了迁移要重启才生效）。
    失败只缓存一分钟，避免库没起来时 /health 每轮都打一次库。
    """
    global _migration, _migration_attempt_at
    if _migration:
        return _migration
    now = time.monotonic()
    if now - _migration_attempt_at < _MIGRATION_RETRY_SECONDS:
        return ""
    _migration_attempt_at = now
    try:
        from sqlalchemy import text
        from app.database import async_session

        async with async_session() as session:
            row = (await session.execute(text("SELECT version_num FROM alembic_version"))).first()
        _migration = (row[0] or "") if row else ""
    except Exception as e:  # noqa: BLE001 —— 迁移号是诊断信息，取不到不能影响健康检查
        logger.debug("读取数据库迁移版本失败：%s", e)
        _migration = ""
    return _migration or ""


def reset_cache() -> None:
    """测试用：清掉进程内缓存。"""
    global _build, _migration, _migration_attempt_at
    _build = None
    _migration = None
    _migration_attempt_at = 0.0
