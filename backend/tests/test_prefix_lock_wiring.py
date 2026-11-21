"""前缀锁的两半必须同时接在每条构造路径上（防止「只 import 一半」再次发生）。

背景：私信那一支曾经只 import 了 locked、没 import guard，函数末尾的 guard 调用变成
NameError；response_worker 把异常吞成一行日志，表现是「所有私信都不回」。
这类漏导入静态检查抓不到（函数体里是合法名字），所以直接检查两个 builder 的代码对象
都引用了这两个全局名——将来谁再拆一段出来忘了接，这条会红。
"""

from app.ai.llm import build_dm_messages, build_messages


def test_both_builders_wire_prefix_lock():
    for fn in (build_messages, build_dm_messages):
        names = fn.__code__.co_names
        assert "prefix_locked" in names, f"{fn.__name__} 没有接前缀锁（locked）"
        assert "prefix_guard" in names, f"{fn.__name__} 没有接前缀校验（guard）"
