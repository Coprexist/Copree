"""管理员的内存策略：日常水位与硬上限（MB，0 = 没设）

为什么放在系统设置里：并发上限（`AI_MAX_CONCURRENT_TURNS`）回答的是"同时跑几个回合"，
但一台机器真正先撞到的往往是内存。部署形态又差得远——同一个镜像可能跑在 2 GB 的树莓派上，
也可能跑在 128 GB 的服务器上，所以**由管理员给出这条线**，比代码里猜任何默认值都准。

两列都是 0 表示"没设"：那时容量准入完全走默认算法（事件循环延迟 / 连接池 / 入口队列）。
设了之后，调控优先向管理员给的值倾斜——写下来的线就是"这条别越"。

Revision ID: 0108
Revises: 0107
Create Date: 2026-11-07
"""
from alembic import op
import sqlalchemy as sa
from typing import Sequence, Union

revision: str = '0108'
down_revision: Union[str, None] = '0107'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('system_settings', sa.Column(
        'memory_daily_target_mb', sa.Integer(), nullable=False, server_default='0',
        comment='日常内存占用目标（MB，0=未设，走默认调控）'))
    op.add_column('system_settings', sa.Column(
        'memory_limit_mb', sa.Integer(), nullable=False, server_default='0',
        comment='内存占用上限（MB，0=未设）'))


def downgrade() -> None:
    op.drop_column('system_settings', 'memory_limit_mb')
    op.drop_column('system_settings', 'memory_daily_target_mb')
