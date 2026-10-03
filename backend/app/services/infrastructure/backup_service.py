"""
数据库备份/恢复服务 — 策略模式

支持 PostgreSQL（pg_dump / psql）和 SQLite（文件复制）两种后端，
通过 DatabaseBackupBackend 抽象接口统一调用方式。

新增后端只需：
  1. 继承 DatabaseBackupBackend 并实现抽象方法
  2. 在 _register_backends() 中登记
"""
import asyncio
import gzip
import io
import logging
import os
import shutil
import tarfile
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.paths import BACKUPS_DIR

logger = logging.getLogger(__name__)

BACKUP_TIMEOUT = 120   # 备份超时（秒）
RESTORE_TIMEOUT = 300  # 恢复超时（秒）


# ═══════════════════════════════════════════════════════════════
# 1. 抽象接口
# ═══════════════════════════════════════════════════════════════

class DatabaseBackupBackend(ABC):
    """数据库备份/恢复后端抽象接口"""

    @property
    @abstractmethod
    def name(self) -> str:
        """后端标识，如 'postgres' / 'sqlite'"""
        ...

    @property
    @abstractmethod
    def backup_extension(self) -> str:
        """备份文件扩展名，如 '.sql' / '.db'"""
        ...

    @abstractmethod
    async def create_backup(self) -> bytes:
        """执行备份，返回备份数据的原始字节"""
        ...

    @abstractmethod
    async def restore_backup(self, data: bytes) -> dict:
        """从备份数据恢复，返回结果字典。⚠️ 会覆盖当前所有数据。"""
        ...

    def describe(self) -> dict:
        """返回后端描述信息（供前端展示）"""
        return {
            "name": self.name,
            "backup_extension": self.backup_extension,
        }


# ═══════════════════════════════════════════════════════════════
# 2. PostgreSQL 后端
# ═══════════════════════════════════════════════════════════════

# PostgreSQL 17+ 新增参数，在 PG16 及以下版本中不存在
_PG17_PLUS_SETTINGS = [
    b"SET transaction_timeout",
]


def _filter_pg_version_specific(sql: bytes) -> bytes:
    """从 pg_dump 输出中移除高版本 PG 专有的 SET 语句"""
    lines = sql.split(b"\n")
    filtered = [
        line for line in lines
        if not any(line.strip().startswith(s) for s in _PG17_PLUS_SETTINGS)
    ]
    return b"\n".join(filtered)


def _parse_pg_url(db_url: str) -> dict:
    """解析 postgresql:// URL 为连接参数"""
    url = db_url.replace("postgresql://", "")
    auth_host, dbname = url.rsplit("/", 1)
    user_pass, host_port = auth_host.rsplit("@", 1)
    user, password = user_pass.split(":", 1)
    host, port = host_port.split(":", 1) if ":" in host_port else (host_port, "5432")
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "dbname": dbname,
    }


class PostgresBackupBackend(DatabaseBackupBackend):
    """PostgreSQL 备份后端 — 使用 pg_dump / psql"""

    @property
    def name(self) -> str:
        return "postgres"

    @property
    def backup_extension(self) -> str:
        return ".sql"

    async def create_backup(self) -> bytes:
        """
        执行 pg_dump，导出整个数据库为 SQL 字节。
        超时 120 秒。
        """
        params = _parse_pg_url(settings.database_url_sync)

        cmd = [
            "pg_dump",
            "-h", params["host"],
            "-p", params["port"],
            "-U", params["user"],
            "-d", params["dbname"],
            "--no-owner",
            "--no-acl",
            "--clean",
            "--if-exists",
            "--encoding=UTF8",
        ]

        logger.info(f"[postgres] 开始备份数据库: {params['dbname']}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PGPASSWORD": params["password"]},
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=BACKUP_TIMEOUT
            )

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace")
                logger.error(f"[postgres] pg_dump 失败 (code={proc.returncode}): {err_msg}")
                raise RuntimeError(f"数据库备份失败: {err_msg[:500]}")

            sql_bytes = _filter_pg_version_specific(stdout)
            logger.info(f"[postgres] 数据库备份完成: {len(sql_bytes)} bytes")
            return sql_bytes

        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("数据库备份超时（超过 120 秒）")
        except FileNotFoundError:
            raise RuntimeError("pg_dump 未安装，请检查 postgresql-client 是否已安装")

    async def restore_backup(self, sql_content: bytes) -> dict:
        """
        执行 psql 恢复数据库。
        ⚠️ 此操作会覆盖当前数据库所有数据。
        超时 300 秒。
        """
        params = _parse_pg_url(settings.database_url_sync)
        _env = {**os.environ, "PGPASSWORD": params["password"]}

        # ---- 第一步：清空 public schema ----
        logger.info(f"[postgres] 恢复前清空数据库 public schema: {params['dbname']}")
        clean_proc = await asyncio.create_subprocess_exec(
            "psql",
            "-h", params["host"], "-p", params["port"],
            "-U", params["user"], "-d", params["dbname"],
            "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_env,
        )
        clean_stdout, clean_stderr = await clean_proc.communicate()
        if clean_proc.returncode != 0:
            err = clean_stderr.decode("utf-8", errors="replace")
            logger.warning(f"[postgres] 清空 schema 警告（可能是空库）: {err[:200]}")

        # ---- 第二步：执行恢复 ----
        sql_content = _filter_pg_version_specific(sql_content)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".sql", delete=False, prefix="copree_restore_"
            ) as f:
                f.write(sql_content)
                tmp_path = f.name

            cmd = [
                "psql",
                "-h", params["host"],
                "-p", params["port"],
                "-U", params["user"],
                "-d", params["dbname"],
                "-f", tmp_path,
                "-v", "ON_ERROR_STOP=1",
            ]

            logger.info(f"[postgres] 开始恢复数据库: {params['dbname']}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=RESTORE_TIMEOUT
            )

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace")
                logger.error(f"[postgres] psql 恢复失败 (code={proc.returncode}): {err_msg}")
                raise RuntimeError(f"数据库恢复失败: {err_msg[:500]}")

            logger.info("[postgres] 数据库恢复完成")
            return {"success": True, "message": "数据库已恢复，请刷新页面"}

        except asyncio.TimeoutError:
            raise RuntimeError("数据库恢复超时（超过 300 秒）")
        except FileNotFoundError:
            raise RuntimeError("psql 未安装，请检查 postgresql-client 是否已安装")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════
# 3. SQLite 后端
# ═══════════════════════════════════════════════════════════════

class SQLiteBackupBackend(DatabaseBackupBackend):
    """SQLite 备份后端 — 直接复制数据库文件"""

    @property
    def name(self) -> str:
        return "sqlite"

    @property
    def backup_extension(self) -> str:
        return ".db"

    def _db_path(self) -> str:
        """获取 SQLite 数据库文件的绝对路径（相对路径基于 settings.data_dir 解析）"""
        path = Path(settings.sqlite_db_path)
        if not path.is_absolute():
            path = Path(settings.data_dir) / path
        return str(path.resolve())

    def _wal_path(self) -> str:
        """WAL journal 文件路径"""
        return self._db_path() + "-wal"

    def _shm_path(self) -> str:
        """SHM 文件路径"""
        return self._db_path() + "-shm"

    async def create_backup(self) -> bytes:
        """
        备份 SQLite 数据库文件。
        使用 SQLite 的 backup API（在线安全备份），不需要停服。
        备份前执行 WAL checkpoint 确保所有数据落盘。
        """
        db_path = self._db_path()

        if not os.path.exists(db_path):
            raise RuntimeError(f"SQLite 数据库文件不存在: {db_path}")

        logger.info(f"[sqlite] 开始备份数据库: {db_path}")

        try:
            import sqlite3

            def _do_backup():
                src = sqlite3.connect(db_path)
                # WAL checkpoint: 将 WAL 日志合并到主文件，确保备份包含所有数据
                src.execute("PRAGMA wal_checkpoint(FULL)")
                dst_path = db_path + ".backup_tmp"
                dst = sqlite3.connect(dst_path)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
                return dst_path

            dst_path = await asyncio.to_thread(_do_backup)

            try:
                with open(dst_path, "rb") as f:
                    data = f.read()
                logger.info(f"[sqlite] 数据库备份完成: {len(data)} bytes")
                return data
            finally:
                if os.path.exists(dst_path):
                    os.unlink(dst_path)

        except Exception as e:
            if isinstance(e, (RuntimeError, OSError)):
                raise
            raise RuntimeError(f"SQLite 备份失败: {str(e)}")

    async def restore_backup(self, data: bytes) -> dict:
        """
        从备份数据恢复 SQLite 数据库。
        ⚠️ 会直接替换数据库文件，覆盖当前所有数据。
        恢复后需要重启应用，因为 SQLAlchemy 连接仍持有旧文件句柄。
        """
        db_path = self._db_path()

        logger.info(f"[sqlite] 开始恢复数据库: {db_path}")

        try:
            # 确保目标目录存在
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

            def _do_restore():
                # 先写到临时文件再原子替换
                tmp_path = db_path + ".restore_tmp"
                with open(tmp_path, "wb") as f:
                    f.write(data)

                # 验证文件是有效的 SQLite 且内容完整
                import sqlite3
                conn = sqlite3.connect(tmp_path)
                result = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if result[0] != "ok":
                    raise RuntimeError(f"SQLite 备份文件完整性校验失败: {result[0]}")

                # 原子替换
                shutil.move(tmp_path, db_path)

                # 删除 WAL/SHM 残留（旧的 WAL 可能指向已不存在的数据库）
                for sidecar in (db_path + "-wal", db_path + "-shm", db_path + "-journal"):
                    if os.path.exists(sidecar):
                        os.unlink(sidecar)
                        logger.info(f"[sqlite] 已清理残留文件: {sidecar}")

            await asyncio.to_thread(_do_restore)

            logger.info("[sqlite] 数据库恢复完成")
            return {
                "success": True,
                "message": "数据库已恢复，请重启应用生效",
                "restart_required": True,
            }

        except Exception as e:
            # 清理可能的临时文件
            tmp = db_path + ".restore_tmp"
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            if isinstance(e, (RuntimeError, OSError)):
                raise
            raise RuntimeError(f"SQLite 恢复失败: {str(e)}")


# ═══════════════════════════════════════════════════════════════
# 4. 注册表 + 工厂函数
# ═══════════════════════════════════════════════════════════════

_BACKENDS: dict[str, DatabaseBackupBackend] = {}


def _register_backends():
    """延迟注册所有备份后端"""
    if _BACKENDS:
        return
    _BACKENDS["postgres"] = PostgresBackupBackend()
    _BACKENDS["sqlite"] = SQLiteBackupBackend()


def get_backup_backend(name: str | None = None) -> DatabaseBackupBackend:
    """获取当前生效的备份后端。未指定时按 settings.db_backend 选择。"""
    _register_backends()
    selected = (name or settings.db_backend or "postgres").lower()
    backend = _BACKENDS.get(selected)
    if backend is None:
        raise RuntimeError(
            f"不支持的数据库后端: '{selected}'"
            f"（已知后端: {', '.join(sorted(_BACKENDS.keys()))}）"
        )
    return backend


def get_backup_info() -> dict:
    """返回当前备份后端信息（供前端展示）"""
    backend = get_backup_backend()
    return {
        "db_backend": backend.name,
        "backup_extension": backend.backup_extension,
        "warning": (
            "备份文件仅可用于相同数据库类型的恢复。"
            "例如 PostgreSQL 备份无法用于 SQLite，反之亦然。"
        ),
    }


# ═══════════════════════════════════════════════════════════════
# 5. 上层服务（只依赖接口）
# ═══════════════════════════════════════════════════════════════

async def create_backup() -> bytes:
    """创建数据库备份（自动选择当前后端）"""
    backend = get_backup_backend()
    return await backend.create_backup()


async def restore_backup(data: bytes) -> dict:
    """恢复数据库备份（自动选择当前后端）"""
    backend = get_backup_backend()
    return await backend.restore_backup(data)


# ═══════════════════════════════════════════════════════════════
# 6. 完整备份（数据库 + 文件）
# ═══════════════════════════════════════════════════════════════

async def create_full_backup() -> tuple[bytes, int, int]:
    """
    创建完整备份（.tar.gz）：
    - 包含数据库备份文件（.sql 或 .db）
    - 包含 /app/data/ 目录下所有文件
    返回 (tar_bytes, db_size, file_count)
    """
    backend = get_backup_backend()
    db_ext = backend.backup_extension  # ".sql" or ".db"
    inner_name = f"backup{db_ext}"     # "backup.sql" or "backup.db"

    logger.info("开始创建完整备份...")

    # 1. 先备份数据库
    db_bytes = await backend.create_backup()
    db_size = len(db_bytes)
    logger.info(f"数据库导出完成: {db_size} bytes ({backend.name})")

    # 2. 打包为 tar.gz
    data_dir = settings.data_dir
    buf = io.BytesIO()
    file_count = 0

    try:
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # 添加数据库备份
            db_info = tarfile.TarInfo(name=inner_name)
            db_info.size = len(db_bytes)
            tar.addfile(db_info, io.BytesIO(db_bytes))
            logger.info(f"  ✅ {inner_name} 已打包")

            # 添加 data/ 目录下用户数据文件（跳过数据库数据目录）
            if os.path.isdir(data_dir):
                SKIP_DIRS = {'postgres', 'pgdata', 'mysql', 'mariadb'}
                for root, dirs, files in os.walk(data_dir):
                    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        try:
                            arcname = os.path.join("data", os.path.relpath(fpath, data_dir))
                            arcname = arcname.replace("\\", "/")
                            tar.add(fpath, arcname=arcname)
                            file_count += 1
                        except FileNotFoundError:
                            logger.warning(f"跳过已消失的文件: {fpath}")
            logger.info(f"  ✅ {file_count} 个文件已打包")

    except Exception as e:
        logger.error(f"创建完整备份失败: {e}")
        raise RuntimeError(f"打包备份失败: {str(e)}")

    tar_bytes = buf.getvalue()
    logger.info(f"完整备份创建完成: {len(tar_bytes)} bytes (DB={db_size}, files={file_count})")
    return tar_bytes, db_size, file_count


async def restore_full_backup(tar_bytes: bytes) -> dict:
    """
    从完整备份 .tar.gz 恢复：
    - 从 backup.sql / backup.db 恢复数据库
    - 将所有 data/ 下的文件还原到 /app/data/
    ⚠️ 覆盖当前所有数据
    """
    data_dir = settings.data_dir
    db_bytes = None
    restored_files = 0

    # 已知的数据库备份内部文件名（兼容旧格式 backup.sql 和新格式 backup.db）
    DB_INNER_NAMES = {"backup.sql", "backup.db"}

    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name in DB_INNER_NAMES:
                    # 提取数据库备份
                    f = tar.extractfile(member)
                    if f:
                        db_bytes = f.read()
                    logger.info(
                        f"  📄 读取 {member.name}: {len(db_bytes)} bytes"
                        if db_bytes
                        else f"  ❌ {member.name} 为空"
                    )
                elif member.name.startswith("data/"):
                    # 提取文件到 /app/data/
                    rel_path = member.name[5:]  # 去掉 "data/" 前缀
                    dest = os.path.join(data_dir, rel_path)
                    # 安全：拒绝绝对路径和目录穿越
                    dest = os.path.normpath(dest)
                    if not dest.startswith(os.path.normpath(data_dir)):
                        logger.warning(f"  ⚠️ 拒绝不安全路径: {member.name} → {dest}")
                        continue
                    if member.isdir():
                        os.makedirs(dest, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        f = tar.extractfile(member)
                        if f:
                            with open(dest, "wb") as out:
                                shutil.copyfileobj(f, out)
                            restored_files += 1

        if db_bytes is None:
            raise RuntimeError("备份文件中未找到数据库备份（backup.sql / backup.db），无法恢复")

        logger.info(f"文件还原完成: {restored_files} 个文件")
        logger.info("开始恢复数据库...")
        result = await restore_backup(db_bytes)
        logger.info("完整备份恢复完成")

        resp = {
            "success": True,
            "message": f"完整备份已恢复：数据库 + {restored_files} 个文件",
            "restored_files": restored_files,
        }
        if result.get("restart_required"):
            resp["restart_required"] = True
            resp["message"] = f"完整备份已恢复：数据库 + {restored_files} 个文件，请重启应用生效"
        return resp

    except tarfile.ReadError as e:
        raise RuntimeError(f"无法读取备份文件（可能已损坏或不是 .tar.gz 格式）: {str(e)}")
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"完整备份恢复失败: {e}", exc_info=True)
        raise RuntimeError(f"恢复失败: {str(e)}")


# ═══════════════════════════════════════════════════════════════
# 7. 每日自动备份（管理员开关 + 保留份数，超出自动清除）
# ═══════════════════════════════════════════════════════════════

BACKUP_DIR = BACKUPS_DIR


def _ensure_backup_dir() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


# 改名前的备份前缀（aischat_*）：历史备份继续能被列出、清理、回档
BACKUP_PREFIXES = ("copree_", "aischat_")


def _backup_glob_pattern() -> str:
    """根据当前后端返回匹配的文件 glob 模式（两种品牌前缀都匹配）"""
    backend = get_backup_backend()
    ext = backend.backup_extension  # ".sql" or ".db"
    return f"*{ext}.gz"


def _is_backup_name(name: str, ext: str) -> bool:
    return name.startswith(BACKUP_PREFIXES) and name.endswith(f"{ext}.gz")


def list_backup_files() -> list[Path]:
    """列出本机备份文件（按时间倒序，兼容两种后端的文件名）"""
    if not BACKUP_DIR.exists():
        return []
    # 合并两种后端、两种品牌前缀的备份文件
    sql_files = sorted((p for p in BACKUP_DIR.glob("*.sql.gz") if _is_backup_name(p.name, ".sql")), reverse=True)
    db_files = sorted((p for p in BACKUP_DIR.glob("*.db.gz") if _is_backup_name(p.name, ".db")), reverse=True)
    # 按修改时间混合排序
    all_files = sql_files + db_files
    all_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return all_files


async def save_backup(db_bytes: bytes) -> str:
    """将备份落盘（gzip 压缩），返回文件名。根据后端自动选择扩展名。"""
    _ensure_backup_dir()
    backend = get_backup_backend()
    ext = backend.backup_extension  # ".sql" or ".db"
    filename = f"copree_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}.gz"
    path = BACKUP_DIR / filename
    # 流式压缩写入
    with gzip.open(path, "wb") as f:
        f.write(db_bytes)
    logger.info(f"💾 备份已落盘: {path} ({len(db_bytes)} bytes)")
    return filename


def prune_backups(keep: int) -> int:
    """保留最近 keep 份，删除更早的备份。返回删除数量。"""
    if keep < 1:
        keep = 1
    files = list_backup_files()
    if len(files) <= keep:
        return 0
    deleted = 0
    for old in files[keep:]:  # 已按时间倒序，末尾 = 最旧
        try:
            old.unlink()
            deleted += 1
            logger.info(f"🗑️ 清理过期备份: {old.name}")
        except OSError as e:
            logger.warning(f"  ⚠️ 清理备份失败 {old.name}: {e}")
    return deleted
