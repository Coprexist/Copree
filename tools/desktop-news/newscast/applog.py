"""日志：控制台 + 文件双输出，文件按大小自截断。

一体机上没有控制台（pythonw 启动），出问题只能靠日志文件回溯，所以日志必须落盘；
又因为程序长期常驻、每天多次运行，日志不能无限增长，超过上限就保留尾部重写。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

_LOGGER_NAME = "newscast"
_configured = False
_lock = threading.Lock()


class _TrimmingFileHandler(logging.Handler):
    """写入前检查体积，超限则保留尾部——比 RotatingFileHandler 更省心。

    常驻进程里文件句柄长期不关，用 RotatingFileHandler 会留下多个滚动文件；
    这里只保留一个文件，出问题直接把文件发给维护者即可。
    """

    def __init__(self, path: Path, max_bytes: int = 1_000_000, keep_bytes: int = 300_000) -> None:
        super().__init__()
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.keep_bytes = keep_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = None

    def _open(self):
        if self._file is None:
            self._file = open(self.path, "a", encoding="utf-8", errors="replace")
        return self._file

    def _trim_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                if self._file is not None:
                    self._file.close()
                    self._file = None
                with open(self.path, "rb") as src:
                    src.seek(max(0, self.path.stat().st_size - self.keep_bytes))
                    tail = src.read()
                # 从头截断到第一个换行，避免留下半行
                cut = tail.find(b"\n")
                if cut > 0:
                    tail = tail[cut + 1:]
                with open(self.path, "wb") as dst:
                    dst.write("===== 日志超出上限，仅保留最近部分 =====\n".encode("utf-8"))
                    dst.write(tail)
        except OSError:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._trim_if_needed()
            f = self._open()
            f.write(self.format(record) + "\n")
            f.flush()
        except Exception:  # 日志失败绝不能打断主流程
            pass

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None
        super().close()


def setup_logging(log_dir: Path, level: str = "INFO", to_console: bool = True) -> logging.Logger:
    """初始化全局日志。重复调用是幂等的，避免多入口重复挂 handler。"""
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    with _lock:
        if _configured:
            return logger
        logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
        logger.propagate = False
        fmt = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
                               datefmt="%Y-%m-%d %H:%M:%S")
        fh = _TrimmingFileHandler(Path(log_dir) / "newscast.log")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        if to_console and sys.stderr is not None:
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(fmt)
            logger.addHandler(sh)
        _configured = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def install_excepthook(logger: logging.Logger) -> None:
    """把未捕获异常写进日志：pythonw 下崩溃时界面上什么都看不到，只能靠这个。"""

    def _hook(exc_type, exc_value, exc_tb):
        logger.error("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _hook
    if hasattr(threading, "excepthook"):
        def _thread_hook(args):
            logger.error("线程未捕获异常：%s", args.thread.name if args.thread else "?",
                         exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

        threading.excepthook = _thread_hook


def real_stdout_fallback() -> None:
    """PyInstaller --noconsole 下 sys.stdout 可能为 None，写入会抛异常，这里补个黑洞。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
