"""Linux release-image qualification; also runnable with the runtime's stdlib unittest."""
from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import book_native, parser_boundary, photo_derivatives
from app.services.book_archives import BookArchiveError


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

    def substitute(self, program: str):
        real_popen = subprocess.Popen

        def launch(argv, **kwargs):
            # Keep the real setpriv policy and supervision; replace only the codec.
            offset = argv.index('--') + 1 if '--' in argv else 0
            process = real_popen([*argv[:offset], sys.executable, '-c', program], **kwargs)
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
import json,os,socket
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
    except PermissionError:
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

    def test_unavailable_landlock_fails_before_worker_without_fallback(self) -> None:
        # Export a real additional denial of the Landlock creation syscall. This
        # simulates ENOSYS from an older/disabled kernel without changing the host.
        original_export = parser_boundary._export_socket_filter

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
                number = library.seccomp_syscall_resolve_name(b'landlock_create_ruleset')
                self.assertEqual(library.seccomp_rule_add(context, 0x00050000 | 38, number, 0), 0)
                self.assertEqual(library.seccomp_export_bpf(context, fd), 0)
            finally:
                library.seccomp_release(context)

        marker = self.target / 'worker-started'
        program = f'from pathlib import Path; Path({str(marker)!r}).write_text("started")'
        with patch.object(parser_boundary, '_export_socket_filter', unavailable):
            for parser, error in (('photo', photo_derivatives.PhotoDecodeError), ('book', BookArchiveError)):
                with self.subTest(parser=parser), self.substitute(program), self.assertRaises(error):
                    self.invoke(parser)
        self.assertFalse(marker.exists())
        self.assert_reaped()
        self.assertIs(parser_boundary._export_socket_filter, original_export)

    @unittest.skipUnless(__import__('platform').machine() == 'x86_64', 'x32 applies to amd64 only')
    def test_x32_abi_is_rejected_by_libseccomp(self) -> None:
        # The filter rejects x32 before kernel dispatch, even if x32 is enabled.
        program = 'import ctypes; ctypes.CDLL(None).syscall(0x40000029, 2, 1, 0); print("x32 bypass")'
        with parser_boundary.parser_command([sys.executable, '-c', program], self.source) as command:
            result = subprocess.run(command, env=parser_boundary.parser_environment(), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, -31)  # SIGSYS
        self.assertEqual(result.stdout, b'')


if __name__ == '__main__':
    unittest.main(verbosity=2)
