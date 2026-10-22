"""messages.sender_type 支持 assistant（群助手发言的正式身份）

群助手是 group_assistants 独立表里的一行：无账号、不入群成员表。它在群消息里的身份
原本写成 sender_type="ai"、sender_id=-ga.id（用负号躲开 users 号段），读的人得记住这条
隐规则，id 与 users.id 同号时还会把「群助手 3」和「用户 3」混成一个人。
改成 sender_type="assistant"、sender_id=group_assistants.id 之后，sender_type 就是
"这个 id 属于哪张表"，与 human/ai 指向 users 是同一套读法。

Revision ID: 0090
Revises: 0089
"""
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None

# 历史库里有两种约束名：线上建的是 messages_sender_type_check，模型里声明的是 ck_message_sender_type
OLD_NAMES = ("messages_sender_type_check", "ck_message_sender_type")


def upgrade() -> None:
    for name in OLD_NAMES:
        op.execute(f"ALTER TABLE messages DROP CONSTRAINT IF EXISTS {name}")
    op.execute(
        "ALTER TABLE messages ADD CONSTRAINT messages_sender_type_check "
        "CHECK (sender_type IN ('human', 'ai', 'assistant'))"
    )
    # 负值老写法就地改成正式身份：不转的话，改完之后那些老消息会显示成「用户-3」
    op.execute(
        "UPDATE messages SET sender_type = 'assistant', sender_id = -sender_id "
        "WHERE sender_type = 'ai' AND sender_id < 0"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE messages SET sender_type = 'ai', sender_id = -sender_id "
        "WHERE sender_type = 'assistant'"
    )
    op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_sender_type_check")
    op.execute(
        "ALTER TABLE messages ADD CONSTRAINT messages_sender_type_check "
        "CHECK (sender_type IN ('human', 'ai'))"
    )
