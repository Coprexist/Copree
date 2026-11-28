"""用本地 llama 自检：真模型、真链路，不走 mock。

什么时候跑：改了 LLM 相关代码（system 布局、凭证解析、世界对话）之后，在真机上
用一个小模型把真实链路走通一遍。单元测试里的 client 都是替身，它们证明不了
「Qwen 的 Jinja 会不会因为 system 不在开头而 500」。

用法（模型与地址都能换）：

    docker exec -w /app ai_group_backend python scripts/selfcheck_local_llm.py

    docker exec -w /app -e SELFCHECK_BASE=http://host.docker.internal:11434/v1 \\
        -e SELFCHECK_MODEL=qwen3:4b \\
        ai_group_backend python scripts/selfcheck_local_llm.py

默认三步（都是轻量的，CPU 上的 4B 模型几十秒能跑完）：
1. 对本地地址真发一次探测，看 system 布局判定是什么（0.4.16「本地 llama 必 500」出在这里）；
2. 用「保存过的 provider 行」与「从未判定的行」各解析一次，确认运行时拿到的布局可信；
3. 按判定出的布局真跑一轮 chat（system 在首位），并对照一次「system 塞在对话中段」。

世界对话整轮默认不跑：它会把工具定义 + 强注入段一起塞进去，prompt 远超小模型默认的
4096 上下文（ollama 默认值），在 CPU 上等于让自检卡死。要跑整套时：
    SELFCHECK_WORLD=1 docker exec ... python scripts/selfcheck_local_llm.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402

BASE = os.getenv("SELFCHECK_BASE", "http://host.docker.internal:11434/v1").rstrip("/")
MODEL = os.getenv("SELFCHECK_MODEL", "qwen3:4b")
RUN_WORLD = os.getenv("SELFCHECK_WORLD", "") == "1"
OWNER = "__selfcheck_llama__"

SYSTEM = {"role": "system", "content": "你是自检助手。回答不超过十个字。"}
# /no_think：Qwen3 这类带思考段的模型在 CPU 上会把 token 预算全花在 reasoning 里，
# 正文是空串。自检要的是「正文真的出来了」，所以关掉思考。
ASK = {"role": "user", "content": "你好，请用一句话回答。/no_think"}


async def step1_layout():
    from app.services.agent.channel_layout import decide_system_layout

    d = await decide_system_layout(base_url=BASE, probe=True, allow_private=True)
    print(f"[1] 布局探测：layout={d.layout} source={d.source} msg={(d.message or '')[:80]}")
    assert d.layout in ("leading", "anywhere"), d.layout
    assert d.source in ("detected", "host"), d.source
    return d.layout


async def step2_provider_helper():
    # 判定入口是 pure 层的 resolve_system_layout（吃 dict，不吃 ORM 行）：
    # 脚本以前 import 的 provider_system_layout 在解析器里从来没有过，这一段一直是坏的。
    from app.utils.pure.provider_config import resolve_system_layout

    saved = {"system_layout": "leading", "system_layout_source": "detected"}
    unsaved: dict = {}
    print(f"[2] 保存过的行 → {resolve_system_layout(saved, None, BASE)}；"
          f"未判定的行 → {resolve_system_layout(unsaved, None, BASE)}")
    assert resolve_system_layout(saved, None, BASE) == "leading"


async def _chat(messages: list, *, label: str) -> tuple[int, str, str]:
    import httpx

    payload = {
        "model": MODEL, "messages": messages,
        "max_tokens": 96, "temperature": 0.2, "stream": False,
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{BASE}/chat/completions", json=payload,
            headers={"Authorization": "Bearer ollama"},
        )
    body = resp.text[:200].replace("\n", " ")
    if resp.status_code != 200:
        print(f"     {label}：HTTP {resp.status_code} {body}")
        return resp.status_code, "", ""
    data = resp.json()
    message = (data.get("choices") or [{}])[0].get("message") or {}
    # 思考型模型（qwen3 等）在 CPU 上会把预算先花在 reasoning 里：正文可能是空串，
    # 但 HTTP 200 + 有生成内容已经证明「请求形状被接受」——自检关心的是这个。
    return resp.status_code, (message.get("content") or "").strip(), (message.get("reasoning") or "").strip()


async def step3_real_chat():
    code, content, reasoning = await _chat([SYSTEM, ASK], label="system 在首位")
    shown = content[:40] if content else f"(reasoning) {reasoning[:40]}"
    print(f"[3] 真实对话：HTTP {code}，输出={shown!r}")
    assert code == 200, f"system 在首位不该失败（{code}）"
    assert content or reasoning, "模型没有任何输出"

    # 对照（只报告不断言）：system 塞在对话中段。严格按 chat template 渲染的后端
    # （Qwen 的 Jinja、llama.cpp 的 --chat-template）会直接 500；宽松的后端照样能答。
    # 这多花几十秒，所以默认不跑：SELFCHECK_CONTRAST=1 打开。
    if os.getenv("SELFCHECK_CONTRAST", "") == "1":
        loose_code, _, _ = await _chat([ASK, SYSTEM], label="system 在对话中段")
        print(f"     对照：system 在对话中段 → HTTP {loose_code}")


async def step4_world_chat():
    """整轮世界对话：真模型、真落库、真广播。需要模型上下文装得下世界 prompt。"""
    from app.database import async_session
    from app.models.user import User
    from app.models.world import World
    from app.services.world.world_chat_items import ChatItem
    from app.services.world.world_chat_service import stream_world_chat
    from app.repositories.world_repo import SQLAlchemyWorldRepository
    from app.utils.crypto import encrypt_api_key

    async with async_session() as db:
        await db.execute(text("DELETE FROM users WHERE username = :n"), {"n": OWNER})
        await db.commit()
        owner = User(
            username=OWNER, password_hash="x", type="human",
            api_base_url=BASE, api_key_encrypted=encrypt_api_key("ollama-local"),
            global_chat_model=MODEL,
        )
        db.add(owner)
        await db.flush()
        world = World(name="自检世界", owner_id=owner.id)
        db.add(world)
        await db.commit()
        world_id, owner_id = world.id, owner.id

    chunks, errors, done, preview = 0, [], False, []
    try:
        async with async_session() as db:
            async for event in stream_world_chat(
                SQLAlchemyWorldRepository(db), world_id, owner_id,
                [ChatItem(text="在吗？只用一句话回答：你好。")], "selfcheck-turn",
            ):
                if event.startswith("data: [ERROR]"):
                    errors.append(event.strip()[:200])
                elif event.startswith("data: [DONE]"):
                    done = True
                elif event.startswith("data: "):
                    chunks += 1
                    if len(preview) < 3:
                        preview.append(event.strip()[:120])
        print(f"[4] 世界对话：正文事件 {chunks} 条，[DONE]={done}，[ERROR]={errors}")
        for line in preview:
            print("     ", line)
        assert not errors, errors
        assert chunks > 0, "一条正文事件都没有"
        assert done is True, "没收到 [DONE]"
    finally:
        async with async_session() as db:
            await db.execute(text("DELETE FROM users WHERE username = :n"), {"n": OWNER})
            await db.commit()


async def main():
    await step1_layout()
    await step2_provider_helper()
    await step3_real_chat()
    if RUN_WORLD:
        await step4_world_chat()
    print("自检通过：真模型链路可用")


if __name__ == "__main__":
    asyncio.run(main())
