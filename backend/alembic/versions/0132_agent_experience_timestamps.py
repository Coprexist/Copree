"""agent_experiences 的三个时间列改成无时区（全站时间戳契约）

这一张表是最后三个 `timezone=True` 的列：其它时间列都是 UTC 的 naive 时间戳
（docs/dev/test_strategy.md / tests/test_schema_contract.py 的契约），只有它还在带时区。
带时区的列一旦和 naive 的 `utc_now()` 比较，差值会按服务器时区歪掉。

转换按 UTC 解释旧值（原来存的就是 UTC 瞬间），不需要业务字段参与。

downgrade 只是把类型转回 timestamptz（同样按 UTC 解释），值不变。

Revision ID: 0132
Revises: 0131
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0132"
down_revision: Union[str, None] = "0131"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = ("started_at", "ended_at", "created_at")


def upgrade() -> None:
    for column in COLUMNS:
        op.execute(
            f"ALTER TABLE agent_experiences ALTER COLUMN {column} "
            f"TYPE timestamp without time zone USING {column} AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    for column in COLUMNS:
        op.execute(
            f"ALTER TABLE agent_experiences ALTER COLUMN {column} "
            f"TYPE timestamp with time zone USING {column} AT TIME ZONE 'UTC'"
        )
