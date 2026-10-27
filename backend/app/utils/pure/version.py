"""
版本号比较 —— 「有没有新版本」的唯一判据。

为什么不用 packaging.version：它是第三方运行时依赖，而这里只需比较 `0.4.15` 这种三段式，
为一个比较函数把 packaging 塞进运行镜像不值得（distutils.version 在 3.12 已被移除）。
本模块无 IO、无配置、无异常——纯字符串进、整数出。

规则是 SemVer 的减法版：
- 前导 `v` 可有可无（发布 tag 是 `v0.4.15`，config 里是 `0.4.15`）；
- 段数不足补 0（`1.2` == `1.2.0`）；
- 预发布后缀比同号正式版**小**：`0.4.16-beta.1 < 0.4.16`；
- 预发布里的数字标识符按数值比（`beta.10 > beta.9`），不是按字符比；
- **认不出的版本号不报错**：比较一律返回 0，于是「有无更新」为否。
  宁可少提示一次，也不要因为上游一个畸形 tag 让全站弹出「有新版本」。

只回答「谁更新」，不回答「能不能直接升」：跨大版本要不要先跑迁移属于发布说明的事，
写进这里会逼着前端一起理解迁移规则。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 主判定只吃数字段；预发布后缀任意（beta / rc.1 / preview-2 都认）
_RELEASE_RE = re.compile(
    r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:-([0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


@dataclass(frozen=True)
class ParsedVersion:
    """解析结果：三段数字 + 预发布标识符元组（正式版为空元组）。"""

    release: tuple[int, int, int]
    pre: tuple[str, ...] = ()

    def __str__(self) -> str:
        base = ".".join(str(n) for n in self.release)
        return f"{base}-{'.'.join(self.pre)}" if self.pre else base


def parse_version(raw: object) -> ParsedVersion | None:
    """把 `v0.4.15` / `0.4.15` / `0.4.15-beta.1` 解析成可比较的结构；认不出返回 None。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text[:1] in ("v", "V"):
        text = text[1:].strip()
    if not text:
        return None
    m = _RELEASE_RE.match(text)
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    pre_parts = tuple(p for p in (pre or "").split(".") if p)
    return ParsedVersion(
        release=(int(major), int(minor or 0), int(patch or 0)),
        pre=pre_parts,
    )


def _cmp_pre(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    """预发布段比较：空 > 非空；数字标识符按数值；数字低于字母（SemVer §11）。"""
    if a == b:
        return 0
    if not a:
        return 1
    if not b:
        return -1
    for x, y in zip(a, b):
        if x == y:
            continue
        x_num, y_num = x.isdigit(), y.isdigit()
        if x_num and y_num:
            return 1 if int(x) > int(y) else -1
        if x_num != y_num:
            return -1 if x_num else 1
        return 1 if x > y else -1
    # 公共前缀相同：段数多的更新（1.0.0-beta < 1.0.0-beta.1）
    if len(a) == len(b):
        return 0
    return 1 if len(a) > len(b) else -1


def compare_versions(a: object, b: object) -> int:
    """a 与 b 比较：a 更新返回 1，相同返回 0，a 更旧返回 -1；任一侧认不出返回 0。"""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return 0                      # 认不出＝不判更新（见模块说明最后一条）
    if pa.release != pb.release:
        return 1 if pa.release > pb.release else -1
    return _cmp_pre(pa.pre, pb.pre)


def is_newer(candidate: object, current: object) -> bool:
    """candidate 是否严格新于 current（相同或认不出都返回 False）。"""
    return compare_versions(candidate, current) > 0


def normalize_version(raw: object) -> str:
    """用于展示与归档的规范形式：去掉前缀 v 与空白；认不出就原样去掉空白返回。"""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    parsed = parse_version(text)
    return str(parsed) if parsed is not None else text.lstrip("vV")
