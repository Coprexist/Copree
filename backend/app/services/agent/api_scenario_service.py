"""默认配置的场景：清单、有序序列、开关与失败状态

这一层只做数据，不碰 HTTP（路由见 routers/user_api_config_scenarios.py）。
放 service 而不是塞进 router，是因为三处都要用到同一份规则：
设置页、解析链的取数、以及失败状态回写——规则只有一份才不会各说各话。

设计依据：docs/dev/api_config_chain.md §3.4。要点复述：

- 每个场景一条**有序**配置序列（position 升序，逐格尝试）；没有序列 = 该场景第 ③ 格为空。
- **总开关**只记「关掉的」：没有记录即开启，新场景上线不用写数据迁移。
- **子开关**（每格 enabled）关掉后保留在序列里，只是不参与尝试，不删除。
- **失败状态**按 (场景, 配置) 唯一；成功后 state 转 ok，但 unread 只由用户阅读清除。
"""
from __future__ import annotations

from sqlalchemy import select, delete

from app.utils.pure.api_scenarios import SCENARIOS, get_scenario


def _profile_brief(profile, provider) -> dict:
    return {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "provider_id": provider.id,
        "provider_name": provider.name,
        "primary": profile.primary_model or provider.default_primary or "",
    }


async def list_scenarios(db, user_id: int) -> list[dict]:
    """场景清单 + 每个场景的序列与失败状态

    清单来自 pure 层（唯一来源）；这里只补用户态：总开关、序列、每格最近一次状态。
    """
    from app.models.api_profile import (
        UserApiProfile, UserApiProvider, UserApiScenarioOff, UserApiScenarioRoute,
        UserApiScenarioStatus,
    )

    off = set((await db.execute(
        select(UserApiScenarioOff.scenario).where(UserApiScenarioOff.user_id == user_id)
    )).scalars().all())

    route_rows = (await db.execute(
        select(UserApiScenarioRoute, UserApiProfile, UserApiProvider)
        .join(UserApiProfile, UserApiProfile.id == UserApiScenarioRoute.profile_id)
        .join(UserApiProvider, UserApiProvider.id == UserApiProfile.provider_id)
        .where(UserApiScenarioRoute.user_id == user_id)
        .order_by(UserApiScenarioRoute.scenario, UserApiScenarioRoute.position,
                  UserApiScenarioRoute.id)
    )).all()

    status_rows = (await db.execute(
        select(UserApiScenarioStatus).where(UserApiScenarioStatus.user_id == user_id)
    )).scalars().all()
    status_of = {(s.scenario, s.profile_id): s for s in status_rows}

    routes_of: dict[str, list[dict]] = {}
    for route, profile, provider in route_rows:
        item = _profile_brief(profile, provider)
        item.update({
            "position": route.position,
            "enabled": bool(route.enabled),
        })
        st = status_of.get((route.scenario, profile.id))
        item["status"] = None if st is None else {
            "state": st.state, "reason": st.reason, "unread": bool(st.unread),
        }
        routes_of.setdefault(route.scenario, []).append(item)

    out = []
    for scenario in SCENARIOS:
        routes = routes_of.get(scenario.key, [])
        enabled_routes = [r for r in routes if r["enabled"]]
        out.append({
            "key": scenario.key,
            "kind": scenario.kind,
            "enabled": scenario.key not in off,
            "routes": routes,
            # 界面上的「当前优先使用」：序列里第一条启用的，不是真实请求结果——
            # 真实请求结果由 AI 详情的 stepper 回答（同一份 Resolution）。
            "first_enabled_profile_id": enabled_routes[0]["profile_id"] if enabled_routes else None,
        })
    return out


async def set_scenario_enabled(db, user_id: int, key: str, enabled: bool) -> dict:
    """总开关：开 = 删记录，关 = 留一行（「没有记录 = 开」在库里永远成立）"""
    from app.models.api_profile import UserApiScenarioOff
    from fastapi import HTTPException

    if get_scenario(key) is None:
        raise HTTPException(status_code=404, detail="没有这个场景")
    row = (await db.execute(
        select(UserApiScenarioOff).where(
            UserApiScenarioOff.user_id == user_id, UserApiScenarioOff.scenario == key,
        )
    )).scalars().first()
    if enabled:
        if row is not None:
            await db.delete(row)
    elif row is None:
        db.add(UserApiScenarioOff(user_id=user_id, scenario=key))
    await db.flush()
    return {"key": key, "enabled": enabled}


async def _own_route_profile(db, user_id: int, profile_id: int):
    """校验配置档属于这个人：场景序列只能挂自己的档，不允许跨账号"""
    from fastapi import HTTPException
    from app.models.api_profile import UserApiProfile

    profile = await db.get(UserApiProfile, profile_id)
    if profile is None or profile.user_id != user_id:
        raise HTTPException(status_code=404, detail="配置档不存在")
    return profile


async def add_route(db, user_id: int, key: str, profile_id: int) -> dict:
    """在序列末尾追加一格；同一档重复添加是幂等的（唯一键在库上）"""
    from app.models.api_profile import UserApiScenarioRoute
    from sqlalchemy import func

    profile = await _own_route_profile(db, user_id, profile_id)
    exists = (await db.execute(
        select(UserApiScenarioRoute).where(
            UserApiScenarioRoute.user_id == user_id,
            UserApiScenarioRoute.scenario == key,
            UserApiScenarioRoute.profile_id == profile.id,
        )
    )).scalars().first()
    if exists is None:
        last = (await db.execute(
            select(func.max(UserApiScenarioRoute.position)).where(
                UserApiScenarioRoute.user_id == user_id,
                UserApiScenarioRoute.scenario == key,
            )
        )).scalar()
        db.add(UserApiScenarioRoute(
            user_id=user_id, scenario=key, profile_id=profile.id,
            position=(last or 0) + 1, enabled=True,
        ))
        await db.flush()
    return {"profile_id": profile.id}


async def save_routes(db, user_id: int, key: str, items: list[dict]) -> list[dict]:
    """整体保存序列：数组顺序即 position，items 里没出现的档从序列移除

    用「整体保存」而不是逐条增删改：排序、子开关、删除在界面上是一次操作，
    拆成多个请求就会出现「顺序存了一半」的中间态。
    """
    from app.models.api_profile import UserApiScenarioRoute

    rows = (await db.execute(
        select(UserApiScenarioRoute).where(
            UserApiScenarioRoute.user_id == user_id,
            UserApiScenarioRoute.scenario == key,
        )
    )).scalars().all()
    keep = {int(i["profile_id"]): bool(i.get("enabled", True)) for i in items}
    for row in rows:
        if row.profile_id not in keep:
            await db.delete(row)
    by_profile = {r.profile_id: r for r in rows}
    for position, (profile_id, enabled) in enumerate(keep.items(), start=1):
        await _own_route_profile(db, user_id, profile_id)
        row = by_profile.get(profile_id)
        if row is None:
            db.add(UserApiScenarioRoute(
                user_id=user_id, scenario=key, profile_id=profile_id,
                position=position, enabled=enabled,
            ))
        else:
            row.position = position
            row.enabled = enabled
    await db.flush()
    return [{"profile_id": pid, "position": i + 1, "enabled": en}
            for i, (pid, en) in enumerate(keep.items())]


async def remove_route(db, user_id: int, key: str, profile_id: int) -> None:
    from app.models.api_profile import UserApiScenarioRoute

    await db.execute(delete(UserApiScenarioRoute).where(
        UserApiScenarioRoute.user_id == user_id,
        UserApiScenarioRoute.scenario == key,
        UserApiScenarioRoute.profile_id == profile_id,
    ))
    await db.flush()


async def record_attempt(db, user_id: int, key: str, profile_id: int, *,
                         ok: bool, reason: str | None = None) -> bool:
    """记一次尝试结果，返回「是否该发站内消息」

    同一格连续失败只发一次：判断依据是它本来就已经是 failed + unread。
    成功只把 state 转回 ok，**不清 unread**——红点只由用户阅读消除（§3.4 R8）。
    """
    from app.models.api_profile import UserApiScenarioStatus

    row = (await db.execute(
        select(UserApiScenarioStatus).where(
            UserApiScenarioStatus.user_id == user_id,
            UserApiScenarioStatus.scenario == key,
            UserApiScenarioStatus.profile_id == profile_id,
        )
    )).scalars().first()
    should_notify = False
    if row is None:
        row = UserApiScenarioStatus(
            user_id=user_id, scenario=key, profile_id=profile_id,
            state="failed" if not ok else "ok",
            reason=reason, unread=not ok,
        )
        db.add(row)
        should_notify = not ok
    else:
        if ok:
            row.state = "ok"
            row.reason = None
        else:
            should_notify = not (row.state == "failed" and row.unread)
            row.state = "failed"
            row.reason = reason
            row.unread = True
    await db.flush()
    return should_notify


async def ack_status(db, user_id: int, key: str, profile_id: int) -> dict:
    """用户读过：只清 unread，历史 state 保留（供下次判断是否重复发消息）"""
    from app.models.api_profile import UserApiScenarioStatus

    row = (await db.execute(
        select(UserApiScenarioStatus).where(
            UserApiScenarioStatus.user_id == user_id,
            UserApiScenarioStatus.scenario == key,
            UserApiScenarioStatus.profile_id == profile_id,
        )
    )).scalars().first()
    if row is not None:
        row.unread = False
        await db.flush()
    return {"profile_id": profile_id, "unread": False}
