#!/usr/bin/env python3
"""桌面新闻系统入口。

用法（Windows）：
    pythonw news_desktop.py            常驻桌面底层，开机自启用的就是这个
    python  news_desktop.py --debug    带标题栏的调试窗口，方便调排版
    python  news_desktop.py --demo     用样例数据预览排版，不联网
    python  news_desktop.py --once     只抓取一次就退出（可配计划任务）
    python  news_desktop.py --check    体检：网络、新闻源、AI 接口、显示器
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from newscast import __version__, applog, config, pipeline, sources  # noqa: E402
from newscast.store import Store  # noqa: E402


class SingleInstance:
    """单实例锁：开机自启 + 手动双击很容易起两个进程，两个透明窗口叠在一起会互相打架。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = open(self.path, "a+")
            if sys.platform.startswith("win"):
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, ImportError):
            return False

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if sys.platform.startswith("win"):
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                self.handle.close()
            finally:
                self.handle = None


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="桌面新闻系统：透明置底的大字号每日新闻速览")
    parser.add_argument("--config", help="配置文件路径，默认为程序目录下的 config.json")
    parser.add_argument("--once", action="store_true", help="只抓取一次并退出（不显示界面）")
    parser.add_argument("--debug", action="store_true", help="调试窗口模式：有标题栏、不置底、保持半透明")
    parser.add_argument("--demo", action="store_true", help="用样例数据预览排版，不联网")
    parser.add_argument("--check", action="store_true", help="体检：新闻源、AI 接口、显示器、目录权限")
    parser.add_argument("--no-ai", action="store_true", help="本次不使用 AI，只做规则整理")
    parser.add_argument("--reset-window", action="store_true",
                        help="把上次运行时间重置为「现在-默认窗口」，用于补抓一段时间")
    parser.add_argument("--version", action="version", version=f"桌面新闻系统 {__version__}")
    return parser.parse_args(argv)


def build_demo_result(cfg: dict):
    """造一份样例结果，用来在真实抓取之前先把排版、字号、透明度调好。"""
    from newscast.models import CategoryBlock, NewsCard, RunResult, SourceStatus
    from newscast import textutil

    samples = {
        "top": ("我国成功发射试验二十八号卫星", "卫星由长征系列运载火箭送入预定轨道，将开展空间环境探测与技术试验，为后续深空任务积累数据。"),
        "politics": ("教育部部署新学期中小学作业管理", "要求学校严格控制作业总量、提高课堂效率，把睡眠时间还给学生，同时加强对校外培训的日常监管。"),
        "tech": ("开源大模型推理成本一年下降九成", "多家机构发布新一代开源模型，同等效果下的推理开销显著降低，中小团队也能自己部署，技术门槛正在被拉平。"),
        "novelty": ("野生大熊猫现身村民院坝", "村民用手机拍下视频，专家判断该个体健康状况良好，随后自行返回山林，当地已加强周边巡护。"),
    }
    angles = {
        "top": "长期主义：大工程靠一代人持续投入",
        "politics": "民生温度：政策的落点在普通人的日常里",
        "tech": "科技自立：核心技术买不来，只能自己长出来",
        "novelty": "敬畏自然：与万物共处的方式",
    }
    now = textutil.now_utc()
    blocks = []
    for cat in cfg.get("categories", []):
        cards = []
        title, summary = samples.get(cat["key"], ("样例新闻", "这是一条用于预览排版的样例新闻。"))
        for index in range(max(2, min(int(cat.get("max", 4)), 3))):
            suffix = "" if index == 0 else f"（样例 {index + 1}）"
            cards.append(NewsCard(
                title=title + suffix,
                summary=summary,
                angle=angles.get(cat["key"], "换个角度看世界：常识之外还有天地"),
                category=cat["key"],
                sources=["IT之家", "人民网"] if index == 0 else ["中国新闻网"],
                urls=["https://example.com/"],
                published=now,
                item_count=3 if index == 0 else 1,
                score=3.0 - index,
                from_ai=True,
            ))
        blocks.append(CategoryBlock(key=cat["key"], label=cat["label"],
                                    accent=cat.get("accent", "#8fa3b8"), cards=cards))
    return RunResult(generated_at=now, window_start=now.replace(hour=0, minute=0),
                     window_end=now, blocks=blocks,
                     source_status=[SourceStatus("样例数据", True, 12)],
                     fetched=12, kept=12, ai_used=True, ai_cards=12,
                     duration=0.4, note="当前为样例数据，点「刷新」或按 Ctrl+Alt+R 抓取真实新闻")


def run_check(cfg: dict, store: Store, logger) -> int:
    """部署前体检。一体机在教室里，出问题不好排查，能在办公室先验完最省事。"""
    import platform
    print(f"桌面新闻系统 {__version__}")
    print(f"Python {platform.python_version()}　{platform.system()} {platform.release()}")
    problems = 0

    try:
        import tkinter  # noqa: F401
        print("[OK]   tkinter 可用")
    except ImportError:
        print("[FAIL] 缺少 tkinter：请使用 python.org 的官方安装包，安装时勾选 tcl/tk")
        problems += 1

    for name, path in (("数据目录", config.data_dir(cfg)), ("日志目录", config.log_dir(cfg))):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print(f"[OK]   {name}可写：{path}")
        except OSError as exc:
            print(f"[FAIL] {name}不可写：{path}（{exc}）")
            problems += 1

    from newscast import win32
    if win32.IS_WINDOWS:
        mons = win32.monitors()
        print(f"[OK]   检测到 {len(mons)} 个显示器：" +
              "、".join(f"{m['width']}x{m['height']}{'(主)' if m['primary'] else ''}" for m in mons))
        ui = cfg["ui"]
        print(f"       置底模式 {ui.get('desktop_mode')}　"
              f"主屏选择 第{int(ui.get('monitor', 0)) + 1} 台　字号缩放 {ui.get('font_scale')}")
    else:
        print("[WARN] 当前不是 Windows：透明置底不可用，只能用 --debug 预览排版")

    print("\n新闻源连通性（每源最多 8 秒）……")
    cfg_probe = cfg
    cfg_probe["app"]["http_timeout"] = 8
    cfg_probe["app"]["http_retries"] = 0
    items, statuses = sources.fetch_all(cfg_probe, logger)
    for st in statuses:
        flag = "[OK]  " if st.ok else "[FAIL]"
        print(f"  {flag} {st.name:<14} {st.items:>4} 条  {st.error[:60]}")
    ok = sum(1 for s in statuses if s.ok)
    print(f"  合计 {ok}/{len(statuses)} 个源可用，抓到 {len(items)} 条")
    if ok == 0:
        print("  [FAIL] 所有源都失败：检查网络/代理，或把 app.allow_insecure_fallback 设为 true 试试")
        problems += 1

    from newscast.ai import AIClient
    client = AIClient(cfg, logger)
    if client.available:
        print(f"\nAI 接口自检：{cfg['ai'].get('base_url')} / {cfg['ai'].get('model')}")
        try:
            client._chat([{"role": "user", "content": "回复两个字：可用"}], max_tokens=16)
            print("[OK]   AI 接口可用")
        except Exception as exc:
            print(f"[FAIL] AI 接口调用失败：{exc}")
            problems += 1
    else:
        print(f"\n[WARN] AI 未启用：{client.unavailable_reason()}")
        print("       没有 AI 也能用（走规则分类 + 原文摘要），配置好 key 后质量提升明显")

    print(f"\n体检结束：{problems} 个需要处理的问题")
    return 0 if problems == 0 else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    applog.real_stdout_fallback()
    cfg = config.load_config(args.config)
    logger = applog.setup_logging(config.log_dir(cfg), cfg["app"].get("log_level", "INFO"))
    applog.install_excepthook(logger)
    store = Store(config.data_dir(cfg))

    logger.info("启动：模式 once=%s debug=%s demo=%s check=%s", args.once, args.debug, args.demo, args.check)

    if args.check:
        return run_check(cfg, store, logger)

    if args.reset_window:
        state = store.load_state()
        state["last_run"] = ""
        store.save_state(state)
        print("已重置时间窗口，下次抓取会回溯 "
              f"{int(cfg['app'].get('window_hours_default', 24))} 小时")
        if not args.once:
            return 0

    if args.once:
        result = pipeline.run_once(cfg, store, logger, use_ai=not args.no_ai,
                                   progress=lambda m: print(f"  {m}", flush=True))
        print(f"\n抓取 {result.fetched} 条，入窗 {result.kept} 条，生成 {result.card_count} 张卡片，"
              f"用时 {result.duration:.1f}s")
        if result.ai_error and not result.ai_used:
            print(f"AI 未生效：{result.ai_error}")
        if result.note:
            print(f"提示：{result.note}")
        for block in result.blocks:
            print(f"\n【{block.label}】")
            for card in block.cards:
                print(f"  · {card.title}")
                print(f"    {card.summary[:80]}")
                if card.angle:
                    print(f"    作文角度：{card.angle}")
        for st in result.source_status:
            if not st.ok:
                logger.debug("失败源 %s：%s", st.name, st.error)
        return 0 if result.card_count else 1

    # 界面模式
    try:
        from newscast import render
    except ImportError as exc:
        print(f"无法加载界面模块（缺少 tkinter？）：{exc}")
        print("请安装 python.org 官方 Python 并勾选 tcl/tk，或改用 --once 模式。")
        return 2

    if args.demo:
        demo = build_demo_result(cfg)
        store.save_latest(demo)
        logger.info("已写入样例数据用于预览排版")

    guard = None
    if not args.debug:
        guard = SingleInstance(config.data_dir(cfg) / "app.lock")
        if not guard.acquire():
            print("已有实例在运行，本次退出。")
            return 0
    try:
        return render.start_gui(cfg, store, logger, debug=args.debug)
    except Exception:
        logger.exception("界面异常退出")
        return 1
    finally:
        if guard:
            guard.release()


if __name__ == "__main__":
    sys.exit(main())
