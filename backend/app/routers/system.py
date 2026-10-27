"""
基础系统路由：健康检查、维护消息、服务根路径。
"""
import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import check_db_connection
from app.services.infrastructure.maintenance import maintenance

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def root():
    """服务根路径"""
    return {
        "service": "AI群聊社交网络",
        "version": settings.app_version,
        "status": "running",
    }


@router.get("/maintenance-msg")
async def public_maintenance_msg():
    """维护状态公开接口"""
    return maintenance.get_public_message()


@router.get("/system/update-status")
async def update_status(force: bool = False):
    """版本与更新检查（公开，无需登录）。

    为什么公开：桌面启动器在用户登录**之前**就要能显示「有新版本」，而它只能问本机后端；
    暴露的信息与 `/health` 同级（当前版本 + 上游公开的最新版本），没有账号相关内容。
    防滥用靠服务端三层缓存（TTL / 单飞 / 手动节流），细节见 services/infrastructure/update_check.py。
    """
    from app.services.infrastructure.update_check import get_update_status

    return await get_update_status(force=force)


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

    status_code = 200 if db_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if db_ok else "degraded",
            "version": settings.app_version,
            # 发行方式（source/docker/desktop）：前端据此决定要不要显示「下载安装包」
            # 这类升级入口。放在 /health 里是为了让界面首屏就能拿到，不必为一个字段
            # 再发一次会触发出站请求的接口。
            "distribution": settings.app_distribution,
        },
    )
