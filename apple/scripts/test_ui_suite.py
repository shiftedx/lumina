#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent))
from ui_suite import FixtureBroker, serve
import run_ui_tiers


class FixtureBrokerTests(unittest.TestCase):
    def test_concurrent_classes_receive_distinct_private_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def command(key: str, fixture: Path) -> list[str]:
                source = (
                    "import json,os,time; "
                    "open(os.environ['LUMINA_NATIVE_FIXTURE'],'w').write(json.dumps({'serverURL':'http://127.0.0.1:'+os.environ['LUMINA_NATIVE_PORT'],'class':'" + key + "'})); "
                    "time.sleep(60)"
                )
                return [sys.executable, "-c", source]
            broker = FixtureBroker(root, sys.executable, command)
            try:
                def request(key: str) -> dict[str, str]:
                    return json.loads(broker.fixture(key))
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    one, two = pool.map(request, ["ClassOne", "ClassTwo"])
                self.assertNotEqual(one["class"], two["class"])
                self.assertEqual(set(broker._fixtures), {"ClassOne", "ClassTwo"})
            finally:
                broker.close()
            self.assertFalse(any(root.iterdir()))

    def test_http_get_and_delete_isolate_two_actual_ports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "broker.json"
            stop = threading.Event()
            def command(key: str, fixture: Path) -> list[str]:
                source = (
                    "import json,os,socket,time; s=socket.socket(); s.bind(('127.0.0.1',0)); "
                    "port=s.getsockname()[1]; open(os.environ['LUMINA_NATIVE_FIXTURE'],'w').write(json.dumps({'serverURL':'http://127.0.0.1:'+str(port),'class':'" + key + "'})); time.sleep(60)"
                )
                return [sys.executable, "-c", source]
            thread = threading.Thread(target=serve, args=(config, sys.executable, command, stop), daemon=True)
            thread.start()
            deadline = time.monotonic() + 5
            while not config.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(config.exists())
            broker = json.loads(config.read_text(encoding="utf-8"))["brokerURL"]
            def get(key: str) -> dict[str, str]:
                with urlopen(f"{broker}/{key}", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    return json.loads(response.read())
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                one, two = pool.map(get, ["ClassOne", "ClassTwo"])
            self.assertNotEqual(one["serverURL"], two["serverURL"])
            request = Request(f"{broker}/ClassOne", method="DELETE")
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"{}")
            stop.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())


class UITierRunnerTests(unittest.TestCase):
    def test_cancelled_run_never_launches_xcode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "run.log"
            with mock.patch.object(run_ui_tiers, "CANCELLED") as cancelled, \
                 mock.patch.object(run_ui_tiers.subprocess, "Popen") as popen:
                cancelled.is_set.return_value = True
                with self.assertRaisesRegex(RuntimeError, "run cancelled"):
                    run_ui_tiers.run_xcode(["xcodebuild", "test"], log)
                popen.assert_not_called()

    def test_platforms_use_distinct_broker_configs_and_parallel_wall_time(self) -> None:
        output = Path("/private/tmp/lumina-run")
        self.assertEqual(
            {run_ui_tiers.broker_config_path(output, platform) for platform in ["iOS", "tvOS"]},
            {output / "iOS-broker.json", output / "tvOS-broker.json"},
        )
        results = {
            "iOS": {"buildExit": 0, "setupStartedMonotonic": 0, "setupFinishedMonotonic": 40,
                      "tests": [{"startedMonotonic": 50, "finishedMonotonic": 170}, {"startedMonotonic": 10, "finishedMonotonic": 20}]},
            "tvOS": {"buildExit": 0, "setupStartedMonotonic": 5, "setupFinishedMonotonic": 35,
                       "tests": [{"startedMonotonic": 55, "finishedMonotonic": 155}, {"startedMonotonic": 15, "finishedMonotonic": 25}]},
        }
        self.assertEqual(run_ui_tiers.per_repeat_walls(results, ["iOS", "tvOS"], 2), [160, 15])
        self.assertEqual(run_ui_tiers.per_repeat_walls({"iOS": results["iOS"]}, ["iOS", "tvOS"], 2), [])
        self.assertEqual(run_ui_tiers.per_repeat_walls({"iOS": {**results["iOS"], "tests": []}, "tvOS": results["tvOS"]}, ["iOS", "tvOS"], 2), [])

    def test_aborted_cohort_releases_waiting_platform(self) -> None:
        cohort = threading.Barrier(2)
        cohort.abort()
        with self.assertRaises(threading.BrokenBarrierError):
            run_ui_tiers.wait_for_cohort(cohort, 1)

    def test_shards_partition_every_selected_test_exactly_once(self) -> None:
        for platform in ["iOS", "tvOS"]:
            for tier in ["smoke", "full"]:
                selected = run_ui_tiers.selectors(platform, tier)
                shards = run_ui_tiers.split_shards(selected)
                self.assertEqual(len(shards), 2)
                self.assertTrue(all(shard for shard in shards))
                self.assertEqual([item for shard in shards for item in shard], selected[::2] + selected[1::2])
                self.assertEqual(sorted(item for shard in shards for item in shard), sorted(selected))
                self.assertEqual(len({item for shard in shards for item in shard}), len(selected))

    def test_result_requires_the_exact_selected_count_and_a_clean_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "run.log"
            result = Path(directory) / "run.xcresult"
            log.write_text("** TEST SUCCEEDED **\n", encoding="utf-8")
            def accepted(**changes: object) -> dict[str, object]:
                summary: dict[str, object] = {
                    "result": "Passed", "totalTestCount": 2, "passedTests": 2,
                    "failedTests": 0, "skippedTests": 0, "expectedFailures": 0,
                }
                summary.update(changes)
                return summary
            cases = [
                (accepted(), True),
                (accepted(totalTestCount=0, passedTests=0), False),
                (accepted(totalTestCount=1, passedTests=1), False),
                (accepted(skippedTests=1, passedTests=1), False),
                (accepted(result="Failed", failedTests=1, passedTests=1), False),
                (accepted(result="unknown"), False),
            ]
            for summary, expected in cases:
                response = subprocess.CompletedProcess([], 0, json.dumps(summary), "")
                with mock.patch.object(run_ui_tiers, "command", return_value=response):
                    self.assertIs(run_ui_tiers.result_ok(result, log, 2), expected)
            log.write_text("** TEST EXECUTE SUCCEEDED **\n", encoding="utf-8")
            response = subprocess.CompletedProcess([], 0, json.dumps(accepted()), "")
            with mock.patch.object(run_ui_tiers, "command", return_value=response):
                self.assertTrue(run_ui_tiers.result_ok(result, log, 2))

    def test_runtime_selection_requires_matching_available_platform_and_device(self) -> None:
        devices = {
            "devicetypes": [
                {"name": "iPad Pro 11-inch (M5)", "identifier": "ipad"},
                {"name": "Apple TV 4K (3rd generation)", "identifier": "tv"},
            ]
        }
        runtimes = {
            "runtimes": [
                {"identifier": "com.apple.CoreSimulator.SimRuntime.iOS-27-0", "version": "27.0", "isAvailable": True, "supportedDeviceTypes": [{"identifier": "ipad"}]},
                {"identifier": "com.apple.CoreSimulator.SimRuntime.iOS-27-1", "version": "27.1", "isAvailable": True, "supportedDeviceTypes": [{"identifier": "ipad"}]},
                {"identifier": "com.apple.CoreSimulator.SimRuntime.iOS-28-0", "version": "28.0", "isAvailable": False, "supportedDeviceTypes": [{"identifier": "ipad"}]},
                {"identifier": "com.apple.CoreSimulator.SimRuntime.tvOS-27-0", "version": "27.0", "isAvailable": True, "supportedDeviceTypes": [{"identifier": "tv"}]},
                {"identifier": "com.apple.CoreSimulator.SimRuntime.tvOS-27-1", "version": "27.1", "isAvailable": True, "supportedDeviceTypes": [{"identifier": "ipad"}]},
            ]
        }
        def fake(*parts: str, **_: object) -> subprocess.CompletedProcess[str]:
            payload = runtimes if "runtimes" in parts else devices
            return subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with mock.patch.object(run_ui_tiers, "command", side_effect=fake):
            self.assertEqual(run_ui_tiers.latest_runtime("iOS"), "com.apple.CoreSimulator.SimRuntime.iOS-27-1")
            self.assertEqual(run_ui_tiers.latest_runtime("tvOS"), "com.apple.CoreSimulator.SimRuntime.tvOS-27-0")
            with mock.patch.object(run_ui_tiers, "IOS_RUNTIME", "27.0"):
                self.assertEqual(run_ui_tiers.latest_runtime("iOS"), "com.apple.CoreSimulator.SimRuntime.iOS-27-0")
            with mock.patch.object(run_ui_tiers, "IOS_RUNTIME", "29.0"):
                with self.assertRaisesRegex(RuntimeError, "no compatible available"):
                    run_ui_tiers.latest_runtime("iOS")

    def test_cleanup_deletes_only_its_named_simulators_and_clones(self) -> None:
        run_id = "recorded-run"
        payload = {"devices": {"runtime": [
            {"name": "Lumina UI tier recorded-run iOS", "udid": "own"},
            {"name": "Lumina UI tier recorded-run iOS Clone 1", "udid": "clone"},
            {"name": "Clone 2 of Lumina UI tier recorded-run iOS", "udid": "clone-of"},
            {"name": "Lumina UI tier recorded-runner iOS", "udid": "nearby"},
            {"name": "A different developer's simulator", "udid": "other"},
        ]}}
        calls: list[tuple[str, ...]] = []
        def fake(*parts: str, **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(parts)
            output = json.dumps(payload) if "list" in parts else ""
            return subprocess.CompletedProcess([], 0, output, "")
        with mock.patch.object(run_ui_tiers, "command", side_effect=fake):
            run_ui_tiers.delete_own_simulators(run_id)
        affected = {call[-1] for call in calls if len(call) > 3 and call[2] in {"shutdown", "delete"}}
        self.assertEqual(affected, {"own", "clone", "clone-of"})


if __name__ == "__main__":
    unittest.main()
