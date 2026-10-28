#!/usr/bin/env python3
"""发版三步：check / archive / tag / verify。

发版原来是一串靠记忆拼起来的动作：改版本号、写 CHANGELOG、打 tag、推两个仓库、盯三个平台的
构建、再人工核对产物与 latest。这里面绝大多数是**判据**，判据该写进代码而不是每次重新想一遍：

- 版本号只在一处定义：`backend/app/config.py::app_version`（tag 必须与它一致）；
- CHANGELOG 必须有这个版本段（发版前把 `[未发布]` 归档）；
- 打 tag 时关键文件不能有未提交改动（否则 tag 里的 CHANGELOG/版本号和你以为的不是一份）；
- 推送 tag 之后由 `.github/workflows/release.yml` 校验 tag==app_version 并通知发布仓构建，
  **不需要**在发布仓里再手工打一次同名 tag。

用法（仓库根目录）：

    python scripts/release.py check              # 只读预检
    python scripts/release.py check --tests      # 预检 + 跑全量后端测试（需要后端容器与 .env）
    python scripts/release.py archive            # 把 [未发布] 归档成 ## [vX] - 日期
    python scripts/release.py tag --push         # 打注释 tag 并推主仓（推 tag 即触发发布仓构建）
    python scripts/release.py verify             # 发布后核对产物与 latest

退出码：0 表示全部通过；1 表示有 ✘（check 会一次列全，不必逐条试）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "backend" / "app" / "config.py"
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
RELEASE_REPO = "Coprexist/Copree-Releases"
# 发版关键文件：这些文件带着未提交改动就打 tag，等于把"你以为的内容"和 tag 里的内容分开
CRITICAL_PATHS = (
    "backend/app/config.py",
    "CHANGELOG.md",
    ".github/workflows/release.yml",
    "scripts/release.py",
    "frontend/package.json",
)

_OK, _BAD, _WARN = "✔", "✘", "·"


def _run(*args: str, check: bool = False, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=check, timeout=timeout)


def read_version() -> str:
    """版本号唯一来源：config.py 的 app_version 默认值。"""
    text = CONFIG.read_text(encoding="utf-8")
    found = re.search(r'^\s*app_version:\s*str\s*=\s*"([^"]+)"', text, re.M)
    if not found:
        raise SystemExit(f"✘ 在 {CONFIG.relative_to(ROOT)} 里找不到 app_version —— 版本号必须只有一个出处")
    return found.group(1)


def tag_exists(version: str) -> bool:
    return _run("git", "rev-parse", "--verify", "--quiet", f"refs/tags/v{version}").returncode == 0


def tag_exists_remote(version: str) -> bool:
    try:
        return bool(_run("git", "ls-remote", "--tags", "origin",
                         f"refs/tags/v{version}", timeout=15).stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return False        # 离线/远端不可达时以本地 tag 为准，别让预检卡住


def changelog_has_release(version: str) -> bool:
    text = CHANGELOG.read_text(encoding="utf-8")
    return re.search(rf"^## \[v?{re.escape(version)}\]", text, re.M) is not None


def dirty_critical_paths() -> list[str]:
    status = _run("git", "status", "--porcelain").stdout.splitlines()
    dirty = {line[3:].strip().strip('"') for line in status if len(line) > 3}
    return sorted(p for p in CRITICAL_PATHS if p in dirty)


def _changelog_section(version: str) -> str:
    """取 CHANGELOG 里该版本段的正文（当 tag 注释用：产物页面能直接看到这版改了什么）。"""
    text = CHANGELOG.read_text(encoding="utf-8")
    found = re.search(rf"^## \[v?{re.escape(version)}\].*$", text, re.M)
    if not found:
        return f"v{version}"
    rest = text[found.end():]
    end = re.search(r"^## \[", rest, re.M)
    body = (rest[: end.start()] if end else rest).strip()
    return f"v{version}\n\n{body}"[:6000]


def cmd_check(args: argparse.Namespace) -> int:
    version = args.version or read_version()
    problems: list[str] = []
    print(f"{_OK} 版本号 {version}（唯一来源 backend/app/config.py::app_version）")

    if args.version and args.version != read_version():
        problems.append(f"config.py 里是 {read_version()}，与 --version {args.version} 不一致")
        print(f"{_BAD} 版本号与 --version 不一致")

    if tag_exists(version) or tag_exists_remote(version):
        problems.append(f"tag v{version} 已存在：版本号不能复用，先把 app_version 改成新版本")
        print(f"{_BAD} tag v{version} 已存在")
    else:
        print(f"{_OK} tag v{version} 未被占用")

    if changelog_has_release(version):
        print(f"{_OK} CHANGELOG 有 v{version} 段")
    else:
        problems.append(f"CHANGELOG 没有 ## [v{version}] 段：先 archive 归档 [未发布]")
        print(f"{_BAD} CHANGELOG 缺少 v{version} 段")

    dirty = dirty_critical_paths()
    if not dirty:
        print(f"{_OK} 发版关键文件都已提交")
    elif getattr(args, "allow_dirty", False):
        print(f"{_WARN} 关键文件有未提交改动（已用 --allow-dirty 放行，它们不会进 tag）：{'、'.join(dirty)}")
    else:
        problems.append("发版关键文件有未提交改动：" + "、".join(dirty)
                        + "（确认它们不在本次发布里可加 --allow-dirty）")
        print(f"{_BAD} 关键文件有未提交改动：{'、'.join(dirty)}")

    others = [line for line in _run("git", "status", "--porcelain").stdout.splitlines() if line]
    if len(others) > len(dirty):
        print(f"{_WARN} 工作区还有 {len(others) - len(dirty)} 处其它未提交改动（不会进 tag，确认与本次发版无关即可）")

    if RELEASE_WORKFLOW.exists():
        print(f"{_OK} 发布流程文件 {RELEASE_WORKFLOW.relative_to(ROOT)} 存在")
    else:
        problems.append(f"缺少 {RELEASE_WORKFLOW.relative_to(ROOT)}：tag 推上去也不会有人构建")
        print(f"{_BAD} 缺少 {RELEASE_WORKFLOW.relative_to(ROOT)}")

    if args.tests:
        print("…… 跑全量后端测试（约 70 秒）")
        ok = _run_tests()
        if not ok:
            problems.append("全量后端测试未通过")
        print(f"{_OK if ok else _BAD} 全量后端测试")
    else:
        print(f"{_WARN} 跳过了全量后端测试（加 --tests 一起跑）")

    if problems:
        print("\n需要先解决：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\n预检通过：可以 python scripts/release.py tag --push")
    return 0


def _run_tests() -> bool:
    """按 docs/guides/test_strategy.md 的口径跑全量（容器内、指向 _test 库）。"""
    if not shutil.which("docker"):
        print(f"{_BAD} 没有 docker，跑不了容器内测试")
        return False
    env_file = ROOT / ".env"
    password = ""
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("DB_PASSWORD="):
                password = line.split("=", 1)[1].strip()
                break
    if not password:
        print(f"{_BAD} 读不到 .env 里的 DB_PASSWORD")
        return False
    url = f"postgresql+asyncpg://ai_chat:{password}@ai_group_postgres:5432/ai_group_chat_test"
    sync = url.replace("+asyncpg", "")
    result = _run(
        "docker", "exec", "-w", "/app",
        "-e", f"TEST_DATABASE_URL={url}", "-e", f"TEST_DATABASE_URL_SYNC={sync}",
        "ai_group_backend", "python", "tests/run_without_pytest.py",
    )
    tail = [line for line in result.stdout.splitlines() if line.startswith("RESULT")]
    print("  " + (tail[-1] if tail else (result.stdout or result.stderr).strip()[-300:]))
    return "failed=0" in (tail[-1] if tail else "")


def cmd_archive(args: argparse.Namespace) -> int:
    """把 [未发布] 的条目归档成 ## [vX] - 日期，并留一个空的 [未发布]。"""
    version = args.version or read_version()
    text = CHANGELOG.read_text(encoding="utf-8")
    if changelog_has_release(version):
        print(f"{_BAD} CHANGELOG 已有 v{version} 段，不重复归档")
        return 1
    if "## [未发布]" not in text:
        print(f"{_BAD} CHANGELOG 里没有 ## [未发布]")
        return 1
    today = args.date or dt.date.today().isoformat()
    text = text.replace("## [未发布]", f"## [未发布]\n\n## [v{version}] - {today}", 1)
    CHANGELOG.write_text(text, encoding="utf-8")
    print(f"{_OK} 已归档：## [v{version}] - {today}（上面留了一个空的 [未发布]）")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    version = args.version or read_version()
    if cmd_check(argparse.Namespace(version=version, tests=False,
                                    allow_dirty=args.allow_dirty)) != 0:
        print("\n预检没过，不打 tag。")
        return 1
    message = _changelog_section(version)
    _run("git", "tag", "-a", f"v{version}", "-m", message, check=True)
    print(f"{_OK} 已打注释 tag v{version}")
    if not args.push:
        print("\n下一步（确认无误后）：")
        print("  git push origin HEAD:main")
        print(f"  git push origin v{version}")
        return 0
    for command in (("git", "push", "origin", "HEAD:main"), ("git", "push", "origin", f"v{version}")):
        done = _run(*command)
        if done.returncode != 0:
            print(f"{_BAD} {' '.join(command)} 失败：{done.stderr.strip()[:300]}")
            return 1
        print(f"{_OK} {' '.join(command)}")
    print("\n已推送：release.yml 会校验 tag==app_version 后通知发布仓构建三平台，用 verify 跟结果。")
    return 0


def _get_json(url: str, *, api: bool = False) -> dict | list:
    """取一段 JSON。api=True 走 GitHub 形状的请求头，需要 token 时读 GITHUB_TOKEN/GH_TOKEN。"""
    headers = {"User-Agent": "copree-release",
               "Accept": "application/vnd.github+json" if api else "application/json"}
    request = urllib.request.Request(url, headers=headers)
    if api:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=20) as resp:      # noqa: S310 —— 稳定 URL 常量
        return json.loads(resp.read().decode())


def cmd_verify(args: argparse.Namespace) -> int:
    version = args.version or read_version()
    tag = f"v{version}"
    problems: list[str] = []
    try:
        releases = _get_json(f"https://api.github.com/repos/{args.repo}/releases?per_page=20", api=True)
    except urllib.error.URLError as e:
        print(f"{_BAD} 拉不到发布列表：{e}")
        return 1
    found = next((r for r in releases if r.get("tag_name") == tag), None)
    if not found:
        problems.append(f"{args.repo} 里还没有 {tag} 的 Release（构建可能仍在跑）")
        print(f"{_BAD} 没有 {tag} 的 Release")
    else:
        print(f"{_OK} Release {tag} 存在")
        assets = found.get("assets") or []
        for asset in assets:
            size = int(asset.get("size") or 0)
            mark = _OK if size > 0 else _BAD
            if size <= 0:
                problems.append(f"产物 {asset.get('name')} 是空文件")
            print(f"{mark} {asset.get('name')}  {size / 1024 / 1024:.1f} MB")
        if len(assets) < 3:
            problems.append(f"产物只有 {len(assets)} 个，三平台应有 3 个")
        if found.get("draft") or found.get("prerelease"):
            print(f"{_WARN} 这条 Release 仍是 draft/prerelease（stable 渠道不会推给用户）")

    latest = _get_json(f"https://api.github.com/repos/{args.repo}/releases/latest", api=True)
    if latest.get("tag_name") == tag:
        print(f"{_OK} latest 指向 {tag}")
    else:
        problems.append(f"latest 仍指向 {latest.get('tag_name')}")
        print(f"{_BAD} latest 指向 {latest.get('tag_name')}")

    if args.site:
        try:
            payload = _get_json(args.site.rstrip("/") + "/api/system/update-status")
        except (urllib.error.URLError, ValueError) as e:
            print(f"{_WARN} 站点 update-status 取不到：{e}")
        else:
            print(f"{_WARN if payload else _BAD} 站点 update-status：当前 "
                  f"{payload.get('current')} / 最新 {payload.get('latest')}")

    if problems:
        print("\n还没齐：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\n发布核对通过。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Copree 发版三步（check / archive / tag / verify）")
    # --version 挂成每个子命令都能用：archive --version 0.4.16 这种读法最顺，
    # 不必记 argparse 的"参数要放在子命令前面"。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--version", help="目标版本号（默认读 config.py 的 app_version）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", parents=[common], help="只读预检，不改任何东西")
    check.add_argument("--tests", action="store_true", help="顺带跑全量后端测试")
    check.add_argument("--allow-dirty", action="store_true",
                       help="承认发版关键文件里的未提交改动不在本次发布里")
    check.set_defaults(func=cmd_check)

    archive = sub.add_parser("archive", parents=[common], help="把 [未发布] 归档成 ## [vX] - 日期")
    archive.add_argument("--date", help="归档日期（默认今天）")
    archive.set_defaults(func=cmd_archive)

    tag = sub.add_parser("tag", parents=[common], help="打注释 tag（--push 连主仓一起推）")
    tag.add_argument("--push", action="store_true", help="推 main 与 tag（推 tag 会触发发布仓构建）")
    tag.add_argument("--allow-dirty", action="store_true", help="同 check 的 --allow-dirty")
    tag.set_defaults(func=cmd_tag)

    verify = sub.add_parser("verify", parents=[common], help="发布后核对产物、latest 与站点版本")
    verify.add_argument("--repo", default=RELEASE_REPO, help=f"发布仓（默认 {RELEASE_REPO}）")
    verify.add_argument("--site", help="站点地址，如 https://coprexist.com（可选）")
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
