"""
主题选色投票 — 临时统计（选色表单投票，按颜色统计用户头像）

- POST /theme-vote       提交自己的选择（覆盖式：同 user_key 覆盖，不新增）
- GET  /theme-vote/stats 返回所有投票（前端按颜色聚合头像）

存储：data/theme_votes.json（单键 upsert，进程内锁 + 原子写）。
身份：由前端负责——已登录用户调 /auth/me 拿 avatar_url 一并提交；未登录传昵称。
      后端不解析 token（保持简单，前端已可获身份信息）。
"""
import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["主题选色投票"])

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
VOTE_FILE = DATA_DIR / "theme_votes.json"
_lock = threading.Lock()


class VoteSubmit(BaseModel):
    colors: dict = Field(..., description="主题色选择 {变量key: hex}")
    user_name: str = Field(..., min_length=1, max_length=30, description="昵称（登录用户=username，未登录=自定义）")
    avatar_url: str | None = Field(None, description="头像 URL（登录用户）")


def _read_votes() -> dict:
    if not VOTE_FILE.exists():
        return {}
    try:
        return json.loads(VOTE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_votes(votes: dict) -> None:
    VOTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VOTE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(votes, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(VOTE_FILE)  # 原子替换


@router.post("/theme-vote")
async def submit_vote(req: VoteSubmit):
    user_key = req.user_name.strip() or "匿名"
    with _lock:
        votes = _read_votes()
        votes[user_key] = {
            "user_key": user_key,
            "user_name": req.user_name.strip(),
            "avatar_url": req.avatar_url or None,
            "colors": req.colors,
        }
        _write_votes(votes)
    return {"ok": True, "count": len(votes)}


@router.get("/theme-vote/stats")
async def vote_stats():
    votes = _read_votes()
    items = sorted(votes.values(), key=lambda v: (v.get("user_name") or ""))
    return {"items": items, "count": len(items)}
