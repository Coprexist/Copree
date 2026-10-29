"""状态链：运行集合改成双向链表（节点只存 id）

把「谁是当前帧」从「数组顺序 + status 推」改成「链尾是谁」：
- `agent_state_chain`：一 AI 一条链，只记两个端点与执行窗口大小；
- `agent_state_chain_node`：链上一个运行中的帧一行，只存 frame_id 与前驱后继。

为什么单独建表、不扩 agents：链只关心顺序，帧内容仍躺在 `agents.state_stack`。
两层各写各的，一次切会话就从"重排整份栈"（线上见过 126 帧 / 546 KB 的一份）
变成几条行更新，而"帧 id → 在队里第几个"变成一次唯一键命中。

老 AI 不需要数据迁移：没有链的 AI 在第一次写栈时按当时的运行集合现建一条
（`state_stack_service._ensure_chain`），"当前帧"因此不变。

Revision ID: 0094
Revises: 0093
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0094"
down_revision: Union[str, None] = "0093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_state_chain",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("head_id", sa.Integer(), nullable=True,
                  comment="链头节点号（压得最久、最不急的一端）"),
        sa.Column("tail_id", sa.Integer(), nullable=True,
                  comment="链尾节点号（最后加入的一端 = 正在执行）"),
        sa.Column("max_active", sa.Integer(), server_default="1", nullable=False,
                  comment="执行窗口：链尾起算的并发帧数（默认 1）"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id"),
    )
    op.create_table(
        "agent_state_chain_node",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("frame_id", sa.String(length=32), nullable=False,
                  comment="agents.state_stack 里的帧 id"),
        sa.Column("prev_id", sa.Integer(), nullable=True, comment="前一个节点号（NULL = 链头）"),
        sa.Column("next_id", sa.Integer(), nullable=True, comment="后一个节点号（NULL = 链尾）"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "frame_id", name="uq_state_chain_frame"),
    )
    op.create_index("ix_state_chain_prev", "agent_state_chain_node", ["agent_id", "prev_id"])
    op.create_index("ix_state_chain_next", "agent_state_chain_node", ["agent_id", "next_id"])


def downgrade() -> None:
    op.drop_index("ix_state_chain_next", table_name="agent_state_chain_node")
    op.drop_index("ix_state_chain_prev", table_name="agent_state_chain_node")
    op.drop_table("agent_state_chain_node")
    op.drop_table("agent_state_chain")
