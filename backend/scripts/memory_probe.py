"""AI 记忆实验台 —— 存一条记忆，看它能不能被想起来

改完记忆相关逻辑后，用它端到端看一眼真相。四步全调真实代码，不复制任何逻辑，
所以链路一改，这里立刻能看出来；它也顺便验证了「空集警告」「类型/权值」「焦段锚点」。

用法（后端容器内；这个小工具不需要 pytest）：

    python scripts/memory_probe.py                          # 默认语料跑一遍
    python scripts/memory_probe.py --say "你还记得暗号吗"     # 换一个语境
    python scripts/memory_probe.py --no-llm                 # 只看注入，不调模型
    python scripts/memory_probe.py --keep                   # 保留实验数据（默认收尾删掉）

四步：
  1. 写入  走 store_memory 工具（带类型、权值、焦段锚点），并把缓冲区落盘
  2. 召回  用 --say 那句话检索，列出命中的记忆
  3. 注入  走真实注入函数，打印 AI 实际会看到的记忆块
  4. 回答  真调一次模型，看它说不说得出来

前置：DATABASE_URL 已设置（容器内有）。模型走 AI 自己的 Key 与四层优先链；
本环境若未启用 embedding，召回会自动降级为关键词匹配，脚本会把这件事说清楚。
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def _flush_memory_buffer(db) -> int:
    """把记忆缓冲区落盘。

    后台 worker 平时攒够 5 条或等 30 秒才写，实验等不起；这里直接取空队列交给
    同一个批量写入函数，路径与线上一致。
    """
    from app.services.memory.memory_buffer import pending_memories, _batch_write_memories

    batch = []
    while not pending_memories.empty():
        batch.append(pending_memories.get_nowait())
    if batch:
        await _batch_write_memories(db, batch)
    return len(batch)


async def probe(args) -> int:
    from sqlalchemy import text

    from app.database import async_session
    from app.models.agent import Agent

    async with async_session() as db:
        # 独立脚本没有服务的启动流程：管理页写进 DB 的配置（DB 覆盖）要自己加载一次。
        # 与服务启动共用同一个入口，免得两处各写一份加载顺序。
        from app.services.infrastructure.app_config_service import load_all_configs

        await load_all_configs(db)

        agent = await db.get(Agent, args.agent)
        if agent is None:
            print(f"没有 id={args.agent} 的 AI")
            return 1
        print(f"[0] AI = {agent.name}（ai_type={agent.ai_type}）")

        from app.ai.executor import _get_api_config

        api_key, api_base, _credit, pool_key_id, provider_info = await _get_api_config(db, agent)
        print(f"    Key {'已取到' if api_key else '未取到（召回会退成关键词，模型调用会失败）'}")

        # ── 1. 写入：走真实工具路径 ──
        from app.tools.memory.store_memory import StoreMemory

        out = await StoreMemory().execute(db, agent.id, args.group, {
            "title": args.title, "content": args.content, "scope": "private",
            "mem_type": args.mem_type, "weight": args.weight,
            "session_foci": args.session_foci, "semantic_foci": args.semantic_foci,
        }, {"session_id": args.session, "api_key": api_key, "api_base_url": api_base})
        print(f"[1] 写入：类型={out['mem_type']} 权值={out['weight']} 锚点={out['anchors']}")
        if out.get("message"):
            print(f"    工具回话：{out['message']}")

        flushed = await _flush_memory_buffer(db)
        await db.commit()
        print(f"    落盘 {flushed} 条")

        # ── 2. 召回：用给出的话去检索 ──
        from app.services.memory.memory_service import recall_relevant_memories

        got = await recall_relevant_memories(
            db, agent.id, query=args.say, top_k=5,
            api_base_url=api_base, api_key=api_key,
            group_id=args.group, ai_type=agent.ai_type or "resonance",
            call_count=agent.llm_call_count or 0)
        await db.commit()
        hit = [m for m in got if m["title"] == args.title]
        print(f"[2] 召回 {len(got)} 条，其中含实验记忆：{'是' if hit else '否'}")
        for m in got:
            print(f"    - {m['title']}（{m.get('source')}）")

        # ── 3. 注入：AI 实际会看到的那块 ──
        from app.ai.llm import _build_injected_skills

        block = await _build_injected_skills(
            db, agent, args.group, args.say, api_base, api_key, None)
        print(f"[3] 注入块含实验记忆：{'是' if args.content[:6] in (block or '') else '否'}")
        print("    --- 注入块原文 ---")
        for line in (block or "(空)").splitlines():
            print(f"    {line}")

        # ── 4. 回答：真调一次模型 ──
        if args.no_llm:
            print("[4] 跳过模型调用（--no-llm）")
        else:
            from app.ai.llm import chat_completion
            from app.utils.pure.prompting import resolve_model

            model = resolve_model(
                agent, global_default_model=provider_info.get("global_default_chat_model"))
            system = (f"你是 {agent.name}，正在和一位老熟人聊天。\n\n{block or ''}")
            resp = await chat_completion(
                [{"role": "system", "content": system},
                 {"role": "user", "content": args.say}],
                model=model, api_base_url=api_base, api_key=api_key,
                pool_key_id=pool_key_id, agent_id=agent.id, db=db,
                temperature=0.3)
            print(f"[4] 模型 = {model}")
            print("    回答：", (resp or {}).get("content") or "(无内容)")

        # ── 5. 收尾 ──
        if args.keep:
            print("[5] 实验数据保留（--keep）")
        else:
            res = await db.execute(
                text("DELETE FROM rough_memories WHERE owner_id = :a AND title = :t"),
                {"a": agent.id, "t": args.title})
            await db.commit()
            print(f"[5] 已清理实验数据 {res.rowcount} 条（--keep 可保留）")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 记忆实验台：存一条记忆，看它能不能被想起来")
    parser.add_argument("--agent", type=int, default=24, help="用哪个 AI 做实验（默认 24）")
    parser.add_argument("--say", default="你还记得我最喜欢什么颜色吗", help="语境（既当检索词，也当问话）")
    parser.add_argument("--title", default="用户最喜欢的颜色", help="实验记忆的标题")
    parser.add_argument("--content", default="他最喜欢的颜色是靛蓝，我们之间的暗号是 4471。",
                        help="实验记忆的内容")
    parser.add_argument("--mem-type", default="preference", help="记忆类型（默认 preference）")
    parser.add_argument("--weight", type=int, default=4, help="设定权值 1-5（默认 4）")
    parser.add_argument("--session-foci", nargs="*", default=["all-chats"],
                        help="会话焦段 id（默认 all-chats = 任何会话都能想起）")
    parser.add_argument("--semantic-foci", nargs="*", default=[], help="语义焦段 id")
    parser.add_argument("--group", type=int, default=None, help="按群聊语境实验（默认私信）")
    parser.add_argument("--session", default="probe_dm", help="实验用的会话键（默认 probe_dm）")
    parser.add_argument("--no-llm", action="store_true", help="只看到注入，不调模型")
    parser.add_argument("--keep", action="store_true", help="保留实验数据（默认收尾删掉）")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
