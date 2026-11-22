"""AI 的语音（TTS）配置：一家提供商 + 一把 key + 一个音色 + 上次检测的结论

为什么单开一张表，不往 agents 上加几列：
- 语音是可选能力，生命周期跟着 AI 走（ON DELETE CASCADE），但和"这个 AI 怎么说话"是
  两件事——配置页分开、权限也分开（创建者给自己 AI 配）；
- 它有自己的一份**检测结论**（通不通、欠费、区域错、限流）要缓存下来，混进 agent 本体
  会让那张已经很宽的表再多五列噪声；
- 早晚要并入"用户自己的配置预设库"（一人多套 key 换着用），先立成独立行，迁移只看这张表。

key 一律 Fernet 加密（与 agents.api_key_encrypted / api_key_pool 同一口径），
明文只在这一进程的内存里出现；读出来用完即弃，不往日志里写。
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
    func,
)

from app.database import Base


class AgentVoiceConfig(Base):
    """一个 AI 的一套语音配置（1:1，靠 unique 约束保证）"""

    __tablename__ = "agent_voice_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False,
        comment="AI 本体 id（agents.id），不是虚拟账号 users.id",
    )
    provider = Column(String(40), nullable=False, default="",
                      comment="provider_presets 里 kind=voice 的预设 key：breeze / minimax-cn / minimax-intl …")
    base_url = Column(Text, nullable=False, default="", comment="供应商根地址；换了区域就整条换掉")
    api_key_encrypted = Column(Text, nullable=True, comment="Fernet 加密的 API Key")
    voice_id = Column(String(200), nullable=False, default="",
                      comment="音色 id，用户自己填（各家体系不同，不校验格式）")
    model = Column(String(120), nullable=False, default="",
                   comment="OpenAI 兼容 TTS 的模型名（tts-1 之类）；其它家留空")
    voice_profile_id = Column(
        Integer, ForeignKey("user_api_profiles.id", ondelete="SET NULL"), nullable=True,
        comment="这个 AI 用哪条语音配置档（kind=voice）；NULL = 用本行手填的厂商/Key/音色",
    )

    # ── 检测结论（缓存：不允许每次发语音都先打一次供应商）──
    probe_ok = Column(Boolean, nullable=False, default=False, server_default="0",
                      comment="上次检测是否通过；False = 这个 AI 暂时没有发语音的能力")
    probe_kind = Column(String(40), nullable=False, default="",
                        comment="失败归类：auth / quota / region / permission / rate_limited / network …")
    probe_message = Column(Text, nullable=True, comment="给人看的一句话（已脱敏）")
    probed_at = Column(DateTime, nullable=True, comment="上次检测时间；空 = 从没测过")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_agent_voice_config"),
    )
