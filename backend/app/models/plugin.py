"""
统一插件模型 — 目录即插件（plugins/<id>/plugin.json）

两级开关：
- plugins.enabled      管理员全局开放/关闭（管理面板一键切换）
- user_plugin_prefs    用户个人启用/停用（设置页一键切换）
生效 = enabled AND 用户偏好（用户偏好默认开启，即"装好即可用"）。
"""
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Boolean, Text, DateTime, ForeignKey, UniqueConstraint,
)
from app.database import Base


def _now() -> datetime:
    return datetime.utcnow()


class Plugin(Base):
    __tablename__ = "plugins"

    id = Column(String(80), primary_key=True, comment="插件 id（目录名，如 skin-aurora）")
    name = Column(String(120), nullable=False, comment="显示名称")
    description = Column(Text, default="", comment="描述")
    category = Column(String(20), default="other", comment="skin | skill | world | service | other")
    version = Column(String(20), default="1.0.0")
    author = Column(String(80), default="")
    icon = Column(String(40), default="", comment="lucide 图标名（前端渲染）")
    enabled = Column(Boolean, default=True, comment="管理员全局开关（false = 所有人不可用）")
    builtin = Column(Boolean, default=False, comment="是否随代码内置（backend/plugins）")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class PluginConfig(Base):
    """插件级配置 — 键与类型由插件声明的 config_schema 决定。

    instance 让"一个插件、多份配置"成立：QQ 通道要接 3 个机器人，就是同一个插件下的
    3 个实例（每个实例一份 app_id/client_secret），而不是 3 个插件。

    机密项（schema 里 secret: true）存 Fernet 密文，出库即解密；
    读写只有 app/services/plugin/config.py 一个入口。
    """
    __tablename__ = "plugin_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plugin_id = Column(String(80), ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False)
    instance = Column(String(40), nullable=False, server_default="", comment="实例 id；单实例为空串")
    key = Column(String(80), nullable=False, comment="配置键（与 config_schema 同名）")
    value = Column(Text, default="", comment="明文值；is_secret 时存 Fernet 密文")
    is_secret = Column(Boolean, default=False, comment="是否机密（由 schema 的 secret 标记决定）")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("plugin_id", "instance", "key", name="uq_plugin_config_key"),)


class PluginServiceState(Base):
    """服务插件的期望运行状态（按实例）。

    为什么不放 plugins 表：一个插件可以有多个实例，各自启停；而且内置服务（browser）
    没有 plugins 行。所以这里只存 id 对，不挂外键。
    """
    __tablename__ = "plugin_service_states"

    plugin_id = Column(String(80), primary_key=True)
    instance = Column(String(40), primary_key=True, server_default="")
    desired_running = Column(Boolean, default=True, nullable=False, comment="期望运行状态：重启后据此恢复")
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class UserPluginPref(Base):
    __tablename__ = "user_plugin_prefs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plugin_id = Column(String(80), ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False)
    enabled = Column(Boolean, default=True, comment="用户个人开关")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("user_id", "plugin_id", name="uq_user_plugin_pref"),)


# 外部身份（含配对状态）见 app/models/external.py：
# 原来这里的 ChannelPairing 单开了一张 channel_pairings，已并入 external_identities
