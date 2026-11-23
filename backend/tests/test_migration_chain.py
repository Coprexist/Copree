"""迁移链的两条硬约束：唯一线性、编号接得上。

为什么值得一条测试：0069 起改用 4 位顺序号（0069 → 0126 连续），但更早的 68 个是
hash。约定只能靠人记，而漏一次就是"编号看着连续、其实有两处分支"这种要翻 126 个文件
才发现的问题。这里让机器把关，违反就红。

**不改老 id**：alembic_version.version_num 里存的就是 revision 字符串本身，
改名会让已升级的实例找不到自己的版本（Can't locate revision）。
"""
import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# 两种写法并存：revision = "0069" 与 revision: str = "0123"（引号单双都有）
_REV = re.compile(r"^revision(?:\s*:\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)
_DOWN = re.compile(
    r"^down_revision(?:\s*:\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.M
)

# 从这一号起改成顺序编号；更早的是 hash，不能动也不会再变
SEQUENTIAL_FROM = 69


def _load():
    """解析所有迁移文件，返回 {revision: (down_revision, 文件名)}。"""
    entries: dict[str, tuple[str | None, str]] = {}
    for path in VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        rev = _REV.search(text)
        if rev is None:
            continue    # 非迁移文件（如内联数据快照的辅助模块）
        down = _DOWN.search(text)
        entries[rev.group(1)] = (down.group(1) if down else None, path.name)
    assert entries, "一个迁移都没解析到，路径错了"
    return entries


def _chain(entries):
    """从唯一 head 往回落，返回按时间顺序（旧 → 新）排列的 [(revision, 文件名)]。"""
    parents = {down for down, _ in entries.values() if down}
    heads = [rev for rev in entries if rev not in parents]
    assert len(heads) == 1, f"迁移链必须只有一个 head，实际有 {heads}"
    chain = []
    cursor = heads[0]
    while cursor:
        down, name = entries[cursor]
        chain.append((cursor, name))
        cursor = down
    chain.reverse()
    return chain


def test_there_is_exactly_one_linear_chain():
    entries = _load()
    chain = _chain(entries)
    on_chain = {rev for rev, _ in chain}
    assert len(chain) == len(entries), (
        "有迁移不在主链上（分支或孤立文件）："
        f"{sorted(set(entries) - on_chain)}"
    )


def test_ids_after_0069_are_sequential_numbered():
    entries = _load()
    chain = _chain(entries)
    revs = [rev for rev, _ in chain]
    start = revs.index(f"{SEQUENTIAL_FROM:04d}")
    numbered = revs[start:]

    for rev in numbered:
        assert re.fullmatch(r"\d{4}", rev), (
            f"{rev} 不是 4 位顺序号：新迁移请接着 head 取号（取号前先 git pull 看 head）；"
            "若已经排到第 10000 个，见 docs/dev/migrations.md「编号会用完吗」"
        )
    numbers = [int(rev) for rev in numbered]
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers))), (
        f"编号不连续：{numbered}"
    )


def test_file_names_start_with_their_revision():
    """文件名与 revision 一致：git log 按文件名排就是时间线。"""
    entries = _load()
    chain = _chain(entries)
    for rev, name in chain:
        if re.fullmatch(r"\d{4}", rev):
            assert name.startswith(rev + "_"), (
                f"{name} 与 revision {rev} 不一致：文件名要以 revision 开头"
            )
