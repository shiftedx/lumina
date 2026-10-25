"""Minimal environment and fail-closed Linux policy for one-operation parsers."""
from __future__ import annotations

import os
import ctypes
import errno
import fcntl
import platform
import sys
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from app.config import settings


def parser_environment() -> dict[str, str]:
    # Build from literals: app settings, loader hooks, user Python paths and
    # proxy credentials must never cross into an untrusted native parser.
    environment = {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "no_proxy": "*",
    }
    if sys.platform == "linux":
        # libarchive-c otherwise shells out to ldconfig/gcc to find this soname.
        # Use the installed library directly, without granting tool execution.
        environment["LIBARCHIVE"] = "libarchive.so.13"
    return environment


class _Argument(ctypes.Structure):
    # Public libseccomp scmp_arg_cmp ABI; no architecture-specific syscall ABI.
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]


# libseccomp owns syscall resolution, architecture checks (including x32), and
# BPF generation. Never load a filter in this multithreaded parent process.
def _export_socket_filter(fd: int) -> None:
    library = ctypes.CDLL("libseccomp.so.2")
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                              ctypes.c_uint, ctypes.POINTER(_Argument)]
    library.seccomp_rule_add_array.restype = ctypes.c_int
    library.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.seccomp_export_bpf.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_release.restype = None
    context = library.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW; bad architectures are killed.
    if not context:
        raise OSError("Parser seccomp policy could not be initialized")
    try:
        def deny(name: str, error: int = errno.EPERM, comparison: _Argument | None = None) -> None:
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            # Negative pseudo numbers are libseccomp's portable aliases (e.g.
            # fork on arm64). Only -1 is the documented unknown-syscall result.
            if number == -1 or library.seccomp_rule_add_array(
                context, 0x00050000 | error, number, int(comparison is not None),
                ctypes.byref(comparison) if comparison is not None else None,
            ) < 0:
                raise OSError("Parser seccomp policy could not be compiled")

        for name in (
            "socket", "socketpair", "io_uring_setup", "ptrace",
            "process_vm_readv", "process_vm_writev", "pidfd_getfd",
            "chmod", "fchmod", "fchmodat", "fchmodat2", "chown", "fchown", "lchown", "fchownat",
            "utime", "utimes", "futimesat", "utimensat", "setxattr", "lsetxattr", "fsetxattr",
            "setxattrat", "removexattr", "lremovexattr", "fremovexattr", "removexattrat", "ioctl",
            "kill", "tkill", "tgkill", "rt_sigqueueinfo", "rt_tgsigqueueinfo", "pidfd_send_signal",
            "fork", "vfork",
            "unshare", "setns", "mount", "umount2", "pivot_root", "chroot",
            "fsopen", "fsconfig", "fsmount", "fspick", "move_mount", "open_tree", "mount_setattr",
        ):
            deny(name)
        # Both supported ABIs put clone flags in argument 0. Deny creation of
        # processes while keeping codec pthreads. glibc falls back to clone when
        # clone3 returns ENOSYS, so the pointer-based clone3 API is unnecessary.
        deny("clone", comparison=_Argument(0, 7, 0x00010000, 0))  # MASKED_EQ(CLONE_THREAD, 0)
        deny("clone3", errno.ENOSYS)
        # Kernel-generated SIGIO can otherwise target the supervisor even when
        # signal syscalls are denied. Preserve ordinary descriptor flag queries.
        for name in ("fcntl", "fcntl64"):
            for operation in (fcntl.F_SETOWN, fcntl.F_SETSIG, 15):  # Linux F_SETOWN_EX
                # The kernel reads cmd as unsigned int, ignoring its upper word.
                deny(name, comparison=_Argument(1, 7, 0xffffffff, operation))  # MASKED_EQ(low32)
        if library.seccomp_export_bpf(context, fd) < 0:
            raise OSError("Parser seccomp policy could not be exported")
    finally:
        library.seccomp_release(context)


@contextmanager
def parser_command(argv: list[str], source: Path, target: Path | None = None) -> Iterator[list[str]]:
    # Native source runs on macOS retain the portable environment/resource limits;
    # only Linux has this qualified filesystem/socket boundary.
    if sys.platform != "linux":
        yield argv
        return
    if platform.machine() not in {"aarch64", "x86_64"}:
        raise OSError("Parser sandbox supports Linux arm64 and amd64 only")
    if settings.parser_sandbox not in {"landlock", "namespace"}:
        raise OSError("Parser sandbox must be landlock or namespace")
    # Explicit rights through TRUNCATE require Landlock ABI 3 (Linux >= 6.2).
    # setpriv aborts before exec if any requested right or kernel policy fails.
    rights = ("execute,write-file,read-file,read-dir,remove-dir,remove-file,make-char,"
              "make-dir,make-reg,make-sock,make-fifo,make-block,make-sym,refer,truncate")
    with tempfile.NamedTemporaryFile(prefix="lumina-parser-", suffix=".bpf") as policy:
        _export_socket_filter(policy.fileno())
        if settings.parser_sandbox == "namespace":
            command = [
                "/usr/bin/bwrap", "--unshare-user", "--unshare-pid", "--unshare-net",
                "--unshare-ipc", "--unshare-uts", "--die-with-parent", "--new-session",
                "--cap-drop", "ALL", "--clearenv",
            ]
            for key, value in parser_environment().items():
                command.extend(["--setenv", key, value])
            command.extend(["--ro-bind", "/usr", "/usr"])
            for path in (Path("/lib"), Path("/lib64")):
                if path.is_symlink():
                    command.extend(["--symlink", os.readlink(path), str(path)])
                elif path.exists():
                    command.extend(["--ro-bind", str(path), str(path)])
            for path in dict.fromkeys((Path(sys.prefix), Path(sys.base_prefix), Path(__file__).resolve().parents[1])):
                if not path.is_relative_to("/usr"):
                    command.extend(["--ro-bind", str(path), str(path)])
            for path in (Path("/etc/ld.so.cache"), Path("/etc/localtime")):
                if path.exists():
                    command.extend(["--ro-bind", str(path), str(path)])
            command.extend([
                "--dir", "/proc", "--dir", "/dev", "--dev-bind", "/dev/null", "/dev/null",
                "--dir", "/tmp", "--ro-bind", str(source.absolute()), str(source.absolute()),
                "--ro-bind", policy.name, "/policy.bpf", "--chdir", str(Path(__file__).resolve().parents[2]),
            ])
            if target is not None:
                command.extend(["--bind", str(target.absolute()), str(target.absolute())])
            # The root tmpfs is immutable; only the selected output bind stays writable.
            command.extend(["--remount-ro", "/"])
            yield command + ["--", "/usr/bin/setpriv", "--no-new-privs", "--seccomp-filter", "/policy.bpf", "--", *argv]
            return
        command = ["/usr/bin/setpriv", "--no-new-privs", "--seccomp-filter", policy.name]

        command.extend(["--landlock-access", f"fs:{rights}"])

        def allow(path: Path, access: str) -> None:
            command.extend(["--landlock-rule", f"path-beneath:{access}:{path.resolve(strict=True)}"])

        # Installed runtime and code only; no app data, library, home, model cache,
        # /proc, or general temporary directory grant. Interpreter exec is exact.
        for path in {Path("/usr"), Path("/lib"), Path("/lib64"), Path(sys.prefix), Path(sys.base_prefix)}:
            if path.exists():
                allow(path, "read-file,read-dir")
        allow(Path(sys.executable), "execute,read-file")
        for path in (Path("/lib/ld-linux-aarch64.so.1"), Path("/lib64/ld-linux-x86-64.so.2")):
            if path.exists():
                allow(path, "execute,read-file")
        allow(Path(__file__).resolve().parents[1], "read-file,read-dir")
        for path in (Path("/etc/ld.so.cache"), Path("/etc/localtime")):
            if path.exists():
                allow(path, "read-file")
        allow(source, "read-file")
        if target is not None:
            allow(target, "read-file,read-dir,write-file,make-reg,remove-file,truncate")
        if argv[1:2] == ["-m"]:
            # PathFinder lists a module search root before importing a package.
            # Load this package explicitly, so /app/backend (and its data tree)
            # never needs a recursive directory-listing grant.
            package = Path(__file__).resolve().parents[1] / "__init__.py"
            bootstrap = (
                "import importlib.util,runpy,sys; "
                f"s=importlib.util.spec_from_file_location('app',{str(package)!r}); "
                "m=importlib.util.module_from_spec(s); sys.modules['app']=m; "
                "s.loader.exec_module(m); name=sys.argv.pop(1); "
                "runpy.run_module(name,run_name='__main__',alter_sys=True)"
            )
            argv = [argv[0], "-c", bootstrap, *argv[2:]]
        yield command + ["--", *argv]
