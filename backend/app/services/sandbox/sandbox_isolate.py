"""
沙箱隔离原语 — Landlock（文件系统）+ seccomp-BPF（系统调用黑名单）。

Landlock 把文件系统访问锁死在授权路径，其余路径一律 EACCES（比 chroot 轻量、无需 root）；
seccomp 按调用方档位禁网络/进程创建与危险系统调用。两者都在 exec 之后施加，失败只告警降级
（纵深防御的一层，不因自身故障影响功能）。构成与档位见 docs/dev/code_sandbox.md。
"""
from __future__ import annotations

import ctypes
import errno
import logging
import os
import platform
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Landlock ──
SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_RULE_PATH_BENEATH = 1

LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 13
# 全部 14 项（ABI v1 全集；不含 EXECUTE 时单独处理）
ALL_FS_ACCESS = (1 << 14) - 1
# 工作目录授予全集（不含 EXECUTE——被跑的代码不需要执行文件）
FS_FULL_NO_EXEC = ALL_FS_ACCESS & ~LANDLOCK_ACCESS_FS_EXECUTE
# 只读授予（额外只读目录，如 skill 代码目录）
FS_READONLY = LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


# ── seccomp（x86_64 syscall 号）──
PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000

BPF_LD = 0x00
BPF_W = 0x00
BPF_ABS = 0x20
BPF_JMP = 0x05
BPF_JEQ = 0x10
BPF_K = 0x00
BPF_RET = 0x06

# 禁用的危险系统调用（x86_64）。网络类仅 skill 沙箱禁（世界代码沙箱保留，受控 API 走 HTTP）。
# 进程创建类（fork/clone）：skill 沙箱禁（纯计算+协议，无线程需求）；世界代码沙箱保留
# （可能用 threading/线程池，数量由 NPROC rlimit 限制，且 execve 仍禁——fork 后无法 exec 真子进程）
DENY_SYSCALLS_PROCESS_CREATION = {
    56: "clone", 57: "fork", 58: "vfork", 435: "clone3", 272: "unshare",
}
DENY_SYSCALLS_GENERIC = {
    # 进程/执行
    59: "execve", 322: "execveat",
    # 内核/权限
    165: "mount", 166: "umount2", 167: "swapon", 168: "swapoff", 155: "pivot_root", 161: "chroot",
    308: "setns", 173: "iopl", 172: "ioperm", 163: "acct", 169: "reboot", 170: "sethostname",
    171: "setdomainname", 101: "ptrace", 310: "process_vm_readv", 311: "process_vm_writev",
    # 模块/固件/内核接口
    175: "init_module", 313: "finit_module", 176: "delete_module", 246: "kexec_load",
    320: "kexec_file_load", 321: "bpf", 298: "perf_event_open", 425: "io_uring_setup",
    426: "io_uring_enter", 427: "io_uring_register",
    # 文件句柄/配额/fanotify（绕过路径控制的句柄操作）
    304: "open_by_handle_at", 303: "name_to_handle_at", 300: "fanotify_init", 301: "fanotify_mark",
    179: "quotactl", 288: "accept4",
}
DENY_SYSCALLS_NET = {
    41: "socket", 42: "connect", 43: "accept", 49: "bind", 50: "listen",
    # 53 socketpair 放行：asyncio 事件循环 self-pipe 必需，仅本地管道对（无网络出口）
}
# xattr 写/删（文件系统元数据越权路径；Landlock 不管 xattr）
DENY_SYSCALLS_XATTR = {188: "setxattr", 189: "lsetxattr", 190: "fsetxattr",
                       194: "removexattr", 195: "lremovexattr", 196: "fremovexattr"}


class _SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8),
                ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(_SockFilter))]


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def apply_landlock(read_dirs: list[str], write_dirs: list[str] | None = None) -> bool:
    """把当前进程文件系统锁死：read_dirs 只读 + write_dirs 读写；其余路径全部拒绝。

    返回是否成功（失败返回 False，调用方决定是否继续）。线程安全要求：必须在
    单线程阶段调用（restrict_self 只影响当前线程——子进程入口处调用即全进程生效）。
    """
    libc = _libc()
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        logger.warning("🛡️ landlock: prctl(NO_NEW_PRIVS) 失败 errno=%s，跳过", ctypes.get_errno())
        return False

    # 只处理 read_dirs + write_dirs 的并集；handled = 全部权限（除 EXECUTE）
    handled = FS_FULL_NO_EXEC
    attr = _RulesetAttr(handled)
    fd = libc.syscall(SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if fd < 0:
        e = ctypes.get_errno()
        logger.warning("🛡️ landlock: create_ruleset 失败 errno=%s（%s），跳过", e, errno.errorcode.get(e, "?"))
        return False

    try:
        write_dirs = write_dirs or []
        for path in [*read_dirs, *write_dirs]:
            p = Path(path)
            if not p.is_dir():
                continue
            dfd = os.open(p, os.O_RDONLY | os.O_DIRECTORY)
            try:
                perm = FS_READONLY if path in read_dirs and path not in write_dirs else FS_FULL_NO_EXEC
                beneath = _PathBeneath(perm, dfd)
                r = libc.syscall(SYS_LANDLOCK_ADD_RULE, fd, LANDLOCK_RULE_PATH_BENEATH,
                                 ctypes.byref(beneath), 0)
                if r != 0:
                    logger.warning("🛡️ landlock: add_rule %s 失败 errno=%s", path, ctypes.get_errno())
            finally:
                os.close(dfd)
        r = libc.syscall(SYS_LANDLOCK_RESTRICT_SELF, fd, 0)
        if r != 0:
            logger.warning("🛡️ landlock: restrict_self 失败 errno=%s", ctypes.get_errno())
            return False
        return True
    finally:
        os.close(fd)


def apply_seccomp(deny_net: bool = True, deny_process_creation: bool = False) -> bool:
    """seccomp-BPF 黑名单。

    - deny_net=True：连网络一起禁（skill 沙箱）
    - deny_process_creation=True：连 fork/clone 一起禁（skill 沙箱纯计算+协议，无线程需求）；
      世界代码沙箱保留（可能用线程池，NPROC rlimit 限制数量，execve 仍禁）
    """
    if platform.machine() != "x86_64":
        logger.warning("🛡️ seccomp: 非 x86_64 平台跳过（%s）", platform.machine())
        return False
    libc = _libc()
    deny = set(DENY_SYSCALLS_GENERIC) | set(DENY_SYSCALLS_XATTR)
    if deny_net:
        deny |= set(DENY_SYSCALLS_NET)
    if deny_process_creation:
        deny |= set(DENY_SYSCALLS_PROCESS_CREATION)

    filters: list[_SockFilter] = []
    filters.append(_SockFilter(BPF_LD | BPF_W | BPF_ABS, 0, 0, 0))
    for nr in sorted(deny):
        filters.append(_SockFilter(BPF_JMP | BPF_JEQ | BPF_K, 0, 1, nr))
        filters.append(_SockFilter(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ERRNO | errno.EPERM))
    filters.append(_SockFilter(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ALLOW))

    arr = (_SockFilter * len(filters))(*filters)
    prog = _SockFprog(len(filters), arr)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        logger.warning("🛡️ seccomp: prctl(NO_NEW_PRIVS) 失败 errno=%s，跳过", ctypes.get_errno())
        return False
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0) != 0:
        logger.warning("🛡️ seccomp: prctl(SECCOMP_FILTER) 失败 errno=%s，跳过", ctypes.get_errno())
        return False
    return True


def apply_isolate(*, work_dir: str | None = None, read_dirs: list[str] | None = None,
                  deny_net: bool = True, deny_process_creation: bool = True,
                  stdlib_readonly: bool = True, readonly: bool = False) -> dict:
    """子进程入口统一调用：Landlock（锁文件系统）+ seccomp（禁危险调用）。

    - work_dir：本次执行授权读写的那个目录（世界目录 / AI 文件空间）；
      read_dirs：额外只读目录（如 skill 代码目录）
    - deny_net：禁网络（AI 脚本 True；世界代码沙箱 False——受控 API 是 HTTP）
    - deny_process_creation：禁 fork/clone（AI 脚本 True；世界代码沙箱 False——线程池兼容）
    - stdlib_readonly：授权标准库目录只读（隔离后仍可 import 标准库；
      标准库是公开代码无敏感信息，只读授权风险可忽略）
    - readonly：work_dir 只进 read_dirs、不进 write_dirs（计划模式的只读运行；
      写文件一律 EACCES。注意世界代码仍有两条不经文件系统的写路径——受控数据 API 与
      群聊写 API——由 API token 的只读前缀在服务端封堵，见 world_proxy._authorize_world_api）
    返回 {"landlock": bool, "seccomp": bool} 各层是否生效（仅记录用）。
    """
    read_dirs = list(read_dirs or [])
    write_dirs = [] if readonly else ([work_dir] if work_dir else [])
    if work_dir:
        read_dirs.append(work_dir)
    if stdlib_readonly:
        try:
            import sysconfig
            for key in ("stdlib", "platstdlib"):
                p = sysconfig.get_paths().get(key)
                if p and os.path.isdir(p) and p not in read_dirs:
                    read_dirs.append(p)
        except Exception:  # noqa: BLE001
            pass
        # ⚠️ 2026-08-13 修复：还要授权动态链接库目录（只读）——Python 运行时
        # dlopen 系统库（如 urllib → libz.so.1）时 Landlock 已生效，若 /lib、/usr/lib
        # 不在授权列表 → EACCES → 'libz.so.1: cannot open shared object file'。
        # 系统库是公开代码无敏感信息，只读授权风险可忽略（与 stdlib 同理）。
        try:
            for lib_dir in ("/lib", "/usr/lib", "/usr/local/lib"):
                if os.path.isdir(lib_dir) and lib_dir not in read_dirs:
                    read_dirs.append(lib_dir)
        except Exception:  # noqa: BLE001
            pass
    landlock_ok = False
    if read_dirs or write_dirs:
        try:
            landlock_ok = apply_landlock(read_dirs, write_dirs)
        except Exception as e:  # noqa: BLE001 —— 隔离层故障不阻断执行
            logger.warning("🛡️ landlock 应用异常（降级继续）: %s", e)
    seccomp_ok = False
    try:
        seccomp_ok = apply_seccomp(deny_net=deny_net, deny_process_creation=deny_process_creation)
    except Exception as e:  # noqa: BLE001
        logger.warning("🛡️ seccomp 应用异常（降级继续）: %s", e)
    return {"landlock": landlock_ok, "seccomp": seccomp_ok}
