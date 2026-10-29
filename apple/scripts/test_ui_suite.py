#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
from io import StringIO
import json
import os
import plistlib
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
from ui_suite import FixtureBroker, fixture_test_class, proof_server_environment, serve
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

    def test_deleting_one_same_class_instance_fixture_keeps_its_sibling_alive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def command(key: str, fixture: Path) -> list[str]:
                source = (
                    "import json,os; from http.server import BaseHTTPRequestHandler,HTTPServer; "
                    "H=type('H',(BaseHTTPRequestHandler,),{'do_GET':lambda s:(s.send_response(200),s.end_headers(),s.wfile.write(b'ok')),'log_message':lambda *a:None}); "
                    "server=HTTPServer(('127.0.0.1',0),H); port=server.server_port; "
                    "open(os.environ['LUMINA_NATIVE_FIXTURE'],'w').write(json.dumps({'serverURL':'http://127.0.0.1:'+str(port),'key':'" + key + "'})); server.serve_forever()"
                )
                return [sys.executable, "-c", source]
            broker = FixtureBroker(root, sys.executable, command)
            first = "NativeAudioAlbumTrackPlaybackReportsProgressTests-instance-one"
            second = "NativeAudioAlbumTrackPlaybackReportsProgressTests-instance-two"
            try:
                one = json.loads(broker.fixture(first))
                two = json.loads(broker.fixture(second))
                self.assertNotEqual(one["serverURL"], two["serverURL"])
                broker.stop(first)
                self.assertEqual(json.loads(broker.fixture(second))["serverURL"], two["serverURL"])
                with urlopen(two["serverURL"], timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b"ok")
                self.assertEqual(
                    proof_server_environment(fixture_test_class("NativeAudioAlbumPlayAfterUnavailableRestoreTests-instance")),
                    {"LUMINA_NATIVE_AUDIO_RESTORE_FAILURE": "1"},
                )
                self.assertEqual(
                    proof_server_environment(fixture_test_class("NativeVideoFormatRecoveryTests-instance")),
                    {"LUMINA_NATIVE_VIDEO_FORMAT_FAILURE": "1"},
                )
                for test_class in ("NativeLibraryCatalogScrollingTests", "NativeTVLibraryAlphabetTests"):
                    self.assertEqual(
                        proof_server_environment(fixture_test_class(f"{test_class}-instance")),
                        {"LUMINA_NATIVE_PERF_CATALOGUE_COUNT": "120"},
                    )
            finally:
                broker.close()


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
        self.assertEqual(run_ui_tiers.phased_repeat_walls(results, [["iOS"], ["tvOS"]], 2), [290, 20])
        self.assertEqual(run_ui_tiers.per_repeat_walls({"iOS": results["iOS"]}, ["iOS", "tvOS"], 2), [])
        self.assertEqual(run_ui_tiers.per_repeat_walls({"iOS": {**results["iOS"], "tests": []}, "tvOS": results["tvOS"]}, ["iOS", "tvOS"], 2), [])
        phases = run_ui_tiers.qualification_phases([
            {"label": "iPhone"}, {"label": "iPad"}, {"label": "Duo"}, {"label": "tvOS"},
        ])
        self.assertEqual([[item["label"] for item in phase] for phase in phases], [["iPad", "tvOS"], ["iPhone", "Duo"]])
        constrained = run_ui_tiers.qualification_phases([
            {"label": "iPhone"}, {"label": "iPad"}, {"label": "Duo"}, {"label": "tvOS"},
        ], max_active_shards=2)
        self.assertEqual(
            [[item["label"] for item in phase] for phase in constrained],
            [["iPad"], ["tvOS"], ["iPhone"], ["Duo"]],
        )
        self.assertEqual(run_ui_tiers.effective_budget("full", 4), 5400)
        self.assertEqual(run_ui_tiers.effective_budget("full", 2), 10800)
        self.assertEqual(run_ui_tiers.effective_budget("smoke", 4), 600)
        self.assertEqual(run_ui_tiers.effective_budget("smoke", 2), 600)
        self.assertTrue(run_ui_tiers.iPad_build_ready({"iPad": {"buildExit": 0}}))
        self.assertFalse(run_ui_tiers.iPad_build_ready({"iPad": {"buildExit": 1}}))

    def test_repeat_qualification_rejects_cancelled_later_repeats(self) -> None:
        results = {"iOS": {"buildExit": 0, "tests": [{"passed": True}]}}
        report: dict[str, object] = {
            "phaseGateError": None,
            "coverageComplete": True,
            "withinBudget": True,
            "repeatCoverageComplete": True,
            "repeatQualificationFailed": False,
            "runCancelled": False,
        }
        self.assertTrue(run_ui_tiers.qualification_passed(report, results, ["iOS"]))
        report["repeatCoverageComplete"] = False
        self.assertFalse(run_ui_tiers.qualification_passed(report, results, ["iOS"]))
        report["repeatCoverageComplete"] = True
        report["repeatQualificationFailed"] = True
        self.assertFalse(run_ui_tiers.qualification_passed(report, results, ["iOS"]))
        report["repeatQualificationFailed"] = False
        report["runCancelled"] = True
        self.assertFalse(run_ui_tiers.qualification_passed(report, results, ["iOS"]))
        report["runCancelled"] = False
        report["withinBudget"] = False
        self.assertFalse(run_ui_tiers.qualification_passed(report, results, ["iOS"]))

    def test_cap_two_reports_the_scaled_full_orchestration_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            class FinishedBroker:
                def poll(self) -> int:
                    return 0

            outcome = {
                "buildExit": 0,
                "setupStartedMonotonic": 0.0,
                "setupFinishedMonotonic": 1.0,
                "tests": [{"passed": True, "startedMonotonic": 1.0, "finishedMonotonic": 2.0}],
            }
            arguments = [
                "run_ui_tiers.py", "--tier", "full", "--platform", "tvOS", "--repeat", "1",
                "--max-active-shards", "2", "--output", str(root),
            ]
            with mock.patch.object(sys, "argv", arguments), \
                 mock.patch.object(run_ui_tiers, "start_broker", return_value=(FinishedBroker(), root / "broker.json")), \
                 mock.patch.object(run_ui_tiers, "one_platform", return_value=outcome), \
                 mock.patch.object(run_ui_tiers, "combined_full_coverage", return_value=True), \
                 mock.patch.object(run_ui_tiers, "delete_own_simulators"), \
                 mock.patch.object(run_ui_tiers, "write_host_crash_snapshot"), \
                 mock.patch.object(run_ui_tiers.signal, "signal"), \
                 mock.patch("sys.stdout", new_callable=StringIO):
                run_ui_tiers.main()

            report = json.loads((root / "full-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["baseBudgetSeconds"], 5400)
        self.assertEqual(report["budgetSeconds"], 10800)
        self.assertEqual(report["orchestrationBudgetScale"], 2)
        self.assertEqual(report["maxActiveShards"], 2)
        self.assertEqual(report["shardTimeoutSeconds"], 3000)
        self.assertTrue(report["withinBudget"])

    def test_shard_timeout_default_override_and_invalid_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            class FinishedBroker:
                def poll(self) -> int:
                    return 0

            outcome = {
                "buildExit": 0,
                "setupStartedMonotonic": 0.0,
                "setupFinishedMonotonic": 1.0,
                "tests": [{"passed": True, "startedMonotonic": 1.0, "finishedMonotonic": 2.0}],
            }
            arguments = [
                "run_ui_tiers.py", "--tier", "smoke", "--platform", "tvOS", "--repeat", "1",
                "--shard-timeout-seconds", "5400", "--output", str(root),
            ]
            with mock.patch.object(sys, "argv", arguments), \
                 mock.patch.object(run_ui_tiers, "start_broker", return_value=(FinishedBroker(), root / "broker.json")), \
                 mock.patch.object(run_ui_tiers, "one_platform", return_value=outcome), \
                 mock.patch.object(run_ui_tiers, "delete_own_simulators"), \
                 mock.patch.object(run_ui_tiers, "write_host_crash_snapshot"), \
                 mock.patch.object(run_ui_tiers.signal, "signal"), \
                 mock.patch("sys.stdout", new_callable=StringIO):
                run_ui_tiers.main()

            report = json.loads((root / "smoke-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["shardTimeoutSeconds"], 5400)

        with mock.patch.object(sys, "argv", [
            "run_ui_tiers.py", "--tier", "smoke", "--output", "/private/tmp/unused",
            "--shard-timeout-seconds", "0",
        ]), self.assertRaises(SystemExit):
            run_ui_tiers.main()

    def test_shard_timeout_only_overrides_test_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broker = root / "broker.json"
            broker.write_text("{}", encoding="utf-8")
            calls: list[tuple[list[str], int]] = []

            def run_xcode(arguments: list[str], _: Path, timeout_seconds: int = 3000) -> tuple[int, float, float, float]:
                calls.append((arguments, timeout_seconds))
                return 0, 1.0, 1.0, 2.0

            common = {
                "boot_simulators": mock.patch.object(run_ui_tiers, "boot_simulators", return_value=(["one"], 0.0, 0.0)),
                "latest_runtime": mock.patch.object(run_ui_tiers, "latest_runtime", return_value="runtime"),
                "xctestrun_path": mock.patch.object(
                    run_ui_tiers, "xctestrun_path", side_effect=[RuntimeError("missing"), root / "tests.xctestrun"]
                ),
                "refresh_broker_fixture": mock.patch.object(run_ui_tiers, "refresh_broker_fixture"),
                "enumerate_test_identifiers": mock.patch.object(run_ui_tiers, "enumerate_test_identifiers", return_value=["test"]),
                "selected_test_identifiers": mock.patch.object(run_ui_tiers, "selected_test_identifiers", return_value=["test"]),
                "selectors": mock.patch.object(run_ui_tiers, "selectors", return_value=["-only-testing:test"]),
                "split_shards": mock.patch.object(run_ui_tiers, "split_shards", return_value=[["-only-testing:test"]]),
                "expected_case_count": mock.patch.object(run_ui_tiers, "expected_case_count", return_value=1),
                "write_coverage_manifest": mock.patch.object(run_ui_tiers, "write_coverage_manifest"),
                "write_simulator_crash_snapshot": mock.patch.object(run_ui_tiers, "write_simulator_crash_snapshot"),
                "command": mock.patch.object(run_ui_tiers, "command", return_value=subprocess.CompletedProcess([], 0, "", "")),
            }
            with common["boot_simulators"], common["latest_runtime"], common["xctestrun_path"], \
                 common["refresh_broker_fixture"], common["enumerate_test_identifiers"], common["selected_test_identifiers"], \
                 common["selectors"], common["split_shards"], common["expected_case_count"], \
                 common["write_coverage_manifest"], common["write_simulator_crash_snapshot"], common["command"], \
                 mock.patch.object(run_ui_tiers, "run_xcode", side_effect=run_xcode), \
                 mock.patch.object(run_ui_tiers, "result_ok", return_value=True):
                outcome = run_ui_tiers.one_platform(
                    "iOS", "smoke", 1, root, broker, "run", shard_timeout_seconds=5400
                )

        self.assertEqual(outcome["buildExit"], 0)
        self.assertTrue(outcome["tests"][0]["passed"])
        self.assertEqual([timeout for _, timeout in calls], [3000, 5400])
        self.assertIn("build-for-testing", calls[0][0])
        self.assertIn("test-without-building", calls[1][0])

    def test_both_owned_simulators_boot_concurrently(self) -> None:
        ready = threading.Barrier(2)
        def boot(*parts: str, **_: object) -> subprocess.CompletedProcess[str]:
            ready.wait(timeout=2)
            return subprocess.CompletedProcess(parts, 0, "", "")
        with mock.patch.object(run_ui_tiers, "create_simulator", side_effect=lambda platform, run_id, number, *_: f"own-{number}"), \
             mock.patch.object(run_ui_tiers, "command", side_effect=boot), \
             mock.patch.object(run_ui_tiers.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            simulators, seconds, waited = run_ui_tiers.boot_simulators("iOS", "run")
        self.assertEqual(simulators, ["own-1", "own-2"])
        self.assertGreaterEqual(seconds, 0)
        self.assertEqual(waited, 0)

    def test_prepared_ios_device_requires_closed_viewport_and_is_never_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readiness = Path(directory) / "duo-readiness.json"
            screenshot = Path(directory) / "closed-duo.png"
            screenshot.write_bytes(b"capture")
            readiness.write_text(json.dumps({
                "simulatorUDID": "prepared-duo", "posture": "closed",
                "appViewport": {"width": 466, "height": 678},
                "screenshotPath": str(screenshot),
            }), encoding="utf-8")
            booted = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch.object(run_ui_tiers, "create_simulator") as create, \
                 mock.patch.object(run_ui_tiers.subprocess, "run", return_value=booted):
                simulators, _, _ = run_ui_tiers.boot_simulators(
                    "iOS", "run", "prepared-duo", readiness
                )
            self.assertEqual(simulators, ["prepared-duo"])
            create.assert_not_called()

            readiness.write_text(json.dumps({
                "simulatorUDID": "prepared-duo", "posture": "open",
                "appViewport": {"width": 466, "height": 678},
                "screenshotPath": str(screenshot),
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "closed Duo"):
                run_ui_tiers.validated_prepared_ios_device("prepared-duo", readiness)

    def test_aborted_cohort_releases_waiting_platform(self) -> None:
        cohort = threading.Barrier(2)
        cohort.abort()
        with self.assertRaises(threading.BrokenBarrierError):
            run_ui_tiers.wait_for_cohort(cohort, 1)

    def test_shards_partition_every_selected_test_exactly_once(self) -> None:
        discovered = [
            "Lumina-iOS-UITests/NativeAudioAlbumTrackPlaybackReportsProgressTests/testAlbumPlayStartsFromIdle",
            "Lumina-iOS-UITests/NativeAudioAlbumTrackPlaybackReportsProgressTests/testAudioAlbumTrackPlaybackReportsProgress",
            "Lumina-iOS-UITests/NativeNativePlaybackReportsResumeAndConversionProgressTests/testNativePlaybackReportsResumeAndConversionProgress",
            "Lumina-iOS-UITests/NativeUnrelatedTests/testCoverageComesFromTheBuiltBundle",
        ]
        for tier in ["smoke", "full"]:
            selected = run_ui_tiers.selectors(run_ui_tiers.selected_test_identifiers(tier, discovered))
            shards = run_ui_tiers.split_shards(selected)
            self.assertEqual(len(shards), 2)
            self.assertTrue(all(shard for shard in shards))
            self.assertEqual([item for shard in shards for item in shard], selected[::2] + selected[1::2])
            self.assertEqual(sorted(item for shard in shards for item in shard), sorted(selected))
            self.assertEqual(len({item for shard in shards for item in shard}), len(selected))

    def test_compiled_enumeration_and_full_selection_are_method_exact(self) -> None:
        payload = {
            "testIdentifiers": [
                "Lumina-iOS-UITests/NativeAudioAlbumTrackPlaybackReportsProgressTests/testAlbumPlayStartsFromIdle",
                "Lumina-iOS-UITests/NativeAudioAlbumTrackPlaybackReportsProgressTests/testCompactMusicToolbarReopensAndResumes",
                "Lumina-iOS-UITests/NativeNativePlaybackReportsResumeAndConversionProgressTests/testNativePlaybackReportsResumeAndConversionProgress",
                "Lumina-iOS-UITests/NativeOtherTests/testRetainedContract",
                "Other-UITests/NativeOtherTests/testWrongTarget",
            ]
        }
        def enumerate_command(*arguments: str, **_: object) -> subprocess.CompletedProcess[str]:
            output_path = Path(arguments[arguments.index("-test-enumeration-output-path") + 1])
            output_path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.CompletedProcess([], 0, "Command line invocation:\\n", "")
        with mock.patch.object(run_ui_tiers, "command", side_effect=enumerate_command) as command:
            discovered = run_ui_tiers.enumerate_test_identifiers(Path("tests.xctestrun"), "iOS", "owned")
        self.assertEqual(discovered, sorted(payload["testIdentifiers"][:4]))
        arguments = command.call_args.args
        self.assertIn("-enumerate-tests", arguments)
        self.assertEqual(arguments[arguments.index("-test-enumeration-format") + 1], "json")
        self.assertNotEqual(arguments[arguments.index("-test-enumeration-output-path") + 1], "-")
        self.assertEqual(run_ui_tiers.selected_test_identifiers("full", discovered), discovered)
        smoke = run_ui_tiers.selected_test_identifiers("smoke", discovered)
        self.assertEqual(len(smoke), 3)
        with self.assertRaisesRegex(RuntimeError, "missing"):
            run_ui_tiers.selected_test_identifiers("smoke", discovered[2:])

    def test_coverage_manifest_records_all_discovered_full_methods_and_shards(self) -> None:
        discovered = [
            "Lumina-iOS-UITests/Class/testOne",
            "Lumina-iOS-UITests/Class/testTwo",
            "Lumina-iOS-UITests/Class/testThree",
        ]
        selectors = run_ui_tiers.selectors(discovered)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_ui_tiers.write_coverage_manifest(root, "iOS", "full", discovered, selectors,
                                                 run_ui_tiers.split_shards(selectors))
            manifest = json.loads((root / "iOS-full-coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["discovered"], discovered)
        self.assertEqual(manifest["selected"], discovered)
        self.assertEqual(manifest["routedToOtherLanes"], {})
        self.assertEqual(manifest["expectedTestCount"], 3)
        self.assertEqual(sum(shard["expectedTestCount"] for shard in manifest["shards"]), 3)

    def test_lane_shard_artifacts_are_unique_without_changing_coverage_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                path
                for label in ["iPad", "iPhone", "Duo"]
                for repeat in [1, 2]
                for shard in [1, 2]
                for path in run_ui_tiers.shard_artifact_paths(root, label, "full", repeat, shard)
            }
            self.assertEqual(len(paths), 24)
            self.assertIn(root / "iPad-full-1-shard-1.xcresult", paths)
            self.assertIn(root / "iPhone-full-2-shard-2.log", paths)
            selected = ["Lumina-iOS-UITests/Class/testOne"]
            run_ui_tiers.write_coverage_manifest(root, "iOS", "full", selected, selected, [selected],
                                                 lane="iPad", label="iPad")
            manifest = json.loads((root / "iPad-full-coverage.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["selected"], selected)

    def test_full_ios_lane_partition_routes_every_discovered_method_once(self) -> None:
        discovered = [
            "Lumina-iOS-UITests/NativeStandardPhoneEpisodeChromeTests/testEpisodeCollectionUsesSafeHeaderAndNativeLeadingEdgeBack()",
            "Lumina-iOS-UITests/NativeEpisodeShowContextTests/testSelectingEpisodeReturnsToItsShowWithTheEpisodeSelected()",
            "Lumina-iOS-UITests/NativeAudioAlbumTrackPlaybackReportsProgressTests/testCompactMusicToolbarReopensAndResumes()",
            "Lumina-iOS-UITests/NativeVideoSettingsLayoutTests/testClosedDuoSettingsPanelKeepsControlsUsable()",
            "Lumina-iOS-UITests/NativeHomeBrowseFiltersSearchAndHeroWatchlistTests/testHomeBrowseFiltersSearchAndHeroWatchlist()",
            "Lumina-iOS-UITests/NativeOrdinaryTests/testRunsOnIPad()",
        ]
        lanes = {
            lane: run_ui_tiers.selected_test_identifiers("full", discovered, lane)
            for lane in ["iPad", "iPhone", "Duo"]
        }
        self.assertEqual(lanes["iPhone"], [discovered[0], discovered[1], discovered[2]])
        self.assertEqual(lanes["Duo"], [discovered[3], discovered[4]])
        self.assertEqual(lanes["iPad"], [discovered[5]])
        self.assertEqual(sorted(item for values in lanes.values() for item in values), sorted(discovered))

    def test_combined_full_coverage_rejects_lost_or_duplicate_lane_methods(self) -> None:
        discovered = ["Lumina-iOS-UITests/Class/testOne", "Lumina-iOS-UITests/Class/testTwo"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for label, lane, selected in [
                ("iPad", "iPad", [discovered[0]]),
                ("iPhone", "iPhone", [discovered[1]]),
            ]:
                (root / f"{label}-full-coverage.json").write_text(json.dumps({
                    "platform": "iOS", "lane": lane, "discovered": discovered, "selected": selected,
                }), encoding="utf-8")
            self.assertTrue(run_ui_tiers.combined_full_coverage(root, ["iPad", "iPhone"]))
            combined = json.loads((root / "full-coverage.json").read_text(encoding="utf-8"))
            self.assertTrue(combined["iOS"]["complete"])
            (root / "iPhone-full-coverage.json").write_text(json.dumps({
                "platform": "iOS", "lane": "iPhone", "discovered": discovered, "selected": [discovered[0]],
            }), encoding="utf-8")
            self.assertFalse(run_ui_tiers.combined_full_coverage(root, ["iPad", "iPhone"]))

    def test_shard_validation_counts_concrete_cases_without_splitting_classes(self) -> None:
        selected = [
            "-only-testing:Lumina-iOS-UITests/NativeAudioAlbumTrackPlaybackReportsProgressTests",
            "-only-testing:Lumina-iOS-UITests/NativeNativePlaybackReportsResumeAndConversionProgressTests",
        ]
        validations: list[int] = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xctestrun = root / "Build" / "Products" / "tests.xctestrun"
            xctestrun.parent.mkdir(parents=True)
            runner = xctestrun.parent / "Debug-iphonesimulator" / "Runner.app"
            bundle = runner / "PlugIns" / "Lumina-iOS-UITests.xctest"
            bundle.mkdir(parents=True)
            embedded_broker = bundle / "FixtureBroker.json"
            embedded_broker.write_bytes(b"old broker")
            broker = root / "broker.json"
            broker.write_bytes(b"new broker")
            with xctestrun.open("wb") as stream:
                plistlib.dump({
                    "__xctestrun_metadata__": {},
                    "Lumina-iOS-UITests": {
                        "IsUITestBundle": True,
                        "TestHostPath": "__TESTROOT__/Debug-iphonesimulator/Runner.app",
                        "TestBundlePath": "__TESTHOST__/PlugIns/Lumina-iOS-UITests.xctest",
                    },
                }, stream)
            phases: list[str] = []

            def run_xcode(arguments: list[str], _: Path, timeout_seconds: int = 3000) -> tuple[int, float, float, float]:
                if "build-for-testing" in arguments:
                    phases.append("build")
                    self.assertIn(run_ui_tiers.UI_TEST_BUILD_CONDITIONS, arguments)
                    self.assertEqual(embedded_broker.read_bytes(), b"old broker")
                    return 0, 0.0, 0.0, 0.0
                phases.append("test")
                self.assertEqual(embedded_broker.read_bytes(), b"new broker")
                return 0, 1.0, 1.0, 2.0

            with \
             mock.patch.object(run_ui_tiers, "boot_simulators", return_value=(["one", "two"], 0.0, 0.0)), \
             mock.patch.object(run_ui_tiers, "latest_runtime", return_value="runtime"), \
             mock.patch.object(run_ui_tiers, "xctestrun_path", side_effect=[RuntimeError("not built"), xctestrun]), \
             mock.patch.object(run_ui_tiers, "enumerate_test_identifiers", return_value=[
                 "Lumina-iOS-UITests/NativeAudioAlbumTrackPlaybackReportsProgressTests/testAudioAlbumTrackPlaybackReportsProgress",
                 "Lumina-iOS-UITests/NativeAudioAlbumTrackPlaybackReportsProgressTests/testAlbumPlayStartsFromIdle",
                 "Lumina-iOS-UITests/NativeNativePlaybackReportsResumeAndConversionProgressTests/testNativePlaybackReportsResumeAndConversionProgress",
             ]), \
             mock.patch.object(run_ui_tiers, "run_xcode", side_effect=run_xcode), \
             mock.patch.object(run_ui_tiers, "result_ok", side_effect=lambda _result, _log, count: validations.append(count) or True), \
             mock.patch.object(run_ui_tiers, "command", return_value=subprocess.CompletedProcess([], 0, "", "")):
                outcome = run_ui_tiers.one_platform("iOS", "smoke", 1, root, broker, "run")
            shards = outcome["tests"][0]["shards"]
            self.assertEqual({item["tests"] for item in shards}, {1, 2})
            self.assertEqual(set(validations), {1, 2})
            self.assertEqual(phases, ["build", "test", "test"])
            self.assertEqual(embedded_broker.read_bytes(), b"new broker")
            embedded_broker.write_bytes(b"stale broker")
            with mock.patch.object(run_ui_tiers.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
                with self.assertRaisesRegex(RuntimeError, "did not match"):
                    run_ui_tiers.refresh_broker_fixture(xctestrun, broker)
            self.assertEqual(run_ui_tiers.expected_case_count(selected), 2)

    def test_unqualified_first_repeat_cancels_only_later_repeats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broker = root / "broker.json"
            broker.write_text("{}", encoding="utf-8")
            repeat_failure = threading.Event()
            common = {
                "boot_simulators": mock.patch.object(run_ui_tiers, "boot_simulators", return_value=(["one"], 0.0, 0.0)),
                "latest_runtime": mock.patch.object(run_ui_tiers, "latest_runtime", return_value="runtime"),
                "xctestrun_path": mock.patch.object(run_ui_tiers, "xctestrun_path", return_value=root / "tests.xctestrun"),
                "refresh_broker_fixture": mock.patch.object(run_ui_tiers, "refresh_broker_fixture"),
                "enumerate_test_identifiers": mock.patch.object(run_ui_tiers, "enumerate_test_identifiers", return_value=["test"]),
                "selected_test_identifiers": mock.patch.object(run_ui_tiers, "selected_test_identifiers", return_value=["test"]),
                "selectors": mock.patch.object(run_ui_tiers, "selectors", return_value=["-only-testing:test"]),
                "split_shards": mock.patch.object(run_ui_tiers, "split_shards", return_value=[["-only-testing:test"]]),
                "expected_case_count": mock.patch.object(run_ui_tiers, "expected_case_count", return_value=1),
                "write_coverage_manifest": mock.patch.object(run_ui_tiers, "write_coverage_manifest"),
                "write_simulator_crash_snapshot": mock.patch.object(run_ui_tiers, "write_simulator_crash_snapshot"),
                "command": mock.patch.object(run_ui_tiers, "command", return_value=subprocess.CompletedProcess([], 0, "", "")),
            }
            with common["boot_simulators"], common["latest_runtime"], common["xctestrun_path"], \
                 common["refresh_broker_fixture"], common["enumerate_test_identifiers"], common["selected_test_identifiers"], \
                 common["selectors"], common["split_shards"], common["expected_case_count"], \
                 common["write_coverage_manifest"], common["write_simulator_crash_snapshot"], common["command"], \
                 mock.patch.object(run_ui_tiers, "run_xcode", return_value=(1, 1.0, 1.0, 2.0)) as run_xcode:
                failed = run_ui_tiers.one_platform(
                    "iOS", "smoke", 2, root, broker, "run", repeat_failure=repeat_failure
                )
                self.assertTrue(repeat_failure.is_set())
                self.assertEqual(len(failed["tests"]), 1)
                self.assertEqual(failed["cancelledRepeats"], [2])
                self.assertEqual(failed["repeatStopReason"], "unqualified-first-repeat")
                self.assertEqual(run_xcode.call_count, 1)

                run_xcode.reset_mock(return_value=True)
                run_xcode.return_value = (0, 1.0, 1.0, 2.0)
                with mock.patch.object(run_ui_tiers, "result_ok", return_value=True):
                    later_phase = run_ui_tiers.one_platform(
                        "tvOS", "smoke", 2, root, broker, "run", repeat_failure=repeat_failure
                    )
            self.assertEqual(len(later_phase["tests"]), 1)
            self.assertTrue(later_phase["tests"][0]["passed"])
            self.assertEqual(later_phase["cancelledRepeats"], [2])
            self.assertEqual(later_phase["repeatStopReason"], "unqualified-first-repeat")

    def test_signal_cancellation_retains_completed_repeat_without_a_phase_gate_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broker = root / "broker.json"
            broker.write_text("{}", encoding="utf-8")
            common = {
                "boot_simulators": mock.patch.object(run_ui_tiers, "boot_simulators", return_value=(["one"], 0.0, 0.0)),
                "latest_runtime": mock.patch.object(run_ui_tiers, "latest_runtime", return_value="runtime"),
                "xctestrun_path": mock.patch.object(run_ui_tiers, "xctestrun_path", return_value=root / "tests.xctestrun"),
                "refresh_broker_fixture": mock.patch.object(run_ui_tiers, "refresh_broker_fixture"),
                "enumerate_test_identifiers": mock.patch.object(run_ui_tiers, "enumerate_test_identifiers", return_value=["test"]),
                "selected_test_identifiers": mock.patch.object(run_ui_tiers, "selected_test_identifiers", return_value=["test"]),
                "selectors": mock.patch.object(run_ui_tiers, "selectors", return_value=["-only-testing:test"]),
                "split_shards": mock.patch.object(run_ui_tiers, "split_shards", return_value=[["-only-testing:test"]]),
                "expected_case_count": mock.patch.object(run_ui_tiers, "expected_case_count", return_value=1),
                "write_coverage_manifest": mock.patch.object(run_ui_tiers, "write_coverage_manifest"),
                "write_simulator_crash_snapshot": mock.patch.object(run_ui_tiers, "write_simulator_crash_snapshot"),
                "command": mock.patch.object(run_ui_tiers, "command", return_value=subprocess.CompletedProcess([], 0, "", "")),
            }
            with common["boot_simulators"], common["latest_runtime"], common["xctestrun_path"], \
                 common["refresh_broker_fixture"], common["enumerate_test_identifiers"], common["selected_test_identifiers"], \
                 common["selectors"], common["split_shards"], common["expected_case_count"], \
                 common["write_coverage_manifest"], common["write_simulator_crash_snapshot"], common["command"], \
                 mock.patch.object(
                     run_ui_tiers,
                     "run_xcode",
                     side_effect=[(0, 1.0, 1.0, 2.0), run_ui_tiers.RunCancelled("signal")],
                 ), \
                 mock.patch.object(run_ui_tiers, "result_ok", return_value=True):
                outcome = run_ui_tiers.one_platform("iOS", "smoke", 2, root, broker, "run")

        self.assertEqual(outcome["buildExit"], 0)
        self.assertEqual(len(outcome["tests"]), 1)
        self.assertTrue(outcome["tests"][0]["passed"])
        self.assertTrue(outcome["runCancelled"])
        self.assertEqual(outcome["cancelReason"], "signal")
        report: dict[str, object] = {
            "phaseGateError": None,
            "coverageComplete": True,
            "withinBudget": True,
            "repeatCoverageComplete": False,
            "repeatQualificationFailed": False,
            "runCancelled": True,
        }
        self.assertFalse(run_ui_tiers.qualification_passed(report, {"iOS": outcome}, ["iOS"]))

    def test_signal_cancellation_retains_completed_shards_from_the_interrupted_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broker = root / "broker.json"
            broker.write_text("{}", encoding="utf-8")
            sibling_started = threading.Event()
            cancellation_raised = threading.Event()

            def run_xcode(arguments: list[str], _: Path, timeout_seconds: int = 3000) -> tuple[int, float, float, float]:
                destination = arguments[arguments.index("-destination") + 1]
                if destination.endswith("id=one"):
                    self.assertTrue(sibling_started.wait(timeout=1))
                    cancellation_raised.set()
                    raise run_ui_tiers.RunCancelled("signal")
                sibling_started.set()
                self.assertTrue(cancellation_raised.wait(timeout=1))
                return 0, 1.0, 1.0, 2.0

            common = {
                "boot_simulators": mock.patch.object(run_ui_tiers, "boot_simulators", return_value=(["one", "two"], 0.0, 0.0)),
                "latest_runtime": mock.patch.object(run_ui_tiers, "latest_runtime", return_value="runtime"),
                "xctestrun_path": mock.patch.object(run_ui_tiers, "xctestrun_path", return_value=root / "tests.xctestrun"),
                "refresh_broker_fixture": mock.patch.object(run_ui_tiers, "refresh_broker_fixture"),
                "enumerate_test_identifiers": mock.patch.object(run_ui_tiers, "enumerate_test_identifiers", return_value=["testOne", "testTwo"]),
                "selected_test_identifiers": mock.patch.object(run_ui_tiers, "selected_test_identifiers", return_value=["testOne", "testTwo"]),
                "selectors": mock.patch.object(run_ui_tiers, "selectors", return_value=["-only-testing:testOne", "-only-testing:testTwo"]),
                "split_shards": mock.patch.object(run_ui_tiers, "split_shards", return_value=[["-only-testing:testOne"], ["-only-testing:testTwo"]]),
                "expected_case_count": mock.patch.object(run_ui_tiers, "expected_case_count", return_value=1),
                "write_coverage_manifest": mock.patch.object(run_ui_tiers, "write_coverage_manifest"),
                "write_simulator_crash_snapshot": mock.patch.object(run_ui_tiers, "write_simulator_crash_snapshot"),
                "command": mock.patch.object(run_ui_tiers, "command", return_value=subprocess.CompletedProcess([], 0, "", "")),
            }
            with common["boot_simulators"], common["latest_runtime"], common["xctestrun_path"], \
                 common["refresh_broker_fixture"], common["enumerate_test_identifiers"], common["selected_test_identifiers"], \
                 common["selectors"], common["split_shards"], common["expected_case_count"], \
                 common["write_coverage_manifest"], common["write_simulator_crash_snapshot"], common["command"], \
                 mock.patch.object(run_ui_tiers, "run_xcode", side_effect=run_xcode), \
                 mock.patch.object(run_ui_tiers, "result_ok", return_value=True):
                outcome = run_ui_tiers.one_platform("iOS", "smoke", 2, root, broker, "run")

        self.assertEqual(outcome["buildExit"], 0)
        self.assertEqual(outcome["tests"], [])
        self.assertTrue(outcome["runCancelled"])
        self.assertEqual(outcome["cancelReason"], "signal")
        interrupted = outcome["interruptedRepeat"]
        self.assertEqual(interrupted["repeat"], 1)
        self.assertEqual(len(interrupted["shards"]), 1)
        self.assertEqual(interrupted["shards"][0]["shard"], 2)
        self.assertTrue(interrupted["shards"][0]["passed"])

    def test_shard_timeout_retains_completed_sibling_without_a_build_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broker = root / "broker.json"
            broker.write_text("{}", encoding="utf-8")
            sibling_started = threading.Event()
            timeout_raised = threading.Event()
            repeat_failure = threading.Event()

            def run_xcode(arguments: list[str], _: Path, timeout_seconds: int = 3000) -> tuple[int, float, float, float]:
                destination = arguments[arguments.index("-destination") + 1]
                if destination.endswith("id=one"):
                    self.assertTrue(sibling_started.wait(timeout=1))
                    timeout_raised.set()
                    raise subprocess.TimeoutExpired(arguments, timeout_seconds)
                sibling_started.set()
                self.assertTrue(timeout_raised.wait(timeout=1))
                return 0, 1.0, 1.0, 2.0

            common = {
                "boot_simulators": mock.patch.object(
                    run_ui_tiers, "boot_simulators", return_value=(["one", "two"], 0.0, 0.0)
                ),
                "latest_runtime": mock.patch.object(run_ui_tiers, "latest_runtime", return_value="runtime"),
                "xctestrun_path": mock.patch.object(
                    run_ui_tiers, "xctestrun_path", return_value=root / "tests.xctestrun"
                ),
                "refresh_broker_fixture": mock.patch.object(run_ui_tiers, "refresh_broker_fixture"),
                "enumerate_test_identifiers": mock.patch.object(
                    run_ui_tiers, "enumerate_test_identifiers", return_value=["testOne", "testTwo"]
                ),
                "selected_test_identifiers": mock.patch.object(
                    run_ui_tiers, "selected_test_identifiers", return_value=["testOne", "testTwo"]
                ),
                "selectors": mock.patch.object(
                    run_ui_tiers, "selectors", return_value=["-only-testing:testOne", "-only-testing:testTwo"]
                ),
                "split_shards": mock.patch.object(
                    run_ui_tiers, "split_shards", return_value=[["-only-testing:testOne"], ["-only-testing:testTwo"]]
                ),
                "expected_case_count": mock.patch.object(run_ui_tiers, "expected_case_count", return_value=1),
                "write_coverage_manifest": mock.patch.object(run_ui_tiers, "write_coverage_manifest"),
                "write_simulator_crash_snapshot": mock.patch.object(run_ui_tiers, "write_simulator_crash_snapshot"),
                "command": mock.patch.object(
                    run_ui_tiers, "command", return_value=subprocess.CompletedProcess([], 0, "", "")
                ),
            }
            with common["boot_simulators"], common["latest_runtime"], common["xctestrun_path"], \
                 common["refresh_broker_fixture"], common["enumerate_test_identifiers"], common["selected_test_identifiers"], \
                 common["selectors"], common["split_shards"], common["expected_case_count"], \
                 common["write_coverage_manifest"], common["write_simulator_crash_snapshot"], common["command"], \
                 mock.patch.object(run_ui_tiers, "run_xcode", side_effect=run_xcode), \
                 mock.patch.object(run_ui_tiers, "result_ok", return_value=True):
                outcome = run_ui_tiers.one_platform(
                    "iOS", "smoke", 2, root, broker, "run", repeat_failure=repeat_failure,
                    shard_timeout_seconds=5400,
                )

        self.assertEqual(outcome["buildExit"], 0)
        self.assertFalse(outcome.get("runCancelled", False))
        self.assertTrue(repeat_failure.is_set())
        self.assertEqual(outcome["cancelledRepeats"], [2])
        self.assertEqual(len(outcome["tests"]), 1)
        shards = outcome["tests"][0]["shards"]
        self.assertFalse(outcome["tests"][0]["passed"])
        self.assertTrue(shards[0]["timedOut"])
        self.assertEqual(shards[0]["timeoutSeconds"], 5400)
        self.assertIsNone(shards[0]["exit"])
        self.assertTrue(shards[1]["passed"])

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

    def test_crash_snapshot_limits_metadata_to_new_target_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recent = root / "Lumina-iOS-2026-10-26.ips"
            old = root / "PosterBoard-old.ips"
            unrelated = root / "OtherProcess-new.ips"
            for report in (recent, old, unrelated):
                report.write_text("metadata only", encoding="utf-8")
            os.utime(old, (10, 10))
            entries = run_ui_tiers.crash_report_entries(root, since_epoch=100)
        self.assertEqual([entry["name"] for entry in entries], [recent.name])


if __name__ == "__main__":
    unittest.main()
