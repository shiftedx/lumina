"""Acceptance checks for the backend tier runner's failure boundaries."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import run_backend_tests as runner  # noqa: E402


class BackendRunnerTests(unittest.TestCase):
    def test_result_validation_fails_closed_and_allows_known_skips(self) -> None:
        selected = ["tests/test_example.py::test_one", "tests/test_example.py::test_two"]
        passing = {"nodeid": selected[0], "outcome": "passed", "reason": "", "seconds": 0.0}
        skipped = {"nodeid": selected[1], "outcome": "skipped", "reason": "needs tool", "seconds": 0.0}

        with self.subTest("empty"):
            with self.assertRaisesRegex(runner.RunError, "identity mismatch"):
                runner.validate_cases(selected, [], [])
        with self.subTest("incomplete"):
            with self.assertRaisesRegex(runner.RunError, "identity mismatch"):
                runner.validate_cases(selected, [passing], [])
        with self.subTest("equal count, wrong identity"):
            wrong = {**passing, "nodeid": "tests/test_example.py::test_wrong"}
            with self.assertRaisesRegex(runner.RunError, "identity mismatch"):
                runner.validate_cases(selected, [passing, wrong], [])
        with self.subTest("duplicate identity"):
            with self.assertRaisesRegex(runner.RunError, "identity mismatch"):
                runner.validate_cases(selected, [passing, passing], [])
        for outcome in ("failure", "error"):
            with self.subTest(outcome):
                failed = {**skipped, "outcome": outcome, "reason": ""}
                with self.assertRaisesRegex(runner.RunError, "failures/errors"):
                    runner.validate_cases(selected, [passing, failed], [])
        with self.subTest("unknown skip"):
            with self.assertRaisesRegex(runner.RunError, "unexpected skips"):
                runner.validate_cases(selected, [passing, skipped], [])
        known_skip = [{"nodeid": selected[1], "reason": "needs tool"}]
        runner.validate_cases(selected, [passing, skipped], known_skip)
        # Optional prerequisites may be present, so their documented skips are
        # allowed without being required for a passing host.
        runner.validate_cases(selected, [passing, {**skipped, "outcome": "passed", "reason": ""}], known_skip)

    def test_xunit1_results_preserve_class_and_parameterized_identities(self) -> None:
        selected = [
            "tests/test_example.py::test_free[host.example]",
            "tests/test_example.py::FirstTests::test_shared[one.two]",
            "tests/test_example.py::SecondTests::test_shared[one.two]",
            "tests/test_example.py::Outer::Inner::test_nested",
        ]
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "junit.xml"
            report.write_text(
                '<testsuite>'
                '<testcase classname="tests.test_example" file="tests/test_example.py" '
                'name="test_free[host.example]"/>'
                '<testcase classname="tests.test_example.FirstTests" file="tests/test_example.py" '
                'name="test_shared[one.two]"/>'
                '<testcase classname="tests.test_example.SecondTests" file="tests/test_example.py" '
                'name="test_shared[one.two]"><skipped message="needs tool"/></testcase>'
                '<testcase classname="tests.test_example.Outer.Inner" file="tests/test_example.py" '
                'name="test_nested"/>'
                '</testsuite>'
            )
            cases = runner.junit_cases(report)
        self.assertEqual([case["nodeid"] for case in cases], selected)
        expected = [{"nodeid": selected[2], "reason": "needs tool"}]
        self.assertEqual(runner.validate_cases(selected, cases, expected), ([], expected))
        with self.assertRaisesRegex(runner.RunError, "unexpected skips"):
            runner.validate_cases(selected, cases, [{**expected[0], "reason": "wrong reason"}])

    def test_timeout_stops_only_its_own_detached_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file, survived_file, script, log = root / "pid", root / "survived", root / "ignore_interrupt.py", root / "child.log"
            script.write_text(
                textwrap.dedent(
                    """\
                    import os
                    from pathlib import Path
                    import signal
                    import subprocess
                    import sys
                    import time
                    child = subprocess.Popen([
                        sys.executable, "-c",
                        "import pathlib, signal, sys, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(.2); pathlib.Path(sys.argv[1]).write_text('survived'); time.sleep(60)",
                        sys.argv[2],
                    ])
                    Path(sys.argv[1]).write_text(f"{os.getpid()} {child.pid}")
                    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
                    time.sleep(60)
                    """
                )
            )
            sentinel = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                with mock.patch.object(runner, "GRACE_SECONDS", 0.05):
                    with self.assertRaisesRegex(runner.RunError, "timed out"):
                        runner.child(
                            [sys.executable, str(script), str(pid_file), str(survived_file)],
                            cwd=root,
                            env=os.environ.copy(),
                            log=log,
                            timeout=0.05,
                        )
                self.assertTrue(pid_file.is_file())
                self.assertIsNone(sentinel.poll())
                # The worker ignored SIGINT and its leader exited. A surviving
                # worker would create this marker after the runner's grace period.
                import time
                time.sleep(0.3)
                self.assertFalse(survived_file.exists())
            finally:
                sentinel.terminate()
                try:
                    sentinel.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    sentinel.kill()
                    sentinel.wait()


if __name__ == "__main__":
    unittest.main()
