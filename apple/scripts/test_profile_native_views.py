from __future__ import annotations

import errno
import io
import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "profile_native_views", Path(__file__).with_name("profile_native_views.py")
)
assert SPEC is not None and SPEC.loader is not None
PROFILE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILE)


class ProfileNativeViewsTests(unittest.TestCase):
    def test_main_stops_the_owned_command_and_reports_reader_failure(self) -> None:
        class FullDiskLog:
            def write(self, _value: str) -> int:
                return 0

            def flush(self) -> None:
                raise OSError(errno.ENOSPC, "No space left on device")

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

        process = Mock()
        process.pid = 123
        process.stdout = io.StringIO("test output\n")
        process.poll.return_value = None
        process.returncode = 1
        original_open = Path.open

        def open_for_test(path: Path, *args, **kwargs):
            if path.name == "test.log":
                return FullDiskLog()
            return original_open(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "capture"
            with patch.object(Path, "open", new=open_for_test), \
                 patch.object(PROFILE.ctypes, "CDLL", return_value=Mock()), \
                 patch.object(PROFILE.subprocess, "Popen", return_value=process), \
                 patch.object(PROFILE, "stop_process_group", return_value=[]) as stop, \
                 patch.object(sys, "argv", [
                     "profile_native_views.py", "--simulator", "sim", "--executable", "Lumina",
                     "--output", str(output), "--", "finite-test-command",
                 ]):
                self.assertEqual(PROFILE.main(), 1)

            stop.assert_called_once_with(process)
            report = __import__("json").loads((output / "report.json").read_text())
            self.assertTrue(report["capture"]["reader_error"])
            self.assertIn("No space left", report["capture"]["reader_errors"][0])

    def test_output_reader_records_a_flush_failure(self) -> None:
        class FullDiskLog:
            def write(self, value: str) -> int:
                return len(value)

            def flush(self) -> None:
                raise OSError(errno.ENOSPC, "No space left on device")

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

        markers: list[dict] = []
        marker_lock = threading.Lock()
        error_event = threading.Event()
        errors: list[str] = []
        errors_lock = threading.Lock()
        with patch.object(Path, "open", return_value=FullDiskLog()):
            PROFILE.consume_test_output(
                io.StringIO('LUMINA_PERF {"phase":"home_idle","event":"ready","timestamp":1}\n'),
                Path("test.log"), markers, marker_lock, error_event, errors, errors_lock,
            )

        self.assertTrue(error_event.is_set())
        self.assertEqual(markers, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("OSError", errors[0])
        self.assertIn("No space left", errors[0])

    def test_summarize_rejects_a_window_with_multiple_processes(self) -> None:
        markers = [
            {"phase": "home_idle", "event": "begin", "timestamp": 10.0},
            {"phase": "home_idle", "event": "ready", "timestamp": 11.0},
            {"phase": "home_idle", "event": "end", "timestamp": 13.0},
        ]
        samples = [
            {"timestamp": 11.0, "pid": 41, "cpu_ns": 1, "wakeups": 1,
             "footprint": 1, "resident": 1, "load1": 1},
            {"timestamp": 12.0, "pid": 42, "cpu_ns": 2, "wakeups": 2,
             "footprint": 2, "resident": 2, "load1": 2},
            {"timestamp": 13.0, "pid": 41, "cpu_ns": 3, "wakeups": 3,
             "footprint": 3, "resident": 3, "load1": 3},
        ]

        [window] = PROFILE.summarize(samples, markers)

        self.assertEqual(window["sample_count"], 3)
        self.assertNotIn("seconds", window)
        self.assertNotIn("cpu_one_core_percent", window)

    def test_stop_process_group_escalates_to_bounded_sigkill(self) -> None:
        process = Mock()
        process.pid = 123
        process.poll.side_effect = [None, None, None, 0]
        process.wait.side_effect = [
            subprocess.TimeoutExpired("test", 1),
            subprocess.TimeoutExpired("test", 1),
            None,
        ]

        with patch.object(PROFILE.os, "killpg") as killpg:
            failures = PROFILE.stop_process_group(process, grace_seconds=1, kill_seconds=1)

        self.assertEqual(failures, [])
        self.assertEqual(
            [call.args[1] for call in killpg.call_args_list],
            [signal.SIGINT, signal.SIGTERM, signal.SIGKILL],
        )
        self.assertEqual(process.wait.call_count, 3)


if __name__ == "__main__":
    unittest.main()
