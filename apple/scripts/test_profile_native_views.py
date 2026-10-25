from __future__ import annotations

import errno
import ctypes
from datetime import datetime, timezone
import os
import time
import io
import importlib.util
import json
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
    def write_diagnostic_report(self, directory: Path, name: str, body: dict) -> None:
        (directory / name).write_text(json.dumps({"bug_type": "synthetic"}) + "\n" + json.dumps(body))

    def test_finds_own_simulator_system_crash_during_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reports = Path(temporary)
            self.write_diagnostic_report(reports, "system.ips", {
                "coalitionName": "com.apple.CoreSimulator.SimDevice.SIM-OWN",
                "procName": "PosterBoard",
                "captureTime": "2026-10-24 22:04:19.8434 -0500",
                "exception": {"type": "EXC_BREAKPOINT", "signal": "SIGTRAP"},
            })
            incident = datetime.strptime(
                "2026-10-24 22:04:19.8434 -0500", "%Y-%m-%d %H:%M:%S.%f %z",
            ).timestamp()

            crashes = PROFILE.find_simulator_system_crashes(
                reports, "SIM-OWN", incident - 1, incident + 1,
            )

        self.assertEqual(crashes, [{
            "process": "PosterBoard",
            "incident_time": "2026-10-24 22:04:19.8434 -0500",
            "exception": "EXC_BREAKPOINT",
            "signal": "SIGTRAP",
        }])

    def test_ignores_other_simulators_old_incidents_and_incomplete_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reports = Path(temporary)
            self.write_diagnostic_report(reports, "wrong-simulator.ips", {
                "coalitionName": "com.apple.CoreSimulator.SimDevice.SIM-OTHER",
                "procName": "SpringBoard",
                "captureTime": "2026-10-24 22:04:19.8434 -0500",
            })
            self.write_diagnostic_report(reports, "old.ips", {
                "coalitionName": "com.apple.CoreSimulator.SimDevice.SIM-OWN",
                "procName": "PosterBoard",
                "captureTime": "2026-10-24 21:04:19.8434 -0500",
            })
            self.write_diagnostic_report(reports, "incomplete.ips", {"procName": "PosterBoard"})
            (reports / "malformed.ips").write_text("metadata only")
            incident = datetime.strptime(
                "2026-10-24 22:04:19.8434 -0500", "%Y-%m-%d %H:%M:%S.%f %z",
            ).timestamp()
            os.utime(reports / "old.ips", (incident, incident))

            crashes = PROFILE.find_simulator_system_crashes(
                reports, "SIM-OWN", incident - 1, incident + 1,
            )

        self.assertEqual(crashes, [])

    def test_green_xctest_with_system_crash_is_an_invalid_capture(self) -> None:
        class ImmediateThread:
            def __init__(self, *, target, args, daemon):
                self.target = target
                self.args = args

            def start(self) -> None:
                self.target(*self.args)

            def join(self, timeout=None) -> None:
                return None

            def is_alive(self) -> bool:
                return False

        process = Mock()
        process.pid = 123
        process.stdout = io.StringIO(
            'LUMINA_PERF {"phase":"login_form","event":"ready","timestamp":0}\n'
            'LUMINA_PERF {"phase":"home_idle","event":"begin","timestamp":0}\n'
            'LUMINA_PERF {"phase":"home_idle","event":"ready","timestamp":0}\n'
            'LUMINA_PERF {"phase":"home_idle","event":"end","timestamp":9999999999}\n'
        )
        process.poll.side_effect = [None, None, None, 0, 0]
        process.returncode = 0
        lib = Mock()

        def process_path(_pid, path, _size):
            path.value = b"/sim/SIM-OWN/Lumina.app/Lumina"
            return len(path.value)

        def process_usage(_pid, _flavor, usage_pointer):
            usage_pointer._obj.start = 1
            return 0

        lib.proc_pidpath.side_effect = process_path
        lib.proc_pid_rusage.side_effect = process_usage
        capture_times = iter([1000.0, 1001.0, 1002.0, 1003.0, 1004.0])

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            reports = home / "Library" / "Logs" / "DiagnosticReports"
            reports.mkdir(parents=True)
            self.write_diagnostic_report(reports, "system.ips", {
                "coalitionName": "com.apple.CoreSimulator.SimDevice.SIM-OWN",
                "procName": "SpringBoard",
                "captureTime": datetime.fromtimestamp(1002, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f %z"),
                "exception": {"type": "EXC_CRASH", "signal": "SIGABRT"},
            })
            output = home / "capture"
            with patch.object(PROFILE.ctypes, "CDLL", return_value=lib), \
                 patch.object(PROFILE, "process_ids", return_value=[321]), \
                 patch.object(PROFILE.subprocess, "Popen", return_value=process), \
                 patch.object(PROFILE.threading, "Thread", ImmediateThread), \
                 patch.object(PROFILE.time, "sleep"), \
                 patch.object(PROFILE.time, "time", side_effect=capture_times), \
                 patch.object(Path, "home", return_value=home), \
                 patch.object(sys, "argv", [
                     "profile_native_views.py", "--simulator", "SIM-OWN", "--executable", "Lumina",
                     "--output", str(output), "--", "finite-test-command",
                 ]):
                exit_code = PROFILE.main()

            report = json.loads((output / "report.json").read_text())

        self.assertEqual(exit_code, 1)
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(any(window.get("sample_count") == 3 for window in report["windows"]))
        self.assertFalse(report["capture"]["valid"])
        self.assertEqual(report["capture"]["started_at"], 1000.0)
        self.assertEqual(report["capture"]["ended_at"], 1004.0)
        self.assertEqual(report["capture"]["system_crashes"][0]["process"], "SpringBoard")
        self.assertEqual(len(report["capture"]["failures"]), 1)
        self.assertIn("SpringBoard", report["capture"]["failures"][0])

    @unittest.skipUnless(sys.platform == "darwin", "macOS process sampler")
    def test_cpu_counter_matches_process_cpu_clock(self) -> None:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        base = PROFILE.MachTimebase(1, 1)
        ctypes.CDLL("/usr/lib/libSystem.B.dylib").mach_timebase_info(ctypes.byref(base))
        before, after = PROFILE.Usage(), PROFILE.Usage()
        lib.proc_pid_rusage(os.getpid(), 2, ctypes.byref(before))
        started = time.process_time_ns()
        until = time.monotonic() + 0.04
        while time.monotonic() < until:
            pass
        elapsed = time.process_time_ns() - started
        lib.proc_pid_rusage(os.getpid(), 2, ctypes.byref(after))
        measured = PROFILE.cpu_nanoseconds(after, base) - PROFILE.cpu_nanoseconds(before, base)
        self.assertAlmostEqual(measured / elapsed, 1, delta=0.1)

    def test_pid_scan_includes_processes_beyond_the_first_quarter(self) -> None:
        lib = Mock()

        def list_pids(buffer, _bytes):
            if buffer is None:
                return 1000
            for index in range(1000):
                buffer[index] = 100 + index
            return 1000

        lib.proc_listallpids.side_effect = list_pids
        self.assertEqual(PROFILE.process_ids(lib), list(range(100, 1100)))

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
                 patch.object(PROFILE, "find_simulator_system_crashes", return_value=[]), \
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
