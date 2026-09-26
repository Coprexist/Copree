"""单文件 JSON 存储：读-改-写共用一把锁 + 原子替换。

临时统计类接口（投票等）共用这一份实现：进程内锁防并发写坏文件，
先写临时文件再 replace，避免别的进程读到半截 JSON。
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class JsonStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    @contextmanager
    def edit(self) -> Iterator[dict]:
        """锁内读-改-写：退出 with 时自动落盘"""
        with self._lock:
            data = self.read()
            yield data
            self.write(data)
