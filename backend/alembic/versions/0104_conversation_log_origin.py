"""对话日志标出来源：一次模型调用，还是一段状态的收尾

日志按状态帧身份归堆，但一条日志原本只对应一次模型调用，而请求体在开轮时就定型了：
一轮中途换帧（enter_state / pop_state）在日志里不留任何痕迹，插件帧那几段于是彻底消失。

于是多一列：离开状态帧时落的那条（origin=switch）与真实调用（origin=call）分开。
两者只有一处差别需要机器认得：切换行不是模型调用，没有结局，也不算 token。
"""
from alembic import op
import sqlalchemy as sa

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_conversation_logs", sa.Column(
        "origin", sa.String(length=16), nullable=False, server_default="call",
        comment="这行从哪来：call = 一次模型调用；switch = 离开一个状态帧时落的那一段"))


def downgrade() -> None:
    op.drop_column("ai_conversation_logs", "origin")
