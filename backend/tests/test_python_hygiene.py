"""Python 源码卫生守卫：无效转义、文件头 BOM。

这两类都不影响运行，但都是「编译期沉默、迟早咬人」的：

- **无效转义（W605）**：3.12 起从 DeprecationWarning 升为 SyntaxWarning，将来是错误。
  它往往还是「引号把字符串截断」的副作用——`r'''...""''...'''` 里那两个单引号会提前
  收尾，后半段悄悄掉进非 raw 串，语义靠巧合才对得上。修的时候要么加 `r` 前缀，
  要么把整个字面量换成三引号（内容里出现连续引号时，单/双引号包裹必然被截断）。
- **UTF-8 BOM**：Python 容忍，但拼进构建脚本、片段拼接、前端读取时，
  文件开头会多出一个看不见的字符，排查成本极高。

用 `compile()` 而不是正则：只有真正的字符串字面量才算，注释与正则里提到反斜杠不算。
"""

import warnings
from pathlib import Path

#: 后端源码根目录（本文件在 backend/tests/ 下）
APP_DIR = Path(__file__).resolve().parents[1] / "app"

#: UTF-8 BOM 的字节序列
BOM = "\ufeff".encode("utf-8")

#: 例外：(相对路径, 为什么它不该被修)。值是理由，不是「以后再说」。
EXEMPT: dict[str, str] = {}


def _sources():
    """遍历 app/ 下未被豁免的 .py 文件，产出 (绝对路径, 相对路径)。"""
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(APP_DIR).as_posix()
        if rel in EXEMPT:
            continue
        yield path, rel


def test_no_invalid_escape_sequences():
    """任何字符串字面量里都不允许出现无效转义序列。"""
    bad: list[str] = []
    for path, rel in _sources():
        # utf-8-sig：BOM 由下一条测试负责，这里只关心字面量
        text = path.read_text(encoding="utf-8-sig")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                compile(text, rel, "exec")
            except SyntaxError as e:
                bad.append(f"  {rel}:{e.lineno}  语法错误：{e.msg}")
                continue
        for w in caught:
            if "invalid escape" in str(w.message):
                bad.append(f"  {rel}:{w.lineno}  {w.message}")
    assert not bad, (
        "存在无效转义序列（加 r 前缀；若是引号把字面量截断了，改用三引号）：\n"
        + "\n".join(bad)
    )


def test_no_utf8_bom():
    """源码文件一律 UTF-8 无 BOM。"""
    bad = [f"  {rel}" for path, rel in _sources() if path.read_bytes().startswith(BOM)]
    assert not bad, "以下文件带 UTF-8 BOM（用 UTF-8 无 BOM 重存）：\n" + "\n".join(bad)
