"""版本号比较的边界（纯函数，无 IO）。

这些用例守的是「宁可少提示一次，也不误报」那条线：上游 tag 畸形时不能把全站
变成「有新版本」——0.4.16-beta < 0.4.16 这类判断错了，用户装完 beta 会一直看到红点。
"""
from __future__ import annotations

from app.utils.pure.version import (
    compare_versions,
    is_newer,
    normalize_version,
    parse_version,
)


def test_parse_accepts_v_prefix_and_short_forms():
    assert parse_version("v0.4.15").release == (0, 4, 15)
    assert parse_version("0.4.15").release == (0, 4, 15)
    assert parse_version("  V1.2 ").release == (1, 2, 0)
    assert parse_version("1").release == (1, 0, 0)
    assert str(parse_version("v0.4.15")) == "0.4.15"
    assert str(parse_version("v1.0.0-rc.2")) == "1.0.0-rc.2"


def test_parse_rejects_garbage():
    for bad in ("", "latest", "v", "release-1", None, 1.5, "0.4.15.1"):
        assert parse_version(bad) is None, bad


def test_compare_is_numeric_not_lexical():
    assert compare_versions("0.10.0", "0.9.9") == 1
    assert compare_versions("0.4.15", "0.4.15") == 0
    assert compare_versions("1.2", "1.2.0") == 0
    assert compare_versions("v0.4.16", "0.4.15") == 1
    assert compare_versions("0.4.14", "0.4.15") == -1


def test_prerelease_is_older_than_release():
    assert compare_versions("0.4.16-beta.1", "0.4.16") == -1
    assert compare_versions("0.4.16", "0.4.16-beta.1") == 1
    # 预发布之间按数值比，不按字符比
    assert compare_versions("0.4.16-beta.10", "0.4.16-beta.9") == 1
    # 数字标识符低于字母标识符（SemVer §11）
    assert compare_versions("1.0.0-1", "1.0.0-alpha") == -1
    # 公共前缀相同：段数多的更新
    assert compare_versions("1.0.0-beta.1", "1.0.0-beta") == 1


def test_unknown_version_never_claims_newer():
    assert compare_versions("junk", "0.4.15") == 0
    assert is_newer("junk", "0.4.15") is False
    assert is_newer("0.4.15", "junk") is False


def test_is_newer_is_strict():
    assert is_newer("0.4.16", "0.4.15") is True
    assert is_newer("0.4.15", "0.4.15") is False
    assert is_newer("0.4.14", "0.4.15") is False


def test_normalize_keeps_unknown_text_for_display():
    assert normalize_version(" v0.4.15 ") == "0.4.15"
    assert normalize_version("0.4.15.1") == "0.4.15.1"
    assert normalize_version(None) == ""
