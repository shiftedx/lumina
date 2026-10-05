"""safety policy: test I/O roots and network access for automated runs.

Run by the bootstrap lane (``python3 -m unittest discover -s scripts``) with
only the standard library. ``backend/tests/conftest.py`` imports this module
so pytest-side tests apply the exact same policy.

Rules (binding for every automated test in this repository):
- Test I/O must never target the live application data directory or host
  media mounts (``FORBIDDEN_ROOTS``); ``check_root_isolation`` raises before
  any I/O when a candidate path is inside one.
- Automated tests may only open loopback sockets (127.0.0.0/8, ::1,
  "localhost"); provider/external network access is denied by default.
  ``LoopbackOnlyGuard`` wraps the socket module to enforce this.
"""

from __future__ import annotations

import ipaddress
import socket
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# Live app data and mounted media volumes must never be test I/O targets.
FORBIDDEN_ROOTS: tuple[Path, ...] = (
    REPOSITORY_ROOT / "backend" / ".data",
    Path("/Volumes"),
)


def check_root_isolation(candidate: Path) -> Path:
    """Return *candidate* if it is a safe test I/O target, else raise."""
    resolved = candidate.resolve()
    for forbidden in FORBIDDEN_ROOTS:
        if resolved == forbidden or forbidden in resolved.parents:
            raise AssertionError(
                f"test I/O target {resolved} is under forbidden root {forbidden}"
            )
    return resolved


class LoopbackOnlyGuard:
    """Permit 127.0.0.0/8, ::1 and "localhost"; deny everything else."""

    def __init__(self) -> None:
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._getaddrinfo = socket.getaddrinfo

    @staticmethod
    def _allow(address: object) -> bool:
        if not isinstance(address, tuple) or not address:
            return False
        host = address[0]
        if isinstance(host, str):
            if host.lower() == "localhost":
                return True
            try:
                host = ipaddress.ip_address(host)
            except ValueError:
                return False  # unix socket path or name: deny
        return host.is_loopback

    def connect(self, sock: socket.socket, address: object) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not self._allow(address):
            raise AssertionError(
                f"provider/external network access blocked in tests: {address!r}"
            )
        self._connect(sock, address)

    def connect_ex(self, sock: socket.socket, address: object) -> int:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not self._allow(address):
            raise AssertionError(
                f"provider/external network access blocked in tests: {address!r}"
            )
        return self._connect_ex(sock, address)

    def getaddrinfo(self, host: str, port: object, *args: object, **kwargs: object) -> object:
        if host:
            lowered = host.lower().strip("[]")
            try:
                ipaddress.ip_address(lowered)
            except ValueError:
                if lowered != "localhost":
                    raise AssertionError(
                        f"external DNS resolution blocked in tests: {host!r}"
                    ) from None
        return self._getaddrinfo(host, port, *args, **kwargs)

    def apply(self, monkeypatch_setattr) -> None:
        """Install the guard via plain functions so class-attribute binding
        passes the socket instance as the first argument."""
        allow = self._allow
        connect = self._connect
        connect_ex = self._connect_ex
        getaddrinfo = self._getaddrinfo

        def guarded_connect(sock: socket.socket, address: object) -> None:
            if sock.family in (socket.AF_INET, socket.AF_INET6) and not allow(address):
                raise AssertionError(
                    f"provider/external network access blocked in tests: {address!r}"
                )
            connect(sock, address)

        def guarded_connect_ex(sock: socket.socket, address: object) -> int:
            if sock.family in (socket.AF_INET, socket.AF_INET6) and not allow(address):
                raise AssertionError(
                    f"provider/external network access blocked in tests: {address!r}"
                )
            return connect_ex(sock, address)

        def guarded_getaddrinfo(host: str, port: object, *args: object, **kwargs: object) -> object:
            if host:
                lowered = host.lower().strip("[]")
                try:
                    ipaddress.ip_address(lowered)
                except ValueError:
                    if lowered != "localhost":
                        raise AssertionError(
                            f"external DNS resolution blocked in tests: {host!r}"
                        ) from None
            return getaddrinfo(host, port, *args, **kwargs)

        monkeypatch_setattr(socket.socket, "connect", guarded_connect)
        monkeypatch_setattr(socket.socket, "connect_ex", guarded_connect_ex)
        monkeypatch_setattr(socket, "getaddrinfo", guarded_getaddrinfo)


class ForbiddenRootTests(unittest.TestCase):
    def test_rejects_live_app_data_directory(self) -> None:
        with self.assertRaises(AssertionError, msg="forbidden root"):
            check_root_isolation(REPOSITORY_ROOT / "backend" / ".data")

    def test_rejects_paths_inside_live_app_data(self) -> None:
        with self.assertRaises(AssertionError, msg="forbidden root"):
            check_root_isolation(REPOSITORY_ROOT / "backend" / ".data" / "cookies" / "x.txt")

    def test_rejects_media_mount_paths(self) -> None:
        with self.assertRaises(AssertionError, msg="forbidden root"):
            check_root_isolation(Path("/Volumes/MountedLibrary/movie.mkv"))

    def test_allows_repository_outside_forbidden_roots(self) -> None:
        self.assertEqual(
            check_root_isolation(REPOSITORY_ROOT / "output" / "tmp"),
            (REPOSITORY_ROOT / "output" / "tmp").resolve(),
        )

    def test_forbidden_roots_cover_live_app_data(self) -> None:
        self.assertIn(REPOSITORY_ROOT / "backend" / ".data", FORBIDDEN_ROOTS)


class LoopbackGuardTests(unittest.TestCase):
    def test_allows_loopback_addresses(self) -> None:
        guard = LoopbackOnlyGuard()
        self.assertTrue(guard._allow(("127.0.0.1", 80)))
        self.assertTrue(guard._allow(("127.9.9.9", 80)))
        self.assertTrue(guard._allow(("::1", 80)))
        self.assertTrue(guard._allow(("localhost", 80)))

    def test_denies_external_addresses_and_names(self) -> None:
        guard = LoopbackOnlyGuard()
        self.assertFalse(guard._allow(("93.184.215.14", 80)))
        self.assertFalse(guard._allow(("192.168.1.10", 80)))
        self.assertFalse(guard._allow(("example.com", 80)))
        self.assertFalse(guard._allow(("/var/run/sock",)))

    def test_getaddrinfo_denies_external_dns_but_allows_localhost(self) -> None:
        guard = LoopbackOnlyGuard()
        resolved = guard.getaddrinfo("localhost", 80)[0][4][0]
        self.assertTrue(
            ipaddress.ip_address(resolved).is_loopback,
            f"localhost must resolve to a loopback address, got {resolved}",
        )
        with self.assertRaises(AssertionError, msg="DNS resolution blocked"):
            guard.getaddrinfo("example.com", 80)

    def test_connect_denies_external_socket(self) -> None:
        guard = LoopbackOnlyGuard()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaises(AssertionError, msg="blocked in tests"):
                guard.connect(sock, ("93.184.215.14", 80))
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
