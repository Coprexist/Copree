"""
OpenCLI 核心服务
命令执行、权限检查（黑白名单+正则）、速率限制、日志记录
"""
import re
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, update
from app.models.opencli import (
    OpenCLIConfig,
    OpenCLIAgentWhitelist,
    OpenCLICommandWhitelist,
    OpenCLIUsageLog,
    OpenCLIDeniedCommand,
)
from app.config import settings
from app.repositories.content_repo import ContentRepository

logger = logging.getLogger(__name__)


# 输出截断长度
STDOUT_MAX_CHARS = 2000


# ============================================================
# 权限检查
# ============================================================

async def _get_config(content_repo: ContentRepository) -> OpenCLIConfig:
    """获取或创建全局配置（单行表）"""
    result = await content_repo.execute(select(OpenCLIConfig).where(OpenCLIConfig.id == 1))
    config = result.scalar_one_or_none()
    if config is None:
        config = OpenCLIConfig(id=1, global_enabled=False)
        content_repo.add(config)
        await content_repo.flush()
    return config


async def check_permission(
    content_repo: ContentRepository,
    agent_id: int,
    command: str,
) -> tuple[bool, str]:
    """
    权限检查，返回 (allowed, reason)。
    检查顺序：全局开关 → AI 白名单 → 默认黑名单 → 命令白名单
    """
    # 1. 全局开关
    config = await _get_config(content_repo)
    if not config.global_enabled:
        return False, "OPENCLI_DISABLED: OpenCLI 全局未启用"

    # 2. 先查命令白名单（用于判断是否 default_enabled）
    cmd_result = await content_repo.execute(
        select(OpenCLICommandWhitelist).where(
            OpenCLICommandWhitelist.enabled == True
        )
    )
    whitelist_entries = cmd_result.scalars().all()

    # 检查命令是否匹配 default_enabled 项
    is_default_cmd = False
    for entry in whitelist_entries:
        if not entry.default_enabled:
            continue
        try:
            matched = re.fullmatch(entry.pattern, command) if entry.is_regex else (entry.pattern == command)
            if matched:
                is_default_cmd = True
                break
        except re.error:
            pass

    # 3. AI 白名单（默认命令可跳过）
    if not is_default_cmd:
        result = await content_repo.execute(
            select(OpenCLIAgentWhitelist).where(
                OpenCLIAgentWhitelist.agent_id == agent_id
            )
        )
        agent_wl = result.scalar_one_or_none()
        if agent_wl is None or not agent_wl.enabled:
            return False, "OPENCLI_AGENT_NOT_ALLOWED: 此 AI 未被授权使用 OpenCLI"

    # 4. 默认黑名单检查（无法被白名单覆盖）
    denied_result = await content_repo.execute(select(OpenCLIDeniedCommand))
    denied_patterns = denied_result.scalars().all()
    for dp in denied_patterns:
        try:
            if re.fullmatch(dp.pattern, command):
                return False, f"OPENCLI_COMMAND_DENIED: 命令 '{command}' 被系统禁止（{dp.reason}）"
        except re.error:
            if dp.pattern == command:
                return False, f"OPENCLI_COMMAND_DENIED: 命令 '{command}' 被系统禁止（{dp.reason}）"

    # 5. 命令匹配
    for entry in whitelist_entries:
        try:
            if entry.is_regex:
                if re.fullmatch(entry.pattern, command):
                    return True, ""
            else:
                if entry.pattern == command:
                    return True, ""
        except re.error as e:
            logger.warning(f"白名单正则 '{entry.pattern}' 无效: {e}")
            continue

    return False, f"OPENCLI_COMMAND_NOT_WHITELISTED: 命令 '{command}' 不在白名单中"


# ============================================================
# 速率限制
# ============================================================

async def check_rate_limit(
    content_repo: ContentRepository,
    agent_id: int,
) -> tuple[bool, str]:
    """
    速率限制检查，返回 (allowed, reason)。
    查询最近 1 分钟内的使用次数，与 AI 的限额比较。
    """
    # 获取该 AI 的限额
    config = await _get_config(content_repo)
    rate_limit = config.default_rate_limit_per_minute

    wl_result = await content_repo.execute(
        select(OpenCLIAgentWhitelist).where(
            OpenCLIAgentWhitelist.agent_id == agent_id
        )
    )
    agent_wl = wl_result.scalar_one_or_none()
    if agent_wl and agent_wl.rate_limit_override is not None:
        rate_limit = agent_wl.rate_limit_override

    # 查询最近 1 分钟的调用次数
    # ⚠️ executed_at 是 TIMESTAMP WITHOUT TIME ZONE，比较值必须去掉时区，
    #    否则 asyncpg 报错: can't subtract offset-naive and offset-aware datetimes
    one_minute_ago = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
    count_result = await content_repo.execute(
        select(func.count(OpenCLIUsageLog.id)).where(
            OpenCLIUsageLog.agent_id == agent_id,
            OpenCLIUsageLog.executed_at >= one_minute_ago,
        )
    )
    recent_count = count_result.scalar() or 0

    if recent_count >= rate_limit:
        return False, f"OPENCLI_RATE_LIMITED: 速率限制已达上限（{rate_limit}次/分钟，当前已用 {recent_count} 次）"

    return True, ""


# ============================================================
# AI 文件空间操作（在进程内执行，不走 opencli 子进程）
# ============================================================
# 每个 AI 拥有独立沙箱目录 /app/data/agents/{agent_id}/
# 所有路径均解析到此目录下，禁止路径穿越（..）

import os
import shutil
from pathlib import Path

AGENTS_DATA_DIR = Path(settings.agents_dir)


def _resolve_agent_path(agent_id: int, file_path: str) -> Path:
    """
    将 AI 请求的文件路径解析到其沙箱目录下。
    阻止路径穿越（如 ../ 企图越狱到上级目录）。

    Args:
        agent_id: AI 的 ID
        file_path: AI 指定的相对路径（如 "notes/thoughts.txt"）

    Returns:
        绝对路径 Path 对象

    Raises:
        ValueError: 路径穿越或非法路径
    """
    workspace = AGENTS_DATA_DIR / str(agent_id)
    # 用 resolve() 消除 .. 和符号链接
    try:
        resolved = (workspace / file_path).resolve()
        workspace_resolved = workspace.resolve()
        # 确保解析后的路径仍在 workspace 下
        if not str(resolved).startswith(str(workspace_resolved) + os.sep) and resolved != workspace_resolved:
            raise ValueError(
                f"路径穿越被阻止: '{file_path}' 试图访问沙箱外路径。"
                f"你的文件空间根目录是 /app/data/agents/{agent_id}/，"
                f"所有文件操作只能在此目录内进行。"
            )
        return resolved
    except (ValueError, OSError) as e:
        if "路径穿越" in str(e):
            raise
        raise ValueError(f"无效路径 '{file_path}': {e}")


async def _handle_file_read(agent_id: int, args: list[str]) -> tuple[int, str, str]:
    """读取文件内容"""
    if not args:
        return 1, "", "用法: file_read <文件路径>"
    file_path = _resolve_agent_path(agent_id, args[0])
    if not file_path.exists():
        return 1, "", f"文件不存在: {args[0]}"
    if not file_path.is_file():
        return 1, "", f"不是文件: {args[0]}"
    try:
        content = file_path.read_text(encoding="utf-8")
        # 截断过长内容
        if len(content) > STDOUT_MAX_CHARS:
            content = content[:STDOUT_MAX_CHARS] + f"\n... (截断，共 {len(content)} 字符)"
        return 0, content, ""
    except UnicodeDecodeError:
        return 1, "", f"文件不是 UTF-8 文本: {args[0]}"
    except Exception as e:
        return 1, "", f"读取失败: {e}"


async def _handle_file_write(agent_id: int, args: list[str]) -> tuple[int, str, str]:
    """写入/创建文件"""
    if len(args) < 2:
        return 1, "", "用法: file_write <文件路径> <内容>"
    file_path = args[0]
    content = " ".join(args[1:])  # 后面所有参数拼接为内容
    resolved = _resolve_agent_path(agent_id, file_path)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return 0, f"已写入 {len(content)} 字符 → {file_path}", ""
    except Exception as e:
        return 1, "", f"写入失败: {e}"


async def _handle_file_list(agent_id: int, args: list[str]) -> tuple[int, str, str]:
    """列出目录文件"""
    dir_path = args[0] if args else "."
    resolved = _resolve_agent_path(agent_id, dir_path)
    if not resolved.exists():
        return 1, "", f"目录不存在: {dir_path}"
    if not resolved.is_dir():
        return 1, "", f"不是目录: {dir_path}"
    try:
        items = sorted(resolved.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        lines = []
        for item in items:
            if item.is_dir():
                lines.append(f"📁 {item.name}/")
            else:
                size = item.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f}MB"
                lines.append(f"📄 {item.name}  ({size_str})")
        if not lines:
            return 0, f"目录为空: {dir_path}", ""
        return 0, "\n".join(lines), ""
    except Exception as e:
        return 1, "", f"列出失败: {e}"


async def _handle_file_delete(agent_id: int, args: list[str]) -> tuple[int, str, str]:
    """删除文件"""
    if not args:
        return 1, "", "用法: file_delete <文件路径>"
    resolved = _resolve_agent_path(agent_id, args[0])
    if not resolved.exists():
        return 1, "", f"文件不存在: {args[0]}"
    if resolved.is_dir():
        return 1, "", f"是目录而非文件，请用 create_dir 操作: {args[0]}"
    try:
        resolved.unlink()
        return 0, f"已删除: {args[0]}", ""
    except Exception as e:
        return 1, "", f"删除失败: {e}"


async def _handle_file_info(agent_id: int, args: list[str]) -> tuple[int, str, str]:
    """查看文件元信息"""
    if not args:
        return 1, "", "用法: file_info <文件路径>"
    resolved = _resolve_agent_path(agent_id, args[0])
    if not resolved.exists():
        return 1, "", f"文件不存在: {args[0]}"
    try:
        stat = resolved.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        size = stat.st_size
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"
        lines = [
            f"路径: {args[0]}",
            f"类型: {'目录' if resolved.is_dir() else '文件'}",
            f"大小: {size_str} ({stat.st_size} 字节)",
            f"修改时间: {mtime}",
        ]
        return 0, "\n".join(lines), ""
    except Exception as e:
        return 1, "", f"获取信息失败: {e}"


async def _handle_create_dir(agent_id: int, args: list[str]) -> tuple[int, str, str]:
    """创建目录"""
    if not args:
        return 1, "", "用法: create_dir <目录路径>"
    resolved = _resolve_agent_path(agent_id, args[0])
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        return 0, f"目录已创建: {args[0]}", ""
    except Exception as e:
        return 1, "", f"创建目录失败: {e}"


# 文件操作 handler 注册表
_FILE_HANDLERS: dict[str, callable] = {
    "file_read": _handle_file_read,
    "file_write": _handle_file_write,
    "file_list": _handle_file_list,
    "file_delete": _handle_file_delete,
    "file_info": _handle_file_info,
    "create_dir": _handle_create_dir,
}


# ============================================================
# 命令执行
# ============================================================

async def execute_opencli(
    content_repo: ContentRepository,
    agent_id: int,
    command: str,
    args: list[str] | None = None,
    timeout: int | None = None,
) -> dict:
    """
    执行命令（文件操作走进程内 Python，其他走 opencli 子进程）。
    返回:
        {command, args, exit_code, stdout, stderr, duration_ms}
    失败时 raise ValueError（由调用方转为工具错误格式）
    """
    args = args or []
    timeout = timeout or settings.opencli_timeout_seconds

    # 1. 权限检查
    allowed, reason = await check_permission(content_repo, agent_id, command)
    if not allowed:
        raise PermissionError(reason)

    # 2. 速率限制检查
    rate_ok, rate_reason = await check_rate_limit(content_repo, agent_id)
    if not rate_ok:
        raise PermissionError(rate_reason)

    # 3. 记录开始时间
    start_time = datetime.now(timezone.utc)

    # 4. 执行命令 —— 文件操作在进程内直接处理
    file_handler = _FILE_HANDLERS.get(command)
    if file_handler:
        try:
            exit_code, stdout, stderr = await file_handler(agent_id, args)
            duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            await _log_usage(content_repo, agent_id, command, args, exit_code, stdout, stderr, duration_ms)
            logger.info(
                f"OpenCLI(file): agent={agent_id} cmd={command} exit={exit_code} duration={duration_ms}ms"
            )
            return {
                "command": command,
                "args": args,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "duration_ms": duration_ms,
            }
        except ValueError as e:
            # 路径校验失败等
            duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            await _log_usage(content_repo, agent_id, command, args, -1, "", str(e), duration_ms)
            raise

    # 5. 非文件命令 —— 走 opencli 子进程
    try:
        proc = await asyncio.create_subprocess_exec(
            "opencli", command, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        # 超时 → 杀死进程 → 记录日志
        duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        await _log_usage(content_repo, agent_id, command, args, None, "", "TIMEOUT", duration_ms)
        raise TimeoutError(
            f"OPENCLI_TIMEOUT: 命令执行超时（{timeout}秒），已终止"
        )

    exit_code = proc.returncode
    duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    stdout = stdout_bytes.decode("utf-8", errors="replace")[:STDOUT_MAX_CHARS]
    stderr = stderr_bytes.decode("utf-8", errors="replace")[:STDOUT_MAX_CHARS]

    # 6. 写入使用日志
    await _log_usage(content_repo, agent_id, command, args, exit_code, stdout, stderr, duration_ms)

    logger.info(
        f"OpenCLI: agent={agent_id} cmd={command} exit={exit_code} duration={duration_ms}ms"
    )

    return {
        "command": command,
        "args": args,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
    }


async def _log_usage(
    content_repo: ContentRepository,
    agent_id: int,
    command: str,
    args: list[str],
    exit_code: int | None,
    stdout: str,
    stderr: str,
    duration_ms: int,
):
    """写入使用日志"""
    log_entry = OpenCLIUsageLog(
        agent_id=agent_id,
        command=command,
        args=" ".join(args) if args else None,
        exit_code=exit_code,
        stdout_truncated=stdout[:STDOUT_MAX_CHARS] if stdout else None,
        stderr_truncated=stderr[:STDOUT_MAX_CHARS] if stderr else None,
        duration_ms=duration_ms,
    )
    content_repo.add(log_entry)
    await content_repo.flush()


# ============================================================
# 配置管理（供 admin router 使用）
# ============================================================

async def get_opencli_config(content_repo: ContentRepository) -> dict:
    """获取全局配置"""
    config = await _get_config(content_repo)
    return {
        "global_enabled": config.global_enabled,
        "default_rate_limit_per_minute": config.default_rate_limit_per_minute,
        "timeout_seconds": config.timeout_seconds,
    }


async def update_opencli_config(
    content_repo: ContentRepository,
    updated_by: int,
    global_enabled: bool | None = None,
    default_rate_limit_per_minute: int | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    """更新全局配置"""
    config = await _get_config(content_repo)
    if global_enabled is not None:
        config.global_enabled = global_enabled
    if default_rate_limit_per_minute is not None:
        config.default_rate_limit_per_minute = default_rate_limit_per_minute
    if timeout_seconds is not None:
        config.timeout_seconds = timeout_seconds
    config.updated_by = updated_by
    config.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await content_repo.flush()
    return await get_opencli_config(content_repo)


async def list_agent_whitelist(content_repo: ContentRepository) -> list[dict]:
    """获取所有 AI 的 OpenCLI 权限状态（含未在表中的 AI）"""
    from app.models.agent import Agent
    config = await _get_config(content_repo)

    # 获取所有 AI
    agents_result = await content_repo.execute(select(Agent).order_by(Agent.id))
    agents = agents_result.scalars().all()

    # 获取已有的白名单记录
    wl_result = await content_repo.execute(select(OpenCLIAgentWhitelist))
    wl_map = {w.agent_id: w for w in wl_result.scalars().all()}

    items = []
    for agent in agents:
        wl = wl_map.get(agent.id)
        enabled = wl.enabled if wl else False
        override = wl.rate_limit_override if wl else None
        actual_rate = override if override is not None else config.default_rate_limit_per_minute
        items.append({
            "agent_id": agent.id,
            "agent_name": agent.name,
            "owner_id": agent.owner_id,
            "enabled": enabled,
            "rate_limit_override": override,
            "actual_rate_limit": actual_rate,
            "created_at": str(wl.created_at) if wl and wl.created_at else None,
        })
    return items


async def update_agent_whitelist(
    content_repo: ContentRepository,
    agent_id: int,
    enabled: bool,
    rate_limit_override: int | None = None,
) -> dict:
    """更新某个 AI 的 OpenCLI 权限"""
    result = await content_repo.execute(
        select(OpenCLIAgentWhitelist).where(
            OpenCLIAgentWhitelist.agent_id == agent_id
        )
    )
    wl = result.scalar_one_or_none()
    if wl is None:
        wl = OpenCLIAgentWhitelist(
            agent_id=agent_id,
            enabled=enabled,
            rate_limit_override=rate_limit_override,
        )
        content_repo.add(wl)
    else:
        wl.enabled = enabled
        wl.rate_limit_override = rate_limit_override

    await content_repo.flush()
    await content_repo.refresh(wl)
    return {"agent_id": wl.agent_id, "enabled": wl.enabled}


async def list_command_whitelist(content_repo: ContentRepository) -> list[dict]:
    """获取命令白名单"""
    result = await content_repo.execute(
        select(OpenCLICommandWhitelist).order_by(OpenCLICommandWhitelist.created_at.desc())
    )
    entries = result.scalars().all()
    return [
        {
            "id": e.id,
            "pattern": e.pattern,
            "is_regex": e.is_regex,
            "description": e.description,
            "enabled": e.enabled,
            "default_enabled": e.default_enabled if hasattr(e, 'default_enabled') else False,
            "created_at": str(e.created_at) if e.created_at else None,
        }
        for e in entries
    ]


async def add_command_whitelist(
    content_repo: ContentRepository,
    pattern: str,
    is_regex: bool,
    description: str | None,
    created_by: int,
    default_enabled: bool = False,
) -> dict:
    """添加命令白名单"""
    # 验证正则
    if is_regex:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"正则表达式无效: {e}")

    entry = OpenCLICommandWhitelist(
        pattern=pattern,
        is_regex=is_regex,
        description=description,
        default_enabled=default_enabled,
        created_by=created_by,
    )
    content_repo.add(entry)
    await content_repo.flush()
    await content_repo.refresh(entry)
    return {
        "id": entry.id,
        "pattern": entry.pattern,
        "is_regex": entry.is_regex,
        "description": entry.description,
        "enabled": entry.enabled,
        "created_at": str(entry.created_at) if entry.created_at else None,
    }


async def toggle_command_whitelist(
    content_repo: ContentRepository,
    cmd_id: int,
    enabled: bool,
) -> dict:
    """开关某条命令白名单"""
    result = await content_repo.execute(
        select(OpenCLICommandWhitelist).where(OpenCLICommandWhitelist.id == cmd_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise ValueError("命令白名单条目不存在")
    entry.enabled = enabled
    await content_repo.flush()
    return {"id": entry.id, "enabled": entry.enabled}


async def delete_command_whitelist(content_repo: ContentRepository, cmd_id: int):
    """删除命令白名单"""
    result = await content_repo.execute(
        select(OpenCLICommandWhitelist).where(OpenCLICommandWhitelist.id == cmd_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise ValueError("命令白名单条目不存在")
    await content_repo.delete(entry)
    await content_repo.flush()


async def get_usage_logs(
    content_repo: ContentRepository,
    agent_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """获取使用日志（分页）"""
    offset = (page - 1) * page_size

    query = select(OpenCLIUsageLog)
    count_query = select(func.count(OpenCLIUsageLog.id))
    if agent_id is not None:
        query = query.where(OpenCLIUsageLog.agent_id == agent_id)
        count_query = count_query.where(OpenCLIUsageLog.agent_id == agent_id)

    total = (await content_repo.execute(count_query)).scalar()
    result = await content_repo.execute(
        query.order_by(OpenCLIUsageLog.executed_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    logs = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": log.id,
                "agent_id": log.agent_id,
                "command": log.command,
                "args": log.args,
                "exit_code": log.exit_code,
                "stdout_truncated": log.stdout_truncated,
                "stderr_truncated": log.stderr_truncated,
                "duration_ms": log.duration_ms,
                "executed_at": str(log.executed_at) if log.executed_at else None,
            }
            for log in logs
        ],
    }
