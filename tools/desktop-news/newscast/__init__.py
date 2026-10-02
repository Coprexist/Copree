"""桌面新闻系统：Windows 桌面底层常驻的大字号新闻速览。

分三层组织，层与层之间只通过数据结构通信，便于分开测试：

- 采集层 netclient / sources / searchapi：把外部新闻源变成 RawItem；
- 处理层 pipeline / ai / classify：按「上次运行到这次运行」的时间窗口筛选、去重聚类、
  概括成适合课堂阅读与作文素材的 NewsCard；
- 表现层 render / win32：把结果画到桌面最底层，抠色透明背景、大字号、超量自动轮播。

只依赖 Python 标准库。目标机不需要联网安装任何包，也不需要 tkinter 之外的运行时
（tkinter 随官方 Windows 安装包提供）。
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
