"""AI 的语音（TTS）配置：一台 AI 一套 key + 一个音色 + 上次检测结论

与表情包开关同一条思路：能力本身在平台，用不用、用谁的 key 由创建者决定；
检测结论也落库（缓存），否则"这个 AI 能不能发语音"每轮都要打一次供应商。
"""
from alembic import op
import sqlalchemy as sa

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_voice_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  nullable=False, comment="AI 本体 id（agents.id），不是虚拟账号 users.id"),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="",
                  comment="provider_presets 里 kind=voice 的预设 key"),
        sa.Column("base_url", sa.Text(), nullable=False, server_default="",
                  comment="供应商根地址；换区域就整条换掉"),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True, comment="Fernet 加密的 API Key"),
        sa.Column("voice_id", sa.String(length=200), nullable=False, server_default="",
                  comment="音色 id，用户自己填"),
        sa.Column("model", sa.String(length=120), nullable=False, server_default="",
                  comment="OpenAI 兼容 TTS 的模型名；其它家留空"),
        sa.Column("probe_ok", sa.Boolean(), nullable=False, server_default=sa.false(),
                  comment="上次检测是否通过"),
        sa.Column("probe_kind", sa.String(length=40), nullable=False, server_default="",
                  comment="失败归类：auth / quota / region / permission / rate_limited / network"),
        sa.Column("probe_message", sa.Text(), nullable=True, comment="给人看的一句话（已脱敏）"),
        sa.Column("probed_at", sa.DateTime(), nullable=True, comment="上次检测时间"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("agent_id", name="uq_agent_voice_config"),
    )


def downgrade() -> None:
    op.drop_table("agent_voice_configs")
