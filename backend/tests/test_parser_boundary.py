"""Linux release-image qualification; also runnable with the runtime's stdlib unittest."""
from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import EnvSettings
from app.services import book_native, parser_boundary, photo_derivatives
from app.services.book_archives import BookArchiveError


class ParserIsolationSettingsTests(unittest.TestCase):
    def test_sandbox_defaults_to_landlock_and_rejects_disabled_or_unknown_backends(self) -> None:
        from pydantic import ValidationError

        variable = 'LUMINA_PARSER_SANDBOX'
        with patch.dict(os.environ):
            os.environ.pop(variable, None)
            self.assertEqual(EnvSettings().parser_sandbox, 'landlock')
            for value in ('landlock', 'namespace'):
                os.environ[variable] = value
                self.assertEqual(EnvSettings().parser_sandbox, value)
            for value in ('off', 'false', '0', '', 'automatic', 'unexpected'):
                os.environ[variable] = value
                with self.subTest(value=value), self.assertRaises(ValidationError):
                    EnvSettings()


@unittest.skipUnless(sys.platform == 'linux', 'Linux sandbox; macOS retains portable limits only')
class ParserBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'input.jpg'
        self.source.write_bytes(b'synthetic input')
        self.unrelated = self.root / 'unrelated'
        self.unrelated.write_text('synthetic unrelated marker')
        self.target = self.root / 'staging'
        self.target.mkdir()
        self.children: list[subprocess.Popen] = []

    def substitute(self, program: str, *, prefix: tuple[str, ...] = ()):
        real_popen = subprocess.Popen

        def launch(argv, **kwargs):
            # Keep the real setpriv policy and supervision; replace only the codec.
            offset = len(argv) - argv[::-1].index('--') if '--' in argv else 0
            process = real_popen([*prefix, *argv[:offset], sys.executable, '-c', program], **kwargs)
            self.children.append(process)
            return process

        return patch.object(subprocess, 'Popen', launch)

    def invoke(self, parser: str):
        if parser == 'photo':
            return photo_derivatives._run('prepare', self.source, self.target)
        return book_native.native_book(self.source, 'pdf')

    def assert_reaped(self) -> None:
        for process in self.children:
            self.assertIsNotNone(process.returncode)
            with self.assertRaises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)

    def test_actual_launch_denies_unrelated_files_source_writes_and_all_sockets(self) -> None:
        with socket.socket() as inherited:
            os.set_inheritable(inherited.fileno(), True)
            program = f'''
import errno,json,os,socket
from pathlib import Path
result = {{'ok': True, 'input': Path({str(self.source)!r}).read_bytes().decode()}}
for name, action in [
    ('unrelated_read', lambda: Path({str(self.unrelated)!r}).read_bytes()),
    ('source_write', lambda: Path({str(self.source)!r}).write_bytes(b'changed')),
    ('ipv4', lambda: socket.socket(socket.AF_INET)),
    ('ipv6', lambda: socket.socket(socket.AF_INET6)),
    ('unix', lambda: socket.socket(socket.AF_UNIX)),
    ('socketpair', socket.socketpair),
]:
    try:
        action()
        result[name] = False
    except OSError as error:
        assert error.errno in (errno.EPERM, errno.EACCES, errno.ENOENT, errno.EROFS), (name, error.errno)
        result[name] = True
try:
    result['inherited_socket'] = os.fstat({inherited.fileno()}).st_ino == {os.fstat(inherited.fileno()).st_ino}
except OSError:
    result['inherited_socket'] = False
print(json.dumps(result))
'''
            for parser in ('photo', 'book'):
                with self.subTest(parser=parser), self.substitute(program):
                    result = self.invoke(parser)
                    self.assertEqual(result, {
                        'ok': True, 'input': 'synthetic input', 'unrelated_read': True,
                        'source_write': True, 'ipv4': True, 'ipv6': True, 'unix': True,
                        'socketpair': True, 'inherited_socket': False,
                    })
                    self.assertEqual(self.source.read_bytes(), b'synthetic input')
            self.assert_reaped()

    def test_metadata_signals_and_process_creation_are_denied_but_threads_work(self) -> None:
        before = self.unrelated.stat()
        program = f'''
import ctypes,fcntl,json,os,threading
from pathlib import Path
result = {{'ok': True}}
pipe_read, pipe_write = os.pipe()
library = ctypes.CDLL('libseccomp.so.2')
library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
number = library.seccomp_syscall_resolve_name(b'fcntl')
raw_syscall = ctypes.CDLL(None, use_errno=True).syscall
raw_syscall.restype = ctypes.c_long

def raw_owner():
    # Linux truncates cmd to unsigned int; retain the upper word at the raw
    # syscall boundary. This only assigns an owner, never enables/delivers SIGIO.
    command = ctypes.c_ulonglong((1 << 32) | fcntl.F_SETOWN)
    if raw_syscall(ctypes.c_long(number), ctypes.c_int(pipe_read), command, ctypes.c_int(os.getppid())) < 0:
        raise OSError(ctypes.get_errno(), 'synthetic fcntl owner probe')

for name, action in [
    ('chmod', lambda: os.chmod({str(self.unrelated)!r}, 0o600)),
    ('chown', lambda: os.chown({str(self.unrelated)!r}, os.getuid(), os.getgid())),
    ('utime', lambda: os.utime({str(self.unrelated)!r}, (1, 1))),
    ('xattr', lambda: os.setxattr({str(self.unrelated)!r}, 'user.lumina_test', b'synthetic')),
    ('signal', lambda: os.kill(os.getppid(), 0)),
    ('sigio_owner', lambda: fcntl.fcntl(pipe_read, fcntl.F_SETOWN, os.getppid())),
    ('sigio_owner_highword', raw_owner),
]:
    try:
        action()
        result[name] = False
    except PermissionError:
        result[name] = True
try:
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    result['fork'] = False
except PermissionError:
    result['fork'] = True
thread = threading.Thread(target=lambda: result.update(thread=True))
thread.start()
thread.join()
print(json.dumps(result))
'''
        for parser in ('photo', 'book'):
            with self.subTest(parser=parser), self.substitute(program):
                result = self.invoke(parser)
                self.assertEqual(result, {'ok': True, 'chmod': True, 'chown': True, 'utime': True,
                                          'xattr': True, 'signal': True, 'sigio_owner': True, 'sigio_owner_highword': True, 'fork': True, 'thread': True})
                after = self.unrelated.stat()
                self.assertEqual((before.st_mode, before.st_mtime_ns), (after.st_mode, after.st_mtime_ns))
                self.assertNotIn('user.lumina_test', os.listxattr(self.unrelated))
        self.assert_reaped()

    def test_staging_writes_and_installed_native_codecs(self) -> None:
        import pymupdf
        from PIL import Image
        from pillow_heif import register_heif_opener

        register_heif_opener()
        for format_ in ('JPEG', 'TIFF', 'HEIF'):
            with self.subTest(format=format_):
                Image.new('RGB', (16, 24), 'blue').save(self.source, format=format_)
                result = photo_derivatives._run('prepare', self.source, self.target)
                self.assertTrue(result['ok'])
                for width in photo_derivatives.WIDTHS:
                    self.assertTrue((self.target / f'{width}.webp').is_file())
        document = self.root / 'input.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((50, 50), 'Synthetic parser qualification')
            pdf.save(document)
        self.assertEqual(book_native.native_book(document, 'pdf')['pages'], ['page-0.png'])
        self.assertTrue(book_native.native_book(document, 'pdf', 'page-0.png').startswith(b'\x89PNG'))
        comic = Path(__file__).parent / 'fixtures/books/solid-rar5.cbr'
        manifest = book_native.native_book(comic, 'cbr')
        self.assertEqual(len(manifest['pages']), 120)
        for page in (manifest['pages'][0], manifest['pages'][-1]):
            self.assertTrue(book_native.native_book(comic, 'cbr', page).startswith(b'\x89PNG'))

    def test_namespace_and_mount_changes_are_denied_before_codec_import(self) -> None:
        program = r'''
import ctypes,errno,json,os
from pathlib import Path
libc = ctypes.CDLL(None, use_errno=True)
assert libc.prctl(21, 0, 0, 0, 0) == 2
assert libc.prctl(39, 0, 0, 0, 0) == 1
library = ctypes.CDLL('libseccomp.so.2')
library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
checked = []
for name, args in [
    ('unshare', [0x10000000]), ('setns', [-1, 0]),
    ('mount', [b'none', b'/tmp', b'tmpfs', 0, 0]), ('umount2', [b'/tmp', 0]),
    ('pivot_root', [b'/tmp', b'/tmp']), ('chroot', [b'/tmp']),
    ('fsopen', [b'tmpfs', 0]), ('fsconfig', [-1, 0, 0, 0, 0]), ('fsmount', [-1, 0, 0]),
    ('fspick', [-1, b'/', 0]), ('move_mount', [-1, b'/', -1, b'/', 0]),
    ('open_tree', [-1, b'/', 0]), ('mount_setattr', [-1, b'/', 0, 0, 0]),
]:
    number = library.seccomp_syscall_resolve_name(name.encode())
    assert number != -1
    ctypes.set_errno(0)
    assert libc.syscall(number, *args) == -1 and ctypes.get_errno() == errno.EPERM, name
    checked.append(name)
class Header(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
class Data(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]
header = Header(0x20080522, 0)
capabilities = (Data * 2)()
assert libc.capget(ctypes.byref(header), capabilities) == 0
result = {'ok': True, 'denied': checked, 'zero_caps': all(value.effective == value.permitted == value.inheritable == 0 for value in capabilities)}
if NAMESPACE:
    assert result['zero_caps']
    assert os.listdir('/proc') == []
    assert os.listdir('/dev') == ['null']
    for path in ('/unexpected-output', '/tmp/unexpected-output'):
        try:
            Path(path).write_bytes(b'forbidden')
            raise AssertionError('sandbox root must be read-only')
        except OSError as error:
            assert error.errno == errno.EROFS
print(json.dumps(result))
'''
        program = program.replace('NAMESPACE', repr(parser_boundary.settings.parser_sandbox == 'namespace'))
        for parser in ('photo', 'book'):
            with self.subTest(parser=parser), self.substitute(program):
                result = self.invoke(parser)
                self.assertEqual(len(result['denied']), 13)
        self.assert_reaped()

    def test_namespace_timeout_and_parent_death_reap_all_descendants(self) -> None:
        if parser_boundary.settings.parser_sandbox != 'namespace':
            self.skipTest('Namespace sandbox only')

        def descendants(pid: int) -> list[int]:
            try:
                children = [int(value) for value in Path(f'/proc/{pid}/task/{pid}/children').read_text().split()]
            except FileNotFoundError:
                return []
            return children + [descendant for child in children for descendant in descendants(child)]

        def wait_for_children(pid: int) -> list[int]:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                children = descendants(pid)
                if len(children) >= 2:
                    return children
                time.sleep(0.01)
            self.fail('Namespace init and codec did not start')

        def assert_all_reaped(children: list[int]) -> None:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(Path(f'/proc/{pid}').exists() for pid in children):
                time.sleep(0.01)
            self.assertFalse([pid for pid in children if Path(f'/proc/{pid}').exists()])

        children: list[int] = []
        real_popen = subprocess.Popen

        with self.substitute('import time; time.sleep(60)'):
            substituting_popen = subprocess.Popen

            def tracked(argv, **kwargs):
                process = substituting_popen(argv, **kwargs)
                try:
                    children.extend(wait_for_children(process.pid))
                except BaseException:
                    process.kill()
                    process.wait(timeout=3)
                    raise
                return process

            with patch.object(subprocess, 'Popen', tracked), patch.object(photo_derivatives, 'TIMEOUT_SECONDS', 0.05):
                with self.assertRaises(photo_derivatives.PhotoDecodeError):
                    self.invoke('photo')
        self.assert_reaped()
        assert_all_reaped(children)

        with parser_boundary.parser_command([sys.executable, '-c', 'import time; time.sleep(60)'], self.source, self.target) as command:
            launcher = 'import subprocess,sys,time; child=subprocess.Popen(sys.argv[1:]); print(child.pid,flush=True); time.sleep(60)'
            with real_popen(
                [sys.executable, '-c', launcher, *command], env=parser_boundary.parser_environment(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ) as supervisor:
                try:
                    wrapper = int(supervisor.stdout.readline())
                    children = [wrapper, *wait_for_children(wrapper)]
                finally:
                    supervisor.kill()
                    supervisor.wait(timeout=3)
            assert_all_reaped(children)

    def test_deadline_and_interrupted_supervision_reap_child(self) -> None:
        for interruption in (False, True):
            with self.subTest(interruption=interruption), self.substitute('import time; time.sleep(60)'):
                with patch.object(photo_derivatives, 'TIMEOUT_SECONDS', 0.05):
                    if interruption:
                        real_monotonic = photo_derivatives.time.monotonic
                        calls = 0

                        def interrupted():
                            nonlocal calls
                            calls += 1
                            if calls > 1:
                                raise KeyboardInterrupt
                            return real_monotonic()

                        with patch.object(photo_derivatives.time, 'monotonic', interrupted):
                            with self.assertRaises(KeyboardInterrupt):
                                self.invoke('photo')
                    else:
                        with self.assertRaises(photo_derivatives.PhotoDecodeError):
                            self.invoke('photo')
                self.assert_reaped()

    def test_native_book_deadline_and_both_output_limits_reap_child(self) -> None:
        real_run = subprocess.run

        def short_deadline(*args, **kwargs):
            self.assertEqual(kwargs['timeout'], 30)
            kwargs['timeout'] = 0.05
            return real_run(*args, **kwargs)

        with self.substitute('import time; time.sleep(60)'), patch.object(subprocess, 'run', short_deadline):
            with self.assertRaises(BookArchiveError):
                self.invoke('book')
        self.assert_reaped()
        for parser, limit, error in (
            ('photo', photo_derivatives.CHILD_MESSAGE_BYTES, photo_derivatives.PhotoDecodeError),
            ('book', book_native.MAX_IMAGE_BYTES, BookArchiveError),
        ):
            program = f'import sys; sys.stdout.write("x" * {limit + 1})'
            with self.subTest(parser=parser), self.substitute(program), self.assertRaises(error):
                self.invoke(parser)
            self.assert_reaped()

    def test_unavailable_selected_sandbox_fails_before_worker_without_fallback(self) -> None:
        # Apply a real outer denial before launching the selected helper. This
        # simulates an unavailable Landlock kernel or Docker's namespace filter.
        def unavailable(fd: int) -> None:
            library = ctypes.CDLL('libseccomp.so.2')
            library.seccomp_init.argtypes = [ctypes.c_uint32]
            library.seccomp_init.restype = ctypes.c_void_p
            library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
            library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
            library.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
            library.seccomp_release.argtypes = [ctypes.c_void_p]
            context = library.seccomp_init(0x7fff0000)
            try:
                denied = ('clone', 'unshare') if parser_boundary.settings.parser_sandbox == 'namespace' else ('landlock_create_ruleset',)
                for name in denied:
                    number = library.seccomp_syscall_resolve_name(name.encode())
                    self.assertNotEqual(number, -1)
                    self.assertEqual(library.seccomp_rule_add(context, 0x00050000 | 38, number, 0), 0)
                self.assertEqual(library.seccomp_export_bpf(context, fd), 0)
            finally:
                library.seccomp_release(context)

        marker = self.target / 'worker-started'
        program = f'from pathlib import Path; Path({str(marker)!r}).write_text("started")'
        with tempfile.NamedTemporaryFile(prefix='unavailable-parser-', suffix='.bpf') as policy:
            unavailable(policy.fileno())
            prefix = ('/usr/bin/setpriv', '--no-new-privs', '--seccomp-filter', policy.name, '--')
            for parser, error in (('photo', photo_derivatives.PhotoDecodeError), ('book', BookArchiveError)):
                with self.subTest(parser=parser, failure='policy'), self.substitute(program, prefix=prefix), self.assertRaises(error):
                    self.invoke(parser)
                with self.subTest(parser=parser, failure='missing helper'):
                    with patch.object(subprocess, 'Popen', side_effect=FileNotFoundError('missing parser helper')) as launch:
                        with self.assertRaises(error):
                            self.invoke(parser)
                    self.assertEqual(launch.call_count, 1, 'An unavailable helper must not trigger a fallback launch')
        self.assertFalse(marker.exists())
        self.assert_reaped()

    @unittest.skipUnless(__import__('platform').machine() == 'x86_64', 'x32 applies to amd64 only')
    def test_x32_abi_is_rejected_by_libseccomp(self) -> None:
        # The filter rejects x32 before kernel dispatch, even if x32 is enabled.
        program = 'import ctypes; ctypes.CDLL(None).syscall(0x40000029, 2, 1, 0); print("x32 bypass")'
        with parser_boundary.parser_command([sys.executable, '-c', program], self.source) as command:
            result = subprocess.run(command, env=parser_boundary.parser_environment(), capture_output=True, timeout=5)
        # Bubblewrap propagates a signaled codec as 128 + signal; setpriv execs it directly.
        expected = 159 if parser_boundary.settings.parser_sandbox == 'namespace' else -31  # SIGSYS
        self.assertEqual(result.returncode, expected)
        self.assertEqual(result.stdout, b'')


if __name__ == '__main__':
    unittest.main(verbosity=2)
