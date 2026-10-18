"""把同一个人在两台机器人下各建的那个通道锚点账号并成一个（运维用）

缘起：QQ 按 appid 发 openid，同一台机器人在每个群里看到同一个人是同一个地址，但两台
机器人看到的是两个地址。认亲（compose 插件里的 _adopt_twin）会把两边接到同一个账号上，
可认亲之前两边各建过一次号的那两个账号还在——群里于是多出一个同名的人，AI 一人一半地
跟他说话（2026-10-18 真机：群 #77 里 AN 成了 #92 与 #161）。

用法（后端容器内；默认只看会动什么，不动库）：

    python scripts/merge_channel_accounts.py 161 92
    python scripts/merge_channel_accounts.py 161 92 --apply

合并方向永远"后来的并进早先的"：名字、群成员、说过的话都留在早先那个账号上。
只并 type='external' 的通道锚点；真人账号会被拒绝（谁是谁只有本人能决定）。
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def _main(from_id: int, to_id: int, apply: bool) -> int:
    from sqlalchemy import select, text

    from app.database import async_session
    from app.models.user import User
    from app.services.plugin.channel_user import merge_channel_accounts

    async with async_session() as db:
        users = (await db.execute(select(User).where(User.id.in_([from_id, to_id])))).scalars().all()
        found = {int(u.id): u for u in users}
        for uid in (from_id, to_id):
            user = found.get(uid)
            if user is None:
                print(f"没有 #{uid} 这个账号")
                return 2
            print(f"#{uid} {user.username}（type={user.type}）")

        counts = {}
        for label, sql in (
            ("群消息", "SELECT count(*) FROM messages WHERE sender_id = :i"),
            ("群成员", "SELECT count(*) FROM group_members WHERE member_id = :i"),
            ("私信会话", "SELECT count(*) FROM dm_sessions WHERE user1_id = :i OR user2_id = :i"),
            ("身份地址", "SELECT count(*) FROM external_identities WHERE user_id = :i"),
        ):
            counts[label] = (await db.execute(text(sql), {"i": from_id})).scalar()
        print("要搬走的东西：" + "，".join(f"{k} {v}" for k, v in counts.items()))

        merged = await merge_channel_accounts(db, from_id=from_id, to_id=to_id)
        if not merged:
            await db.rollback()
            print("没有合并（见上面的原因）")
            return 1
        if apply:
            await db.commit()
            print(f"已合并：#{from_id} → #{to_id}")
        else:
            await db.rollback()
            print("只是看了一眼（加 --apply 才真的合并）")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="合并两个通道锚点账号")
    parser.add_argument("from_id", type=int)
    parser.add_argument("to_id", type=int)
    parser.add_argument("--apply", action="store_true", help="真的写库（默认只预演）")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.from_id, args.to_id, args.apply)))
