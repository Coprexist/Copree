"""配置：内置默认值 + config.json 覆盖 + 环境变量兜底。

默认值必须能独立跑通（把 AI key 留空也能出新闻），否则一体机上第一次启动就白屏，
使用者无从判断是配置问题还是程序问题。所以这里的每条默认值都按「开箱可用」挑选。
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

# 新闻源清单按实测可用性挑选：2024-2025 年间直连返回 200 且是真 RSS/JSON 的才默认开启。
# 其余（36氪/果壳/虎嗅/央视/澎湃的公开 RSS 已改版返回 HTML，微博热搜需登录 Cookie，
# RSSHub 公共实例在校园网常不可达）一律 enabled=false 保留在配置里，方便以后按需打开。
DEFAULT_CONFIG: dict = {
    "app": {
        "title": "每日新闻速览",
        "subtitle": "作文素材 · 科技 · 时事 · 新奇",
        # 每天几点自动刷新一次（本地时间）
        "refresh_hour": 6,
        "refresh_minute": 45,
        # 开机启动时，距上次抓取超过这么多小时就先立刻抓一次，避免早上第一节课还是昨天的新闻
        "startup_stale_hours": 8,
        # 首次运行（没有历史记录）时回溯多久；上限防止关机一周后一次抓回上百条
        "window_hours_default": 24,
        "max_window_hours": 96,
        "data_dir": "data",
        "log_dir": "logs",
        "log_level": "INFO",
        "fetch_workers": 8,
        "http_timeout": 15,
        "http_retries": 2,
        # 判定「两条新闻是同一件事」的相似度阈值：调高合并更保守（卡片更多、可能重复），
        # 调低合并更激进（卡片更少、可能把两件事并成一条）。默认值按真实标题对校准过。
        "cluster_threshold": 0.42,
        # 校园网常做 SSL 中间人，开启后证书校验失败的源会降级重试（仅用于抓公开新闻）
        "allow_insecure_fallback": False,
        "proxy": "",
    },
    "ai": {
        "enabled": True,
        # 任意 OpenAI 兼容接口都行：DeepSeek / 智谱 / 通义 / Kimi / 本地 Ollama
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "",
        "api_key_env": "NEWSCAST_API_KEY",
        "temperature": 0.3,
        "max_tokens": 4000,
        "timeout": 120,
        # 控制单次花费：每条新闻只截断送入，分批调用，批数封顶
        "max_items_per_call": 22,
        "max_batches": 3,
        "max_summary_chars": 90,
        # AI 结果按条目指纹缓存，重复运行不再重复计费
        "cache_days": 10,
    },
    "search": {
        # 可选：用搜索 API 补充 RSS 覆盖不到的内容（RSS 已能覆盖日常需求，默认关闭）
        "enabled": False,
        "provider": "tavily",
        "api_key": "",
        "api_key_env": "NEWSCAST_SEARCH_KEY",
        "queries": ["今日重大新闻", "今日科技新闻", "今日国内时事", "今日新奇趣闻"],
        "results_per_query": 8,
    },
    "categories": [
        {
            "key": "top",
            "label": "大事件",
            "accent": "#ff5f56",
            "max": 4,
            "keywords": ["地震", "台风", "洪水", "暴雨", "山洪", "决堤", "事故", "爆炸", "火灾",
                         "战争", "冲突", "袭击", "空袭", "疫情", "遇难", "死亡", "失联", "救援",
                         "主席", "总理", "国务院", "两会", "中央", "政治局", "发布", "通报",
                         "发射", "飞船", "空间站", "峰会", "签署", "开幕", "闭幕", "重大", "紧急"],
        },
        {
            "key": "politics",
            "label": "时政·社会",
            "accent": "#ffb74d",
            "max": 4,
            "keywords": ["政策", "民生", "教育", "高考", "中考", "医保", "就业", "养老", "改革",
                         "法院", "立法", "部委", "通知", "规定", "补贴", "乡村", "城市", "交通",
                         "校园", "学生", "教师", "家长", "就业", "住房", "物价", "经济", "外交"],
        },
        {
            "key": "tech",
            "label": "科技前沿",
            "accent": "#4fc3f7",
            "max": 4,
            "keywords": ["人工智能", "AI", "大模型", "芯片", "半导体", "量子", "航天", "火箭",
                         "卫星", "机器人", "算法", "互联网", "电池", "新能源", "生物", "基因",
                         "医学", "物理", "化学", "材料", "6G", "5G", "自动驾驶", "无人机",
                         "天文", "望远镜", "核聚变", "脑机", "诺奖"],
        },
        {
            "key": "novelty",
            "label": "新奇趣闻",
            "accent": "#9ccc65",
            "max": 4,
            "keywords": ["罕见", "首次", "奇", "怪", "趣", "意外", "巧合", "打破", "纪录",
                         "吉尼斯", "萌", "暖心", "流浪", "发现", "走红", "爆火", "反转",
                         "幸运", "最长", "最大", "最小", "最老", "最年轻", "哭笑不得"],
        },
    ],
    "sources": [
        # ---- 科技 ----
        {"name": "IT之家", "type": "rss", "url": "https://www.ithome.com/rss/", "hint": "tech", "enabled": True},
        {"name": "Solidot", "type": "rss", "url": "https://www.solidot.org/index.rss", "hint": "tech", "enabled": True},
        {"name": "少数派", "type": "rss", "url": "https://sspai.com/feed", "hint": "tech", "enabled": True},
        {"name": "爱范儿", "type": "rss", "url": "https://www.ifanr.com/feed", "hint": "tech", "enabled": True},
        {"name": "人民网·科技", "type": "rss", "url": "http://www.people.com.cn/rss/scitech.xml", "hint": "tech", "enabled": True},
        # ---- 时政 / 社会 ----
        {"name": "人民网·时政", "type": "rss", "url": "http://www.people.com.cn/rss/politics.xml", "hint": "politics", "enabled": True},
        {"name": "人民网·社会", "type": "rss", "url": "http://www.people.com.cn/rss/society.xml", "hint": "politics", "enabled": True},
        {"name": "人民网·教育", "type": "rss", "url": "http://www.people.com.cn/rss/edu.xml", "hint": "politics", "enabled": True},
        {"name": "人民网·文化", "type": "rss", "url": "http://www.people.com.cn/rss/culture.xml", "hint": "politics", "enabled": True},
        {"name": "新华网·时政", "type": "rss", "url": "http://www.xinhuanet.com/politics/news_politics.xml", "hint": "politics", "enabled": True},
        {"name": "中国新闻网", "type": "rss", "url": "https://www.chinanews.com.cn/rss/scroll-news.xml", "hint": "politics", "enabled": True},
        {"name": "中新网·社会", "type": "rss", "url": "https://www.chinanews.com.cn/rss/society.xml", "hint": "politics", "enabled": True},
        {"name": "界面新闻", "type": "rss", "url": "https://a.jiemian.com/index.php?m=article&a=rss", "hint": "politics", "enabled": True},
        # ---- 国际（大事件补充）----
        {"name": "人民网·国际", "type": "rss", "url": "http://www.people.com.cn/rss/world.xml", "hint": "top", "enabled": True},
        {"name": "共同网", "type": "rss", "url": "https://china.kyodonews.net/rss/news.xml", "hint": "top", "enabled": True},
        # ---- 热搜榜（JSON 接口，最能出「新奇」和「大事件」）----
        {"name": "百度热搜", "type": "baidu_hot", "url": "https://top.baidu.com/api/board?platform=wise&tab=realtime",
         "hint": "top", "enabled": True},
        {"name": "头条热榜", "type": "toutiao_hot", "url": "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
         "hint": "top", "enabled": True},
        {"name": "知乎日报", "type": "zhihu_daily", "url": "https://news-at.zhihu.com/api/4/news/latest",
         "hint": "novelty", "enabled": True},
        # ---- 以下为实测已改版/需登录/校园网不可达，保留备用 ----
        {"name": "36氪", "type": "rss", "url": "https://36kr.com/feed", "hint": "tech", "enabled": False},
        {"name": "果壳", "type": "rss", "url": "https://www.guokr.com/rss/", "hint": "novelty", "enabled": False},
        {"name": "澎湃新闻", "type": "rss", "url": "https://www.thepaper.cn/rss_newsDetail_25950.xml", "hint": "top", "enabled": False},
        {"name": "央视新闻", "type": "rss", "url": "http://news.cctv.com/rss/china.xml", "hint": "top", "enabled": False},
        {"name": "微博热搜", "type": "weibo_hot", "url": "https://weibo.com/ajax/side/hotSearch", "hint": "top", "enabled": False},
        {"name": "BBC中文", "type": "rss", "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml", "hint": "top", "enabled": False},
    ],
    "ui": {
        # workerw=挂到壁纸层（能压住「显示桌面」，但会被桌面图标盖住）
        # bottom =始终压到窗口最底层（图标层之上、所有窗口之下）
        # normal =普通窗口（非 Windows 或调试时用）
        "desktop_mode": "auto",
        "transparent_color": "#010203",
        # 整窗透明度：面板会随壁纸透出来，文字保持清晰
        "alpha": 0.9,
        "click_through": False,
        "font_family": ["微软雅黑", "Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "sans-serif"],
        # 一体机分辨率差异大，字号整体缩放；1.0 对应 1920x1080
        "font_scale": 1.0,
        "monitor": 0,
        "margin": {"left": 34, "top": 26, "right": 34, "bottom": 22},
        "columns": 2,
        "rows": 2,
        "page_rotate_seconds": 25,
        "summary_lines": 3,
        "angle_lines": 1,
        "show_angle": True,
        "show_sources": True,
        "colors": {
            "panel": "#0d1117",
            "panel_edge": "#1f2a37",
            "title": "#f2f6fc",
            "summary": "#c6d0dc",
            "angle": "#8fa3b8",
            "muted": "#6b7a8c",
            "header": "#e8eef6",
        },
        "hotkeys": {
            "enabled": True,
            "refresh": "ctrl+alt+r",
            "toggle_visible": "ctrl+alt+n",
            "click_through": "ctrl+alt+t",
            "quit": "ctrl+alt+q",
        },
    },
}


def _project_dir() -> Path:
    """源码运行时是包目录的上一级；PyInstaller 打包后是 exe 所在目录。

    打包后必须让 config.json / data / logs 落在 exe 旁边，否则用户改配置无从下手。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_DIR = _project_dir()


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：覆盖只写想改的键，其余沿用默认值。

    源清单是特例——按 name 数组整体替换，逐项合并会让「删掉某个源」无法表达。
    """
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key == "sources" and isinstance(value, list):
            result[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _coerce_types(cfg: dict) -> None:
    """就地纠正 config.json 里手写的类型错误（字符串数字、"true" 等）。"""
    app = cfg["app"]
    for key in ("refresh_hour", "refresh_minute", "startup_stale_hours", "fetch_workers",
                "http_timeout", "http_retries"):
        try:
            app[key] = int(app[key])
        except (TypeError, ValueError):
            app[key] = DEFAULT_CONFIG["app"][key]
    for key in ("window_hours_default", "max_window_hours"):
        try:
            app[key] = float(app[key])
        except (TypeError, ValueError):
            app[key] = DEFAULT_CONFIG["app"][key]
    app["refresh_hour"] = min(23, max(0, app["refresh_hour"]))
    app["refresh_minute"] = min(59, max(0, app["refresh_minute"]))
    app["allow_insecure_fallback"] = bool(app.get("allow_insecure_fallback"))

    ui = cfg["ui"]
    try:
        ui["alpha"] = min(1.0, max(0.2, float(ui.get("alpha", 0.9))))
    except (TypeError, ValueError):
        ui["alpha"] = 0.9
    try:
        ui["font_scale"] = min(3.0, max(0.5, float(ui.get("font_scale", 1.0))))
    except (TypeError, ValueError):
        ui["font_scale"] = 1.0
    for key in ("columns", "rows", "page_rotate_seconds", "summary_lines", "angle_lines", "monitor"):
        try:
            ui[key] = int(ui.get(key, DEFAULT_CONFIG["ui"][key]))
        except (TypeError, ValueError):
            ui[key] = DEFAULT_CONFIG["ui"][key]
    ui["columns"] = min(4, max(1, ui["columns"]))
    ui["rows"] = min(4, max(1, ui["rows"]))


def load_config(path: str | Path | None = None) -> dict:
    """读取配置。文件不存在就用默认值（并把它写出来，方便用户改）。"""
    cfg_path = Path(path) if path else (PROJECT_DIR / "config.json")
    user_cfg: dict = {}
    if cfg_path.exists():
        try:
            user_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"配置文件解析失败：{cfg_path}\n{exc}") from exc
    cfg = _deep_merge(DEFAULT_CONFIG, user_cfg)
    _coerce_types(cfg)
    write_example = not cfg_path.exists()
    if write_example:
        try:
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return cfg


def resolve_api_key(section: dict) -> str:
    """API key 取「配置项 > 环境变量」。

    环境变量优先级更低是刻意的：一体机上多半没人配环境变量，配置文件才是唯一入口。
    """
    key = (section.get("api_key") or "").strip()
    if key:
        return key
    env_name = (section.get("api_key_env") or "").strip()
    if env_name:
        return (os.environ.get(env_name) or "").strip()
    return ""


def resolve_path(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (PROJECT_DIR / p)


def data_dir(cfg: dict) -> Path:
    return resolve_path(cfg["app"].get("data_dir") or "data")


def log_dir(cfg: dict) -> Path:
    return resolve_path(cfg["app"].get("log_dir") or "logs")


def active_sources(cfg: dict) -> list[dict]:
    return [s for s in cfg.get("sources", []) if s.get("enabled", True) and s.get("url")]


def category_map(cfg: dict) -> dict[str, dict]:
    return {c["key"]: c for c in cfg.get("categories", [])}
