"""桌面渲染：透明背景、大字排版、2x2 分类网格、超量自动轮播。

为什么用 Canvas 而不是 Frame/Label 堆叠：抠色透明要求整窗只有一个背景色，
Frame 各自带边框和背景，叠起来会出现色块边缘；Canvas 上所有元素都能精确摆放和清空，
翻页只要 delete("all") 重画，也方便按字体实际宽度做中文换行。
"""

from __future__ import annotations

import datetime as _dt
import logging
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont

from . import textutil, win32
from .models import RunResult
from .pipeline import run_once

# 基准字号按 1920x1080 定，实际会按屏宽与 ui.font_scale 缩放
_BASE_FONTS = {
    "header": (30, "bold"),
    "header_sub": (15, "normal"),
    "category": (22, "bold"),
    "card_top": (26, "bold"),
    "card_title": (23, "bold"),
    "summary": (16, "normal"),
    "angle": (15, "normal"),
    "source": (12, "normal"),
    "status": (12, "normal"),
}


def pick_family(candidates: list[str]) -> str:
    """从候选字体里挑第一个系统装了的，全都没有就用 Tk 默认。"""
    try:
        available = set(tkfont.families())
    except tk.TclError:
        return candidates[0] if candidates else "TkDefaultFont"
    for name in candidates:
        if name in available:
            return name
    return candidates[-1] if candidates else "TkDefaultFont"


def wrap_text(text: str, font: tkfont.Font, width: int, max_lines: int) -> list[str]:
    """按像素宽度做中文友好的贪心折行，超出 max_lines 时末行加省略号。

    Canvas 的自动折行无法控制行数，多出来的字会溢出面板压到下一张卡片上，所以自己算。
    """
    if not text or width <= 0 or max_lines <= 0:
        return []
    lines: list[str] = []
    current = ""
    truncated = False
    for ch in text:
        if ch == "\n":
            lines.append(current)
            current = ""
            if len(lines) >= max_lines:
                truncated = True
                break
            continue
        candidate = current + ch
        if font.measure(candidate) > width and current:
            lines.append(current)
            current = ch
            if len(lines) >= max_lines:
                truncated = True
                break
        else:
            current = candidate
    if not truncated and current:
        lines.append(current)
    elif truncated and lines:
        last = lines[-1]
        while last and font.measure(last + "…") > width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines[:max_lines]


def fit_one_line(text: str, font: tkfont.Font, width: int) -> str:
    if not text:
        return ""
    if font.measure(text) <= width:
        return text
    out = text
    while out and font.measure(out + "…") > width:
        out = out[:-1]
    return out + "…"


class DesktopNewsApp:
    def __init__(self, cfg: dict, store, logger: logging.Logger, debug: bool = False) -> None:
        self.cfg = cfg
        self.store = store
        self.logger = logger
        self.debug = debug
        self.ui = cfg["ui"]
        self.app = cfg["app"]
        self.colors = self.ui.get("colors", {})
        self.key_color = self.ui.get("transparent_color", "#010203")
        self.mode = self._resolve_mode()
        self.click_through = bool(self.ui.get("click_through", False))
        self.visible = True
        self.busy = False
        self.result: RunResult | None = None
        self.pages: list[list[tuple[object, list]]] = []
        self.page_index = 0
        self.page_count = 1
        self.queue: queue.Queue = queue.Queue()
        self._error_text = ""

        self.root = tk.Tk()
        self.root.title(self.app.get("title", "每日新闻速览"))
        self._setup_window()
        self.canvas = tk.Canvas(self.root, bg=self.key_color, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._init_fonts()
        self._bind_geometry()
        self.result = self.store.load_latest()
        self._render()
        self.root.after(120, self._post_setup)
        self.root.after(200, self._tick_hotkeys)
        self.root.after(300, self._poll_queue)
        self.root.after(3000, self._keep_bottom)
        self.root.after(1000, self._maybe_startup_refresh)

    # ------------------------------------------------------------ 窗口与桌面层

    def _resolve_mode(self) -> str:
        mode = str(self.ui.get("desktop_mode", "auto")).lower()
        if mode == "auto":
            return "bottom" if win32.IS_WINDOWS else "normal"
        if mode in ("workerw", "bottom") and not win32.IS_WINDOWS:
            return "normal"
        return mode

    def _screen_rect(self) -> tuple[int, int, int, int]:
        """返回目标显示器的 (x, y, width, height)。多屏时按 ui.monitor 选择。"""
        index = int(self.ui.get("monitor", 0) or 0)
        mons = win32.monitors()
        if mons and 0 <= index < len(mons):
            m = mons[index]
            return m["x"], m["y"], m["width"], m["height"]
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _setup_window(self) -> None:
        if self.debug or self.mode == "normal":
            self.root.geometry("1360x820+40+40")
            return
        self.root.overrideredirect(True)          # 无边框无标题栏
        self.root.attributes("-topmost", False)
        x, y, w, h = self._screen_rect()
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        try:
            self.root.attributes("-transparentcolor", self.key_color)
        except tk.TclError as exc:
            self.logger.warning("系统不支持抠色透明：%s", exc)
        try:
            self.root.attributes("-alpha", float(self.ui.get("alpha", 0.9)))
        except tk.TclError as exc:
            self.logger.warning("系统不支持整窗透明：%s", exc)

    def _post_setup(self) -> None:
        """窗口映射之后再动 Win32 句柄：Tk 只有在 map 之后才有真实 HWND。"""
        if self.debug or self.mode == "normal":
            return
        self.root.update_idletasks()
        self.hwnd = win32.hwnd_of(self.root)
        if not self.hwnd:
            self.logger.warning("拿不到窗口句柄，跳过桌面层处理")
            return
        x, y, w, h = self._screen_rect()
        if self.mode == "workerw" and win32.attach_to_desktop(self.hwnd):
            win32.place_window(self.hwnd, x, y, w, h)
        else:
            # 挂壁纸层失败就退回置底：功能不打折，只是「显示桌面」时会一起被藏起来
            win32.place_window(self.hwnd, x, y, w, h)
            win32.push_to_bottom(self.hwnd)
        # tkinter 对 -alpha/-transparentcolor 是两次独立调用，会互相覆盖标志位，
        # 这里合并重设一次，保证「背景全透明 + 面板半透明」同时生效
        win32.apply_layered(self.hwnd, float(self.ui.get("alpha", 0.9)),
                            self.key_color if self.mode != "normal" else None)
        win32.set_click_through(self.hwnd, self.click_through)

    def _keep_bottom(self) -> None:
        """周期性重新压底：其他程序（尤其是一体机的教学软件）会把自己插到更底层。"""
        if not self.debug and self.mode == "bottom" and getattr(self, "hwnd", 0) and self.visible:
            win32.push_to_bottom(self.hwnd)
        self._after(3000, self._keep_bottom)

    def _toggle_click_through(self) -> None:
        self.click_through = not self.click_through
        if getattr(self, "hwnd", 0):
            win32.set_click_through(self.hwnd, self.click_through)
        self._render()

    def _toggle_visible(self) -> None:
        self.visible = not self.visible
        if self.visible:
            self.root.deiconify()
            if getattr(self, "hwnd", 0):
                self.root.attributes("-topmost", False)
                win32.push_to_bottom(self.hwnd)
        else:
            self.root.withdraw()

    def _bind_geometry(self) -> None:
        self.root.bind("<Configure>", self._on_resize)

    def _on_resize(self, _event=None) -> None:
        # 防抖：拖动/分辨率变化时会连发多次 Configure
        if getattr(self, "_resize_job", None):
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(400, self._render)

    def _init_fonts(self) -> None:
        families = self.ui.get("font_family") or ["微软雅黑"]
        if isinstance(families, str):
            families = [families]
        family = pick_family(list(families))
        _, _, screen_w, _ = self._screen_rect()
        # 一体机分辨率差异大：以 1920 宽为基准等比缩放，再乘用户配置的系数
        auto = max(0.75, min(2.2, (screen_w or 1920) / 1920.0))
        scale = auto * float(self.ui.get("font_scale", 1.0))
        self.fonts = {
            name: tkfont.Font(family=family, size=max(9, int(round(size * scale))),
                              weight=weight)
            for name, (size, weight) in _BASE_FONTS.items()
        }
        self.scale = scale

    # ------------------------------------------------------------ 数据刷新

    def _maybe_startup_refresh(self) -> None:
        """开机时如果数据太旧就先抓一次，保证早上第一节课看到的是今天的新闻。"""
        stale_hours = float(self.app.get("startup_stale_hours", 8))
        last = self.store.last_run()
        now = textutil.now_utc()
        need = self.result is None or last is None or (now - last) > _dt.timedelta(hours=stale_hours)
        self._schedule_daily()
        if need:
            self.logger.info("数据超过 %.1f 小时未更新，启动时立即抓取", stale_hours)
            self.start_refresh()
        else:
            self._render()

    def _schedule_daily(self) -> None:
        """排在每天配置的时刻刷新一次。"""
        now = _dt.datetime.now()
        target = now.replace(hour=int(self.app.get("refresh_hour", 6)),
                             minute=int(self.app.get("refresh_minute", 45)),
                             second=0, microsecond=0)
        if target <= now:
            target += _dt.timedelta(days=1)
        delay_ms = int((target - now).total_seconds() * 1000)
        self._after(delay_ms, self._daily_tick)
        self.next_refresh = target
        self.logger.info("下次自动刷新：%s", target.strftime("%Y-%m-%d %H:%M"))

    def _daily_tick(self) -> None:
        self.start_refresh()
        self._schedule_daily()

    def start_refresh(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._error_text = ""
        self._render()
        threading.Thread(target=self._refresh_worker, name="refresh", daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            result = run_once(self.cfg, self.store, self.logger,
                              progress=lambda msg: self.queue.put(("progress", msg)))
            self.queue.put(("done", result))
        except Exception as exc:  # 后台线程异常必须带回主线程显示，否则界面永远停在「刷新中」
            self.logger.exception("刷新失败")
            self.queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "progress":
                    self._error_text = payload
                    self._render()
                elif kind == "done":
                    self.result = payload
                    self.busy = False
                    self.page_index = 0
                    self._error_text = ""
                    self._render()
                elif kind == "error":
                    self.busy = False
                    self._error_text = f"刷新失败：{payload}"
                    self._render()
        except queue.Empty:
            pass
        self._after(300, self._poll_queue)

    # ------------------------------------------------------------ 热键

    def _tick_hotkeys(self) -> None:
        bindings = self.ui.get("hotkeys") or {}
        if bindings.get("enabled", True):
            if not hasattr(self, "hotkeys"):
                self.hotkeys = win32.HotkeyWatcher({k: v for k, v in bindings.items() if k != "enabled"})
            actions = {
                "refresh": self.start_refresh,
                "toggle_visible": self._toggle_visible,
                "click_through": self._toggle_click_through,
                "quit": self.root.destroy,
            }
            for action in self.hotkeys.poll():
                handler = actions.get(action)
                if handler:
                    self.logger.info("热键触发：%s", action)
                    handler()
        self._after(200, self._tick_hotkeys)

    def _after(self, ms: int, func) -> None:
        """统一走 root.after；窗口销毁后回调不会再执行，无需逐个取消。"""
        try:
            self.root.after(ms, func)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 绘制

    def _render(self) -> None:
        try:
            self.canvas.delete("all")
            width = self.canvas.winfo_width() or self.root.winfo_width() or 1920
            height = self.canvas.winfo_height() or self.root.winfo_height() or 1080
            if width < 100 or height < 100:
                # 首帧尺寸还没确定，等下一次 Configure
                self._after(200, self._render)
                return
            margin = self.ui.get("margin", {})
            left = int(margin.get("left", 34))
            top = int(margin.get("top", 26))
            right = int(margin.get("right", 34))
            bottom = int(margin.get("bottom", 22))
            header_h = int(self.fonts["header"].metrics("linespace") * 2.2)
            status_h = int(self.fonts["status"].metrics("linespace") * 2.4)
            grid_top = top + header_h
            grid_h = height - grid_top - status_h - bottom
            grid_w = width - left - right
            self._draw_header(left, top, grid_w, header_h)
            self._draw_grid(left, grid_top, grid_w, grid_h)
            self._draw_status(left, height - status_h - bottom + 6, grid_w)
        except tk.TclError:
            pass
        except Exception as exc:
            self.logger.exception("渲染失败：%s", exc)

    def _draw_header(self, x: int, y: int, width: int, height: int) -> None:
        title = self.app.get("title", "每日新闻速览")
        today = _dt.datetime.now().strftime("%Y年%m月%d日")
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        subtitle = self.app.get("subtitle", "")
        right_text = f"{today} {weekdays[_dt.datetime.now().weekday()]}"
        if self.result and self.result.generated_at:
            right_text += f"　数据时间 {textutil.format_local(self.result.generated_at)}"

        self.canvas.create_text(x + 6, y + height * 0.34, anchor="w", text=title,
                                font=self.fonts["header"], fill=self.colors.get("header", "#e8eef6"))
        tw = self.fonts["header"].measure(title)
        if subtitle:
            self.canvas.create_text(x + 6 + tw + 28, y + height * 0.42, anchor="w", text=subtitle,
                                    font=self.fonts["header_sub"],
                                    fill=self.colors.get("muted", "#6b7a8c"))
        self.canvas.create_text(x + width - 6, y + height * 0.38, anchor="e", text=right_text,
                                font=self.fonts["header_sub"],
                                fill=self.colors.get("angle", "#8fa3b8"))
        line_y = y + height - 6
        self.canvas.create_line(x + 4, line_y, x + width - 4, line_y,
                                fill=self.colors.get("panel_edge", "#1f2a37"))

    def _draw_status(self, x: int, y: int, width: int) -> None:
        parts: list[str] = []
        if self.busy:
            parts.append(self._error_text or "正在刷新")
        elif self._error_text:
            parts.append(self._error_text)
        if self.result:
            r = self.result
            if r.window_start and r.window_end:
                parts.append(f"时间范围 {textutil.format_span(r.window_start, r.window_end)}")
            parts.append(f"源 {r.sources_ok}/{r.sources_ok + r.sources_failed}"
                         f"　抓取 {r.fetched}　入窗 {r.kept}　卡片 {r.card_count}")
            parts.append("AI 概括已启用" if r.ai_used else
                         f"AI 未启用（{r.ai_error[:28]}）" if r.ai_error else "规则整理")
            if r.note:
                parts.append(r.note)
        else:
            parts.append("尚无数据")
        if getattr(self, "next_refresh", None):
            parts.append("下次刷新 " + self.next_refresh.strftime("%m-%d %H:%M"))
        if self.page_count > 1:
            parts.append(f"第 {self.page_index + 1}/{self.page_count} 页")
        hotkeys = self.ui.get("hotkeys") or {}
        if hotkeys.get("enabled", True):
            parts.append("Ctrl+Alt+R 刷新　Ctrl+Alt+N 显示/隐藏　Ctrl+Alt+T 鼠标穿透"
                         "　Ctrl+Alt+Q 退出")
        text = "　·　".join(p for p in parts if p)
        self.canvas.create_text(x + 6, y, anchor="nw", text=fit_one_line(text, self.fonts["status"], width - 12),
                                font=self.fonts["status"],
                                fill=self.colors.get("muted", "#6b7a8c"))

    def _draw_grid(self, x: int, y: int, width: int, height: int) -> None:
        columns = max(1, int(self.ui.get("columns", 2)))
        rows = max(1, int(self.ui.get("rows", 2)))
        gap = max(8, int(10 * self.scale))
        cell_w = (width - gap * (columns - 1)) // columns
        cell_h = (height - gap * (rows - 1)) // rows

        blocks = self.result.blocks if self.result else []
        self._build_pages(cell_w, cell_h)
        # 页面结构：[{block, cards}] 列表；轮播时取第 page_index 页
        cells = self.pages[self.page_index] if self.pages else []
        for index in range(columns * rows):
            cx = x + (index % columns) * (cell_w + gap)
            cy = y + (index // columns) * (cell_h + gap)
            if index < len(cells):
                block, cards = cells[index]
                self._draw_panel(cx, cy, cell_w, cell_h, block, cards)
            else:
                self._draw_empty_panel(cx, cy, cell_w, cell_h)

    def _build_pages(self, cell_w: int, cell_h: int) -> None:
        """按格子能放下的卡片数把各类新闻切页；页数取各分类的最大值。

        切页结果做缓存：只有格子尺寸或卡片数量变化时才重算，避免每次重绘都做一遍除法。
        """
        blocks = self.result.blocks if self.result else []
        header_h = int(self.fonts["category"].metrics("linespace") * 2.0)
        card_h = self._card_height(cell_w)
        capacity = max(1, (cell_h - header_h - int(8 * self.scale)) // max(1, card_h))
        key = (capacity, tuple((b.key, len(b.cards)) for b in blocks))
        if getattr(self, "_pages_key", None) == key:
            return
        self._pages_key = key

        max_pages = 1
        for block in blocks:
            max_pages = max(max_pages, (len(block.cards) + capacity - 1) // capacity or 1)
        pages = []
        for page in range(max_pages):
            row = []
            for block in blocks:
                chunk = block.cards[page * capacity:(page + 1) * capacity]
                row.append((block, chunk))
            pages.append(row)
        self.pages = pages
        self.page_count = len(pages)
        if self.page_index >= self.page_count:
            self.page_index = 0
        if self.page_count > 1 and not getattr(self, "_rotate_started", False):
            self._rotate_started = True
            self._schedule_rotate()

    def _schedule_rotate(self) -> None:
        seconds = int(self.ui.get("page_rotate_seconds", 25))
        if seconds <= 0:
            return
        def _next():
            if self.page_count > 1 and self.visible:
                self.page_index = (self.page_index + 1) % self.page_count
                self._render()
            self._after(max(3000, seconds * 1000), _next)
        self._after(max(3000, seconds * 1000), _next)

    def _card_height(self, cell_w: int) -> int:
        line = self.fonts["summary"].metrics("linespace")
        title_line = self.fonts["card_title"].metrics("linespace")
        angle_line = self.fonts["angle"].metrics("linespace")
        summary_lines = int(self.ui.get("summary_lines", 3))
        angle_lines = int(self.ui.get("angle_lines", 1)) if self.ui.get("show_angle", True) else 0
        pad = int(14 * self.scale)
        return (title_line + line * summary_lines + angle_line * angle_lines
                + int(self.fonts["source"].metrics("linespace") if self.ui.get("show_sources", True) else 0)
                + pad * 2 + int(10 * self.scale))

    def _draw_panel(self, x: int, y: int, width: int, height: int, block, cards: list) -> None:
        radius = int(14 * self.scale)
        self._rounded_rect(x, y, x + width, y + height, radius,
                           fill=self.colors.get("panel", "#0d1117"),
                           outline=self.colors.get("panel_edge", "#1f2a37"))
        accent = block.accent or "#8fa3b8"
        label_y = y + int(12 * self.scale)
        self.canvas.create_rectangle(x + int(14 * self.scale), label_y,
                                     x + int(20 * self.scale),
                                     label_y + self.fonts["category"].metrics("linespace"),
                                     fill=accent, outline="")
        self.canvas.create_text(x + int(30 * self.scale), label_y, anchor="nw", text=block.label,
                                font=self.fonts["category"], fill=accent)
        count_text = f"{len(cards)} 条" if cards else "暂无"
        self.canvas.create_text(x + width - int(14 * self.scale), label_y + int(6 * self.scale),
                                anchor="ne", text=count_text, font=self.fonts["source"],
                                fill=self.colors.get("muted", "#6b7a8c"))

        if not cards:
            self.canvas.create_text(x + width / 2, y + height / 2, text="本次没有新的相关新闻",
                                    font=self.fonts["summary"],
                                    fill=self.colors.get("muted", "#6b7a8c"))
            return

        top = y + int(self.fonts["category"].metrics("linespace") * 2.0)
        card_h = self._card_height(width)
        inner_w = width - int(28 * self.scale)
        for card in cards:
            if top + card_h > y + height:
                break
            self._draw_card(x + int(14 * self.scale), top, inner_w, card_h, card, accent, block.key)
            top += card_h

    def _draw_card(self, x: int, y: int, width: int, height: int, card, accent: str, category: str) -> None:
        line_gap = int(4 * self.scale)
        cursor = y + int(2 * self.scale)

        title_font = self.fonts["card_top"] if category == "top" else self.fonts["card_title"]
        title = fit_one_line(card.title, title_font, width - int(8 * self.scale))
        self.canvas.create_text(x, cursor, anchor="nw", text=title, font=title_font,
                                fill=self.colors.get("title", "#f2f6fc"))
        cursor += title_font.metrics("linespace") + line_gap

        summary_lines = int(self.ui.get("summary_lines", 3))
        for line in wrap_text(card.summary, self.fonts["summary"], width, summary_lines):
            self.canvas.create_text(x, cursor, anchor="nw", text=line, font=self.fonts["summary"],
                                    fill=self.colors.get("summary", "#c6d0dc"))
            cursor += self.fonts["summary"].metrics("linespace")

        if self.ui.get("show_angle", True) and card.angle:
            cursor += int(2 * self.scale)
            angle_lines = int(self.ui.get("angle_lines", 1))
            text = "作文角度：" + card.angle
            for line in wrap_text(text, self.fonts["angle"], width, angle_lines):
                self.canvas.create_text(x, cursor, anchor="nw", text=line, font=self.fonts["angle"],
                                        fill=self.colors.get("angle", "#8fa3b8"))
                cursor += self.fonts["angle"].metrics("linespace")

        if self.ui.get("show_sources", True):
            meta = "　".join(filter(None, [
                textutil.format_local(card.published, "%m-%d %H:%M") if card.published else "",
                "、".join(card.sources[:3]),
                f"{card.item_count} 条报道" if card.item_count > 1 else "",
            ]))
            self.canvas.create_text(x, y + height - self.fonts["source"].metrics("linespace"),
                                    anchor="nw", text=fit_one_line(meta, self.fonts["source"], width),
                                    font=self.fonts["source"],
                                    fill=self.colors.get("muted", "#6b7a8c"))

    def _draw_empty_panel(self, x: int, y: int, width: int, height: int) -> None:
        self._rounded_rect(x, y, x + width, y + height, int(14 * self.scale),
                           fill=self.colors.get("panel", "#0d1117"),
                           outline=self.colors.get("panel_edge", "#1f2a37"))

    def _rounded_rect(self, x1: int, y1: int, x2: int, y2: int, r: int, **kwargs):
        """Tk 没有圆角矩形，用平滑多边形模拟（视觉上等价，且只有一个图形对象）。"""
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)

    # ------------------------------------------------------------ 生命周期

    def run(self) -> None:
        self.root.mainloop()


def start_gui(cfg: dict, store, logger: logging.Logger, debug: bool = False) -> int:
    win32.set_dpi_awareness()
    app = DesktopNewsApp(cfg, store, logger, debug=debug)
    app.run()
    return 0
