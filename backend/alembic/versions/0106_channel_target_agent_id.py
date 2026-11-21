"""通道配置补上 AI 的 id

Revision ID: 0106
Revises: 0105
Create Date: 2026-11-03

通道曾用 AI 名字当绑定键（target_agent），改名就把通道绑死：2026-11-02 线上
「鲸鱼娘 Deepseek」改名后，qq-channel 按名字在群里找不到它，start() 失败，
整条通道静默不启动。现在认人只按 id，运行时不再保留"按名字找一次"的兼容分支，
存量配置在这里补齐。

实例名一直是 agent-<agent_id>（channel.instance_of），所以回填是确定性的，
不需要读 AI 名字（名字恰恰是过期的那一份）。只圈内置的两个通道插件：
其它插件的配置不认这个键，写了只是噪音；将来新插件在保存时就会拿到 id，用不着回填。
"""
from alembic import op
import sqlalchemy as sa
from typing import Sequence, Union

revision: str = '0106'
down_revision: Union[str, None] = '0105'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        INSERT INTO plugin_configs (plugin_id, instance, key, value, is_secret, created_at, updated_at)
        SELECT plugin_id, instance, 'target_agent_id', substring(instance from 7), false, now(), now()
        FROM (SELECT DISTINCT plugin_id, instance FROM plugin_configs
              WHERE plugin_id IN ('qq-channel', 'qq-napcat')
                AND instance ~ '^agent-[0-9]+$') t
        WHERE NOT EXISTS (
            SELECT 1 FROM plugin_configs c
            WHERE c.plugin_id = t.plugin_id
              AND c.instance = t.instance
              AND c.key = 'target_agent_id'
        )
        ON CONFLICT (plugin_id, instance, key) DO NOTHING
    """))


def downgrade() -> None:
    # 只回收本迁移补的行：值必须与实例名里那段 id 一致
    op.execute(sa.text("""
        DELETE FROM plugin_configs
        WHERE key = 'target_agent_id'
          AND plugin_id IN ('qq-channel', 'qq-napcat')
          AND instance ~ '^agent-[0-9]+$'
          AND value = substring(instance from 7)
    """))
