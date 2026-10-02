"""本地存档：状态、最近结果、AI 结果缓存。

三个文件职责分明，坏掉一个不影响另外两个：
- state.json   上次运行时间 + 已见条目指纹（决定「这次要抓哪段时间」）
- latest.json  最近一次整理好的卡片（界面启动时先读它，做到「开机秒显」，不必等网络）
- ai_cache.json 条目指纹 -> 概括结果（避免重复调用 AI 计费）
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path

from . import textutil
from .models import RunResult


def atomic_write_json(path: Path, payload) -> None:
    """先写临时文件再替换：一体机可能被直接断电，半个 JSON 会让程序再也起不来。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.latest_path = self.dir / "latest.json"
        self.cache_path = self.dir / "ai_cache.json"

    # ---------------- 运行状态 ----------------
    def load_state(self) -> dict:
        state = read_json(self.state_path, default={}) or {}
        if not isinstance(state, dict):
            state = {}
        state.setdefault("last_run", "")
        state.setdefault("seen", {})
        state.setdefault("runs", 0)
        state.setdefault("last_error", "")
        if not isinstance(state.get("seen"), dict):
            state["seen"] = {}
        return state

    def save_state(self, state: dict) -> None:
        atomic_write_json(self.state_path, state)

    def last_run(self) -> _dt.datetime | None:
        return textutil.parse_time(self.load_state().get("last_run"))

    def prune_seen(self, state: dict, keep_days: int = 30, max_entries: int = 20000) -> dict:
        """指纹表要定期瘦身：长期运行后它会变成几万条，拖慢启动。"""
        cutoff = textutil.now_utc() - _dt.timedelta(days=keep_days)
        seen = state.get("seen") or {}
        kept = {}
        for fp, stamp in seen.items():
            dt = textutil.parse_time(stamp)
            if dt is not None and dt >= cutoff:
                kept[fp] = stamp
        if len(kept) > max_entries:
            # 超出上限时按时间保留最近的
            ordered = sorted(kept.items(), key=lambda kv: textutil.parse_time(kv[1]) or textutil.now_utc(),
                             reverse=True)
            kept = dict(ordered[:max_entries])
        state["seen"] = kept
        return state

    # ---------------- 最近结果 ----------------
    def load_latest(self) -> RunResult | None:
        raw = read_json(self.latest_path)
        if not raw:
            return None
        try:
            result = RunResult.from_dict(raw)
        except (TypeError, ValueError, KeyError):
            return None
        return result if result.blocks else None

    def save_latest(self, result: RunResult) -> None:
        atomic_write_json(self.latest_path, result.to_dict())

    # ---------------- AI 缓存 ----------------
    def load_cache(self) -> dict:
        cache = read_json(self.cache_path, default={}) or {}
        return cache if isinstance(cache, dict) else {}

    def save_cache(self, cache: dict, keep_days: int = 10) -> None:
        cutoff = textutil.now_utc() - _dt.timedelta(days=max(1, int(keep_days)))
        pruned = {}
        for fp, entry in (cache or {}).items():
            if not isinstance(entry, dict):
                continue
            at = textutil.parse_time(entry.get("at"))
            if at is None or at >= cutoff:
                pruned[fp] = entry
        atomic_write_json(self.cache_path, pruned)
