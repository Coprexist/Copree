"""用户自己的 API 厂商与配置档 —— 两层结构，LLM 与语音共用

为什么是两层：
- **厂商**回答「在哪请求、这把 key 归谁」：地址、模型清单、默认模型、默认 Key。
  同一家厂商（通义、DeepSeek、自建中转站）只需要定义一次。
- **配置档**回答「这次用哪一把」：名称 + 厂商 + 可覆盖的 Key/模型/思考开关。
  一家厂商下可以挂多条（「家里」「公司」），这就是「一人多套 key 换着用」的落点。

为什么 Key 挂在厂商上还能挂配置档：
- 厂商层的 `default_api_key` 是这家的常用 key，填一次，名下配置档默认就用它；
- 配置档填了就是覆盖（同一家两个账号，各自一把）。
- **跨厂商永不回退**：解析时只读「配置档 → 它所属的厂商」，代码层面拿不到别家的 Key。
  拿别家的 key 去请求只会得到一句供应商的 401，比报「这家还没填 Key」难查得多。

为什么 kind 放在厂商上、不放在配置档上：
- 配置档的 kind 由它所属厂商决定，冗余一份迟早不一致；
- 需要按 kind 过滤时 join 一次即可（配置档数量是个人级，几十条，join 无压力）。

语音与 LLM 同表同形状：列名用 primary/secondary 这种中性说法，
LLM 里它们是聊天模型/工作模型，语音里 primary 是音色、secondary 留空（见 services/agent/api_config_resolver.py）。
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
    func,
)

from app.database import Base
from app.db_providers import json_column


class UserApiProvider(Base):
    """用户接入的一家 API 厂商（内置 14 家的副本，或自建中转站）"""

    __tablename__ = "user_api_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        comment="归属用户（users.id）",
    )
    kind = Column(String(16), nullable=False, default="llm",
                  comment="llm | voice：同表两类，界面互不串")
    name = Column(String(80), nullable=False,
                  comment="展示名，接入内置时可改（「通义」「通义·内网」）")
    preset_key = Column(
        String(40), nullable=True,
        comment="来自哪条内置预设（provider_presets.PRESETS 的 key）；空 = 纯自建。"
                "「恢复默认」按它重新拉内置值，也是识别内置厂商的唯一依据",
    )
    base_url = Column(Text, nullable=False, default="", comment="请求根地址")
    default_api_key_encrypted = Column(
        Text, nullable=True,
        comment="这家的默认 Key（Fernet 加密）：名下配置档没填 Key 时用它；"
                "**只对这一家有效**，绝不跨厂商回退",
    )
    options = json_column(),
    # ↑ [{value, label}]：LLM 是模型清单，语音是音色清单。通用形状换来两边一条代码路径。
    default_primary = Column(String(200), nullable=True,
                             comment="默认主型号：LLM 的聊天模型 / 语音的音色 id")
    default_secondary = Column(String(200), nullable=True,
                               comment="默认副型号：LLM 的工作模型 / 语音留空")
    thinking_supported = Column(Boolean, nullable=False, default=False, server_default="0",
                               comment="这家认不认深度思考开关；配置档里的三态以它为前提")
    system_layout = Column(
        String(16), nullable=False, default="anywhere", server_default="anywhere",
        comment="对端模板认不认中间 system（anywhere|leading|none），见 utils/pure/chat_template.py；"
                "中转站经常需要改这一项",
    )
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "kind", "name", name="uq_user_api_provider_name"),
    )


class UserApiProfile(Base):
    """一整套命名好的 API 配置：厂商 + 这把 Key + 模型覆盖"""

    __tablename__ = "user_api_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        comment="归属用户；与 provider.user_id 必须一致（写入端保证）",
    )
    provider_id = Column(
        Integer, ForeignKey("user_api_providers.id", ondelete="CASCADE"), nullable=False,
        comment="所属厂商——Key 与地址都从这一行派生，跨厂商不互通",
    )
    name = Column(String(80), nullable=False, comment="配置档名，如「家里」「公司」")

    api_key_encrypted = Column(
        Text, nullable=True,
        comment="这把 Key（加密）。空 = 用所属厂商的 default_api_key",
    )
    primary_model = Column(String(200), nullable=True,
                           comment="覆盖主型号；空 = 用厂商 default_primary")
    secondary_model = Column(String(200), nullable=True,
                             comment="覆盖副型号；空 = 用厂商 default_secondary")
    is_default = Column(Boolean, nullable=False, default=False, server_default="0",
                        comment="全局默认档：同一用户的同一 kind 下应当只有一条（写入端保证）")
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("provider_id", "name", name="uq_user_api_profile_name"),
    )
