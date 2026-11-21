"""一段会话一帧：把存储里同一 context_ref 的重复帧归并成一条

旧版本「本会话的帧被移出运行集合之后，下一次消息再新建一条」：线上实测某个 AI 的 group:66
攒了 167 条帧，全是同一段会话的副本——环境锁在最活跃那条上，其余多数是空壳。这些副本让
每次读帧数组都得扫一遍历史区，也让"这段会话是哪一帧"只能靠挑规则回答。

这次一次性归并：同一 context_ref 只留一帧（当前帧优先，其次最近激活），被归并帧上还有内容
的字段并进幸存者（环境锁、便签副本、待告知、原文尾巴、调用计数），再把指向被归并帧的长期
指针改到幸存者（闹钟记的 frame_id、状态链节点），最后删掉那几条帧。

归并规则内联在迁移里、不 import 应用代码：已经发布的迁移是一份快照，不能跟着以后的重构
改变行为。运行时那套规则在 utils/pure/state_stack.py（merge_same_context）。

Revision ID: 0109
Revises: 0108
Create Date: 2026-11-11
"""
from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0109'
down_revision: Union[str, None] = '0108'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── 归并规则：与 merge_same_context 同一张表（快照）──

def _blank(value) -> bool:
    """这个字段算「没写过」吗（归并时只在空白处补）。"""
    return value is None or value == "" or value == {} or value == []


def _union(a, b) -> list:
    out = list(a or [])
    for item in (b or []):
        if item not in out:
            out.append(item)
    return out


def _merge_handoff(keeper, other) -> dict:
    out = dict(keeper or {})
    for key, value in (other or {}).items():
        if key == "tail":
            out["tail"] = _union(out.get("tail"), value)
        elif _blank(out.get(key)):
            out[key] = value
    return out


# 与运行时 _MERGE_SKIP_FIELDS 同表：身份/时序 + 只属于被并帧的标记
_MERGE_SKIP = ("id", "created_at", "status", "last_active_at", "merged_into", "retired_at")


def _merge_into(keeper: dict, frame: dict) -> None:
    for key, value in frame.items():
        if key in _MERGE_SKIP or _blank(value):
            continue
        if key == "call_count":
            keeper[key] = int(keeper.get(key) or 0) + int(value or 0)
        elif key == "tool_uses":
            cur = dict(keeper.get(key) or {})
            for k, v in (value or {}).items():
                cur[k] = max(int(cur.get(k) or 0), int(v or 0))
            keeper[key] = cur
        elif key == "delivered":
            cur = dict(keeper.get(key) or {})
            for k, v in (value or {}).items():
                cur.setdefault(k, v)
            keeper[key] = cur
        elif key in ("pending_notices", "notes", "tail"):
            keeper[key] = _union(keeper.get(key), value)
        elif key in ("handoff", "completed_handoff"):
            keeper[key] = _merge_handoff(keeper.get(key), value)
        elif _blank(keeper.get(key)):
            keeper[key] = value


def _merge_duplicates(stack: list) -> list:
    """把同一 context_ref 的重复帧并进各自那一帧，返回被并的帧（并从数组里摘掉）。

    幸存的规则：当前帧优先，其次最近激活——与运行时一致。被并帧在返回前带上 merged_into，
    调用方据此把指向它的指针改到幸存者。
    """
    groups: dict[str, list[dict]] = {}
    for frame in stack:
        if not isinstance(frame, dict):
            continue
        ref = str(frame.get("context_ref") or "")
        if ref:
            groups.setdefault(ref, []).append(frame)

    merged: list[dict] = []
    for frames in groups.values():
        if len(frames) < 2:
            continue
        keeper = max(frames, key=lambda f: (
            f.get("status") == "active",
            str(f.get("last_active_at") or f.get("created_at") or ""),
        ))
        stamps = [str(f.get("last_active_at") or f.get("created_at") or "") for f in frames]
        for frame in frames:
            if frame is keeper:
                continue
            _merge_into(keeper, frame)
            frame["merged_into"] = str(keeper.get("id") or "")
            merged.append(frame)
        # 刚被用过的那一帧，时间戳不该停在旧值上（容量闸按它挑"最久没用"）
        if max(stamps):
            keeper["last_active_at"] = max(stamps)

    gone = {id(frame) for frame in merged}
    stack[:] = [frame for frame in stack if id(frame) not in gone]
    return merged


def _rebind(bind, agent_id: int, lost: str, keep: str) -> None:
    """把指向被归并帧的长期指针改到幸存者：计划板记的 frame_id、状态链节点。"""
    if not lost:
        return
    bind.execute(sa.text(
        "UPDATE agent_alarms SET frame_id = :keep WHERE agent_id = :aid AND frame_id = :lost"),
        {"keep": keep or None, "aid": agent_id, "lost": lost})
    if not keep:
        return
    keep_node = bind.execute(sa.text(
        "SELECT 1 FROM agent_state_chain_node WHERE agent_id = :aid AND frame_id = :keep"),
        {"aid": agent_id, "keep": keep}).first()
    if keep_node:
        # 幸存者本就在链上：副本的节点是多余的（一帧只能挂一个节点）
        bind.execute(sa.text(
            "DELETE FROM agent_state_chain_node WHERE agent_id = :aid AND frame_id = :lost"),
            {"aid": agent_id, "lost": lost})
    else:
        bind.execute(sa.text(
            "UPDATE agent_state_chain_node SET frame_id = :keep "
            "WHERE agent_id = :aid AND frame_id = :lost"),
            {"aid": agent_id, "keep": keep, "lost": lost})


def upgrade() -> None:
    bind = op.get_bind()
    # JSONB 归并用 PG 的写法；别的方言（本地 sqlite）没有历史数据可清
    if bind.dialect.name != "postgresql":
        return
    rows = bind.execute(sa.text(
        "SELECT id, state_stack FROM agents WHERE state_stack IS NOT NULL")).fetchall()
    merged_total = 0
    for agent_id, raw in rows:
        try:
            stack = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(stack, list) or not stack:
            continue
        merged = _merge_duplicates(stack)
        if not merged:
            continue
        for frame in merged:
            _rebind(bind, agent_id, str(frame.get("id") or ""),
                    str(frame.get("merged_into") or ""))
        bind.execute(sa.text(
            "UPDATE agents SET state_stack = CAST(:s AS jsonb) WHERE id = :i"),
            {"s": json.dumps(stack, ensure_ascii=False), "i": agent_id})
        merged_total += len(merged)
    print(f"[0109] 同一段会话的重复帧归并 {merged_total} 条")


def downgrade() -> None:
    """重复帧是同一段状态的副本，归并后无法还原（也不需要）；这里不回滚数据。"""
    pass
