"""
维护模式管理器 — 维护状态的唯一读写入口。

- 状态：文件标记（单容器单进程部署，标记目录见 settings.maintenance_dir，默认 <data_dir>/maintenance）
- 读：TTL 缓存（3s）避免每请求 stat；写：立即失效缓存，保证本进程内即时生效
- 所有读写（lifespan / 中间件 / admin 路由）必须经过本类；
  外部直接 touch 文件时最长 TTL 内生效
- 文案 JSON 持久化在数据目录（docker 挂载卷，重启不丢）
"""
import json
import logging
import os
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# 维护图片 URL 中指向文件表的形态：/fs/public/{file_id}
_PUBLIC_FILE_PREFIX = "/fs/public/"


def parse_public_file_id(url: str) -> int | None:
    """从 /fs/public/{file_id} 形态的 URL 解析文件编号；不是该形态返回 None"""
    if _PUBLIC_FILE_PREFIX not in (url or ""):
        return None
    tail = url.split(_PUBLIC_FILE_PREFIX, 1)[1].split("/", 1)[0].split("?", 1)[0]
    return int(tail) if tail.isdigit() else None

_DEFAULT_MSG = {
    "hard_title": "正在更新",
    "hard_body": "服务器正在更新，稍等一下就好~",
    "hard_color": "#f59e0b", "hard_text_color": "#ffffff",
    "hard_image": "", "hard_style": "popup",
    "soft_text": "服务器正在调整，功能可能偶尔不稳定",
    "soft_color": "#f59e0b", "soft_text_color": "#ffffff",
    "soft_style": "banner", "soft_once": False,
}


class MaintenanceManager:
    """维护模式状态与文案的统一入口（单例使用：maintenance）"""

    # 文件标记读取的 TTL 缓存（秒）：stat 开销可忽略，缓存主要避免高频路径重复系统调用
    _STAT_TTL = 3.0

    def __init__(self, maint_dir: str | None = None, data_dir: str | None = None):
        self._dir = maint_dir or settings.maintenance_dir
        self._auto = os.path.join(self._dir, "maintenance_startup")
        self._soft = os.path.join(self._dir, "maintenance_soft")
        self._hard = os.path.join(self._dir, "maintenance_admin_hard")
        self._msg_file = os.path.join(data_dir or settings.data_dir, "maintenance_msg.json")
        self._legacy_msg = os.path.join(self._dir, "maintenance_msg.json")
        self._images_file = os.path.join(data_dir or settings.data_dir, "maintenance_images.json")
        self._cache: dict[str, tuple[float, bool]] = {}
        self._migrate_legacy_msg()

    # ── 状态查询（TTL 缓存） ──

    def _stat(self, key: str, path: str) -> bool:
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < self._STAT_TTL:
            return hit[1]
        value = os.path.exists(path)
        self._cache[key] = (now, value)
        return value

    def is_auto(self) -> bool:
        """自动维护（启动/关闭期间）"""
        return self._stat("auto", self._auto)

    def is_soft(self) -> bool:
        """软维护（管理员手动）：API 正常但前端显示提示"""
        return self._stat("soft", self._soft)

    def is_hard(self) -> bool:
        """管理员手动硬维护"""
        return self._stat("hard", self._hard)

    def hard_active(self) -> bool:
        """当前是否处于任何硬维护（自动或管理员手动）"""
        return self.is_auto() or self.is_hard()

    def state(self) -> dict:
        """三态快照（admin 状态接口用）"""
        return {"auto": self.is_auto(), "hard": self.is_hard(), "soft": self.is_soft()}

    def mode(self) -> str | None:
        """当前维护模式: hard / soft / None（auto 归入 hard）"""
        if self.hard_active():
            return "hard"
        if self.is_soft():
            return "soft"
        return None

    # ── 状态写入（写后立即失效缓存） ──

    def _set_marker(self, path: str, active: bool) -> bool:
        """设置/清除标记；返回操作后的状态"""
        if active:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w"):
                pass
        elif os.path.exists(path):
            os.remove(path)
        self._cache.clear()
        return os.path.exists(path)

    def set_auto(self) -> None:
        """进入自动维护（启动/关闭期间）"""
        self._set_marker(self._auto, True)

    def clear_auto(self) -> bool:
        """退出自动维护；返回是否确实处于自动维护（供调用方决定提示文案）"""
        existed = self.is_auto()
        self._set_marker(self._auto, False)
        return existed

    def toggle_hard(self) -> bool:
        """切换管理员硬维护；返回操作后是否开启"""
        return self._set_marker(self._hard, not os.path.exists(self._hard))

    def toggle_soft(self) -> bool:
        """切换软维护；返回操作后是否开启"""
        return self._set_marker(self._soft, not os.path.exists(self._soft))

    # ── 文案 ──

    def get_msg(self) -> dict:
        """读取自定义维护文本，不存在则返回默认"""
        try:
            if os.path.exists(self._msg_file):
                with open(self._msg_file, encoding="utf-8") as f:
                    return json.loads(f.read())
        except Exception:
            logger.warning("⚠️ 维护文案读取失败，使用默认文案", exc_info=True)
        return dict(_DEFAULT_MSG)


    def get_public_message(self) -> dict:
        """供公开路由使用，不暴露内部字段"""
        msg = self.get_msg() or {}
        return {
            "msg": msg.get("hard_body", ""),
            "hard": self.hard_active(),
        }

    def save_msg(self, msg: dict) -> None:
        """持久化维护文案（数据目录，重启不丢）"""
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        with open(self._msg_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False))

    # ── 维护弹窗图片（唯一允许匿名下载的文件集合） ──

    def list_images(self) -> list[str]:
        """维护弹窗可选的图片 URL 列表"""
        try:
            if os.path.exists(self._images_file):
                with open(self._images_file, encoding="utf-8") as f:
                    return json.loads(f.read())
        except Exception:
            logger.warning("⚠️ 维护图片列表读取失败，按空列表处理", exc_info=True)
        return []

    def _save_images(self, images: list[str]) -> None:
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        with open(self._images_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(images, ensure_ascii=False))

    def add_image(self, url: str) -> list[str]:
        """新增图片 URL（去重，最新在前）"""
        images = [u for u in self.list_images() if u != url]
        images.insert(0, url)
        self._save_images(images)
        return images

    def remove_image(self, url: str) -> list[str]:
        """移除图片 URL"""
        images = [u for u in self.list_images() if u != url]
        self._save_images(images)
        return images

    def is_public_file(self, file_id: int) -> bool:
        """file_id 是否被维护弹窗引用 = 是否允许匿名下载。

        维护弹窗要在硬维护（用户必然未登录）时显示图片，所以 /fs/public 必须免鉴权；
        白名单只此一处，避免这条路径变成「按自增 ID 拖走全站文件」。
        """
        return any(parse_public_file_id(url) == file_id for url in self.list_images())

    def _migrate_legacy_msg(self) -> None:
        """旧版本文案在 MAINTENANCE_DIR（/tmp）→ 迁移到持久化数据目录"""
        if os.path.exists(self._msg_file) or not os.path.exists(self._legacy_msg):
            return
        try:
            Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
            with open(self._legacy_msg, encoding="utf-8") as src, \
                 open(self._msg_file, "w", encoding="utf-8") as dst:
                dst.write(src.read())
            logger.info("📦 维护文案已从 %s 迁移到 %s", self._legacy_msg, self._msg_file)
        except Exception:
            logger.warning("⚠️ 维护文案迁移失败（忽略，使用默认文案）", exc_info=True)


# 全局单例：中间件 / lifespan / admin 路由共用
maintenance = MaintenanceManager()
