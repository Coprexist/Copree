"""
基础系统路由：健康检查、维护消息、登录页公开设置、服务根路径。
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import check_db_connection, get_db
from app.services.infrastructure.maintenance import maintenance

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def root():
    """服务根路径"""
    from app.services.infrastructure.build_info import build_id

    return {
        "service": "AI群聊社交网络",
        "version": settings.app_version,
        "build": build_id(),
        "status": "running",
    }


@router.get("/maintenance-msg")
async def public_maintenance_msg():
    """维护状态公开接口"""
    return maintenance.get_public_message()


@router.get("/system/settings")
async def public_system_settings(db: AsyncSession = Depends(get_db)):
    """登录页/注册页用的公开设置（无需认证）。

    只暴露前端渲染必需、且本身不敏感的字段；SMTP、厂商配置等一律不在这里返回。
    """
    from app.services.infrastructure.system_settings_service import get_settings

    sys = await get_settings(db)
    return {
        "default_language": sys.get("default_language", "en"),
        "login_providers": sys.get("login_providers", ["direct"]),
        "require_email_verification": bool(sys.get("require_email_verification", False)),
        "registration_enabled": bool(sys.get("registration_enabled", True)),
        "audit_use_real_ip": bool(sys.get("audit_use_real_ip", False)),
    }


@router.get("/system/update-status")
async def update_status(force: bool = False):
    """版本与更新检查（公开，无需登录）。

    为什么公开：桌面启动器在用户登录**之前**就要能显示「有新版本」，而它只能问本机后端；
    暴露的信息与 `/health` 同级（当前版本 + 上游公开的最新版本），没有账号相关内容。
    防滥用靠服务端三层缓存（TTL / 单飞 / 手动节流），细节见 services/infrastructure/update_check.py。
    """
    from app.services.infrastructure.update_check import get_update_status

    return await get_update_status(force=force)


@router.post("/system/debug-wave")
async def debug_wave(payload: dict):
    """临时调试端点：把每帧波形高度追加写入 /tmp/waveframes.jsonl（分析波形用，用完删除）。"""
    try:
        with open("/tmp/waveframes.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("debug-wave 写入失败")
    return {"ok": True}


@router.get("/health")
async def health():
    """健康检查：数据库可用性，5s 超时保护"""
    try:
        db_ok = await asyncio.wait_for(check_db_connection(), timeout=5.0)
    except asyncio.TimeoutError:
        db_ok = False
        logger.warning("健康检查超时: 数据库查询超过 5s")
    except Exception as e:
        db_ok = False
        logger.warning("健康检查异常: %s", e)

    from app.services.infrastructure.build_info import build_id, migration_revision

    status_code = 200 if db_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if db_ok else "degraded",
            "version": settings.app_version,
            # 两个指纹：build 说明代码在哪一刻，migration 说明库在哪一步。合起来才能把
            # 一次反馈对到具体快照（只有版本号分不出两天的 preview，见 build_info 模块说明）。
            "build": build_id(),
            "migration": await migration_revision() if db_ok else "",
            # 发行方式（source/docker/desktop）：前端据此决定要不要显示「下载安装包」
            # 这类升级入口。放在 /health 里是为了让界面首屏就能拿到，不必为一个字段
            # 再发一次会触发出站请求的接口。
            "distribution": settings.app_distribution,
        },
    )
