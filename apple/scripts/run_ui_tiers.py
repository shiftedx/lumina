#!/usr/bin/env python3
"""Run isolated native UI tiers without touching another developer's simulators.

Each invocation creates its own simulators and broker, discovers the compiled
test methods, runs two explicit test shards in parallel, and removes only
identifiers and processes it created.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APPLE = ROOT / "apple"
SMOKE_CLASSES = [
    "NativeAudioAlbumTrackPlaybackReportsProgressTests",
    "NativeNativePlaybackReportsResumeAndConversionProgressTests",
]
IOS_SPECIAL_LANES = {
    "NativeBookCurlTests/testCompactReaderDefaultsToOnePage": "iPhone",
    "NativeVideoSettingsLayoutTests/testClosedDuoSettingsPanelKeepsControlsUsable": "Duo",
    "NativeStandardPhoneEpisodeChromeTests/testEpisodeCollectionUsesSafeHeaderAndNativeLeadingEdgeBack": "iPhone",
    "NativeEpisodeShowContextTests/testSelectingEpisodeReturnsToItsShowWithTheEpisodeSelected": "iPhone",
    "NativeStandardPhonePushedChromeNavigationTests/testHomeSearchUsesLeadingEdgeBackWithoutPoppingRoot": "iPhone",
    "NativeStandardPhoneAppearanceChromeTests/testPushedChromeStaysClearInLightAndDarkAppearances": "iPhone",
    "NativeHomeBrowseFiltersSearchAndHeroWatchlistTests/testStandardPhoneUsesBottomNavigationAndCompactBrowseMenu": "iPhone",
    "NativeVideoFormatRecoveryTests/testStandardPhoneInitialFormatFailureRetriesOneCompatibleVideo": "iPhone",
    "NativeAudioAlbumTrackPlaybackReportsProgressTests/testCompactMusicToolbarReopensAndResumes": "iPhone",
    "NativeHomeBrowseFiltersSearchAndHeroWatchlistTests/testClosedDisplayFitsThreeCategoriesBesideLogo": "Duo",
    "NativeHomeBrowseFiltersSearchAndHeroWatchlistTests/testHomeHeroArtworkSwipesAndPlaysSelectedTitle": "Duo",
    "NativeOpenDuoLibraryHeaderTests/testOpenDuoLibraryHeaderAndSearchKeepCategoriesClearOfChrome": "Duo",
    "NativeOpenDuoLibraryHeaderTests/testOpenPortraitPlacesNavigationOverContentAndUtilitiesBelowStatus": "Duo",
    "NativeHomeBrowseFiltersSearchAndHeroWatchlistTests/testHomeBrowseFiltersSearchAndHeroWatchlist": "Duo",
}
IOS_LANE_DEVICE_TYPES = {
    "iPad": "iPad Pro 11-inch (M5)",
    "iPhone": "iPhone 17",
}
BUDGET = {"smoke": 600, "full": 5400}
UI_TEST_BUILD_CONDITIONS = "SWIFT_ACTIVE_COMPILATION_CONDITIONS=$(inherited) LUMINA_UI_TESTING"
IOS_RUNTIME: str | None = None
CANCELLED = threading.Event()
CRASH_REPORT_PROCESSES = ("lumina", "posterboard", "springboard", "backboardd")
LANE_ACTIVE_SHARDS = {"iPad": 2, "tvOS": 2, "iPhone": 2, "Duo": 1}


class RunCancelled(RuntimeError):
    """A signal stopped the runner; this is not a build or test failure."""


def check_cancelled() -> None:
    if CANCELLED.is_set():
        raise RunCancelled("native UI tier run cancelled")


def effective_budget(tier: str, max_active_shards: int) -> int:
    """Scale only the full-run orchestration wall budget for an explicit shard cap."""
    base = BUDGET[tier]
    if tier != "full":
        return base
    return base * 4 // max_active_shards


def command(*parts: str, cwd: Path = ROOT, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(parts, cwd=cwd, text=True, capture_output=capture, check=False)


def latest_runtime(platform: str, ios_device_type: str | None = None) -> str:
    data = json.loads(command("xcrun", "simctl", "list", "runtimes", "-j", capture=True).stdout)
    prefix = f"com.apple.CoreSimulator.SimRuntime.{platform}-"
    required = device_type(platform, ios_device_type)
    candidates = [entry for entry in data["runtimes"] if entry.get("isAvailable")
                  and entry["identifier"].startswith(prefix)
                  and any(device["identifier"] == required for device in entry.get("supportedDeviceTypes", []))]
    if platform == "iOS" and IOS_RUNTIME:
        candidates = [entry for entry in candidates if entry["version"] == IOS_RUNTIME]
    if not candidates:
        raise RuntimeError(f"no compatible available {platform} simulator runtime")
    return max(candidates, key=lambda entry: tuple(int(part) for part in entry["version"].split(".")))["identifier"]


def device_type(platform: str, ios_device_type: str | None = None) -> str:
    wanted = (ios_device_type or IOS_LANE_DEVICE_TYPES["iPad"])
    if platform != "iOS":
        wanted = "Apple TV 4K (3rd generation)"
    data = json.loads(command("xcrun", "simctl", "list", "devicetypes", "-j", capture=True).stdout)
    for entry in data["devicetypes"]:
        if entry["name"] == wanted:
            return entry["identifier"]
    raise RuntimeError(f"required simulator type unavailable: {wanted}")


def create_simulator(platform: str, run_id: str, number: int, ios_device_type: str | None = None) -> str:
    check_cancelled()
    name = f"Lumina UI tier {run_id} {platform} {number}"
    result = command("xcrun", "simctl", "create", name, device_type(platform, ios_device_type),
                     latest_runtime(platform, ios_device_type), capture=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "simulator creation failed")
    return result.stdout.strip()


def delete_own_simulators(run_id: str) -> None:
    data = json.loads(command("xcrun", "simctl", "list", "devices", "-j", capture=True).stdout)
    prefix = f"Lumina UI tier {run_id} "
    for devices in data["devices"].values():
        for device in devices:
            if device.get("name", "").startswith(prefix) or f" of {prefix}" in device.get("name", ""):
                command("xcrun", "simctl", "shutdown", device["udid"])
                command("xcrun", "simctl", "delete", device["udid"])


def crash_report_entries(root: Path, since_epoch: float) -> list[dict[str, object]]:
    """Return recent, relevant crash-report metadata without copying report contents."""
    if not root.is_dir():
        return []
    entries: list[dict[str, object]] = []
    for report in root.rglob("*"):
        if not report.is_file():
            continue
        try:
            stat = report.stat()
        except OSError:
            continue
        lowered = report.name.lower()
        if stat.st_mtime >= since_epoch and any(process in lowered for process in CRASH_REPORT_PROCESSES):
            entries.append({
                "name": report.name,
                "mtimeEpoch": round(stat.st_mtime, 3),
                "sizeBytes": stat.st_size,
            })
    return sorted(entries, key=lambda entry: (str(entry["name"]), float(entry["mtimeEpoch"])))


def write_simulator_crash_snapshot(output: Path, label: str, simulators: list[str], since_epoch: float) -> None:
    """Persist metadata before this runner deletes its own ordinary simulators."""
    device_root = Path.home() / "Library" / "Developer" / "CoreSimulator" / "Devices"
    snapshot = {
        "scope": "owned simulator CrashReporter",
        "sinceEpoch": round(since_epoch, 3),
        "processes": list(CRASH_REPORT_PROCESSES),
        "devices": {
            simulator: crash_report_entries(
                device_root / simulator / "data" / "Library" / "Logs" / "CrashReporter", since_epoch
            ) for simulator in simulators
        },
    }
    (output / f"{label}-crash-reports.json").write_text(
        json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
    )


def write_host_crash_snapshot(output: Path, since_epoch: float) -> None:
    snapshot = {
        "scope": "host DiagnosticReports",
        "sinceEpoch": round(since_epoch, 3),
        "processes": list(CRASH_REPORT_PROCESSES),
        "reports": crash_report_entries(Path.home() / "Library" / "Logs" / "DiagnosticReports", since_epoch),
    }
    (output / "host-crash-reports.json").write_text(
        json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
    )


def run_xcode(arguments: list[str], log: Path, timeout_seconds: int = 3000) -> tuple[int, float, float, float]:
    check_cancelled()
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(arguments, cwd=APPLE, text=True, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + timeout_seconds
            peak_load = 0.0
            while True:
                check_cancelled()
                if process.poll() is not None:
                    break
                load = os.getloadavg()[0]
                peak_load = max(peak_load, load)
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(arguments, timeout_seconds)
                time.sleep(1)
            stream.write(f"\nPeak host load1: {peak_load:.2f}\n")
        except (KeyboardInterrupt, subprocess.TimeoutExpired, RuntimeError):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    finished = time.monotonic()
    return process.returncode, finished - started, started, finished


def result_ok(result: Path, log: Path, expected_count: int) -> bool:
    text = log.read_text(encoding="utf-8", errors="replace")
    if not any(marker in text for marker in ("** TEST SUCCEEDED **", "** TEST EXECUTE SUCCEEDED **")) or "skipped" in text.lower():
        return False
    parsed = command("xcrun", "xcresulttool", "get", "test-results", "summary", "--path", str(result), capture=True)
    if parsed.returncode:
        return False
    try:
        summary = json.loads(parsed.stdout)
    except json.JSONDecodeError:
        return False
    return (summary.get("result") == "Passed" and summary.get("totalTestCount") == expected_count
            and summary.get("passedTests") == expected_count and summary.get("failedTests") == 0
            and summary.get("skippedTests") == 0 and summary.get("expectedFailures") == 0)


def test_identifier_strings(value: object) -> list[str]:
    """Extract Xcode method identifiers from the supported enumeration JSON."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"identifier", "testIdentifier"} and isinstance(nested, str):
                found.append(nested)
            else:
                found.extend(test_identifier_strings(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(test_identifier_strings(nested))
    elif isinstance(value, str):
        found.append(value)
    return found


def enumerate_test_identifiers(xctestrun: Path, platform: str, simulator: str) -> list[str]:
    """Ask Xcode which methods the exact built UI bundle can execute."""
    check_cancelled()
    with tempfile.TemporaryDirectory(prefix="lumina-ui-enumeration-") as directory:
        enumeration = Path(directory) / "tests.json"
        listed = command(
            "xcodebuild", "test-without-building", "-xctestrun", str(xctestrun),
            "-destination", f"platform={platform} Simulator,id={simulator}",
            "-enumerate-tests", "-test-enumeration-style", "flat",
            "-test-enumeration-format", "json", "-test-enumeration-output-path", str(enumeration), capture=True,
        )
        if listed.returncode:
            raise RuntimeError(listed.stderr.strip() or "Xcode could not enumerate compiled UI tests")
        try:
            payload = json.loads(enumeration.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Xcode returned invalid JSON while enumerating compiled UI tests") from error
    target = f"Lumina-{platform}-UITests/"
    discovered = sorted({identifier for identifier in test_identifier_strings(payload)
                         if identifier.startswith(target) and identifier.count("/") >= 2})
    if not discovered:
        raise RuntimeError(f"Xcode enumerated no compiled {platform} UI test methods")
    return discovered


def ios_lane(identifier: str) -> str:
    components = identifier.rsplit("/", 2)
    if len(components) < 2:
        return "iPad"
    key = f"{components[-2]}/{components[-1].removesuffix('()')}"
    return IOS_SPECIAL_LANES.get(key, "iPad")


def selected_test_identifiers(tier: str, discovered: list[str], lane: str | None = None) -> list[str]:
    """Keep smoke curated; full executes every compiled method for its platform."""
    if tier == "full":
        if lane in {"iPad", "iPhone", "Duo"}:
            selected = [identifier for identifier in discovered if ios_lane(identifier) == lane]
            if not selected:
                raise RuntimeError(f"compiled iOS full tier has no tests assigned to {lane}")
            return selected
        return discovered
    selected = [identifier for identifier in discovered
                if identifier.rsplit("/", 2)[-2] in SMOKE_CLASSES]
    found = {identifier.rsplit("/", 2)[-2] for identifier in selected}
    missing = sorted(set(SMOKE_CLASSES) - found)
    if missing:
        raise RuntimeError(f"compiled smoke classes are missing: {', '.join(missing)}")
    return selected


def selectors(test_identifiers: list[str]) -> list[str]:
    return [f"-only-testing:{identifier}" for identifier in test_identifiers]


def split_shards(values: list[str], count: int = 2) -> list[list[str]]:
    return [values[index::count] for index in range(count)]


def expected_case_count(selectors: list[str]) -> int:
    return len(selectors)


def shard_artifact_paths(output: Path, label: str, tier: str, repeat: int, shard: int) -> tuple[Path, Path]:
    stem = f"{label}-{tier}-{repeat}-shard-{shard}"
    return output / f"{stem}.xcresult", output / f"{stem}.log"


def write_coverage_manifest(output: Path, platform: str, tier: str, discovered: list[str],
                            selected: list[str], shards: list[list[str]], lane: str | None = None,
                            label: str | None = None) -> None:
    selected_identifiers = [selector.removeprefix("-only-testing:") for selector in selected]
    manifest = {
        "platform": platform,
        "lane": lane,
        "tier": tier,
        "discovered": discovered,
        "selected": selected_identifiers,
        "routedToOtherLanes": ({identifier: ios_lane(identifier) for identifier in discovered
                                 if identifier not in selected_identifiers} if lane else {}),
        "expectedTestCount": len(selected_identifiers),
        "shards": [{"number": number + 1,
                    "tests": [selector.removeprefix("-only-testing:") for selector in shard],
                    "expectedTestCount": len(shard)} for number, shard in enumerate(shards)],
    }
    (output / f"{label or platform}-{tier}-coverage.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def combined_full_coverage(output: Path, labels: list[str]) -> bool:
    """Verify no compiled full-test method is lost or duplicated across lanes."""
    by_platform: dict[str, list[dict[str, object]]] = {}
    for label in labels:
        path = output / f"{label}-full-coverage.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            platform = manifest["platform"]
            if not isinstance(platform, str):
                raise ValueError("platform")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return False
        by_platform.setdefault(platform, []).append(manifest)

    complete = True
    aggregate: dict[str, object] = {}
    for platform, manifests in by_platform.items():
        discovered = manifests[0].get("discovered")
        selected = [identifier for manifest in manifests for identifier in manifest.get("selected", [])]
        consistent = all(manifest.get("discovered") == discovered for manifest in manifests)
        exact = (isinstance(discovered, list) and len(selected) == len(set(selected))
                 and set(selected) == set(discovered))
        aggregate[platform] = {
            "discovered": discovered,
            "selected": sorted(selected),
            "lanes": {str(manifest.get("lane") or platform): manifest.get("selected", []) for manifest in manifests},
            "complete": consistent and exact,
        }
        complete = complete and consistent and exact
    (output / "full-coverage.json").write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
    return complete


def broker_config_path(output: Path, platform: str) -> Path:
    return output / f"{platform}-broker.json"


def start_broker(output: Path, platform: str) -> tuple[subprocess.Popen[bytes], Path]:
    check_cancelled()
    config = broker_config_path(output, platform)
    broker = subprocess.Popen([sys.executable, str(APPLE / "scripts" / "ui_suite.py"), "--output", str(config)],
                              cwd=ROOT, start_new_session=True)
    try:
        deadline = time.monotonic() + 15
        while not config.exists() and time.monotonic() < deadline:
            check_cancelled()
            time.sleep(0.1)
        if not config.exists():
            raise RuntimeError(f"{platform} fixture broker did not start")
    except BaseException:
        if broker.poll() is None:
            os.killpg(broker.pid, signal.SIGTERM)
            broker.wait(timeout=30)
        raise
    return broker, config


def wait_for_cohort(cohort: threading.Barrier | None, timeout: int) -> None:
    check_cancelled()
    if cohort is not None:
        cohort.wait(timeout=timeout)


def per_repeat_walls(results: dict[str, dict[str, object]], platforms: list[str], repeat: int) -> list[float]:
    if (set(results) != set(platforms) or any(results[platform]["buildExit"] != 0 for platform in platforms)
            or any(len(results[platform]["tests"]) < repeat for platform in platforms)):
        return []
    walls = []
    for index in range(repeat):
        tests = [results[platform]["tests"][index] for platform in platforms]
        try:
            wall = max(float(test["finishedMonotonic"]) for test in tests) - min(float(test["startedMonotonic"]) for test in tests)
            if index == 0:
                wall += max(float(results[platform]["setupFinishedMonotonic"]) for platform in platforms) - min(float(results[platform]["setupStartedMonotonic"]) for platform in platforms)
        except (KeyError, TypeError):
            return []
        walls.append(wall)
    return walls


def phased_repeat_walls(results: dict[str, dict[str, object]], phases: list[list[str]], repeat: int) -> list[float]:
    """Add serial phase walls while retaining each phase's concurrent wall time."""
    walls = [0.0] * repeat
    for phase in phases:
        if not phase:
            continue
        phase_walls = per_repeat_walls({label: results[label] for label in phase if label in results}, phase, repeat)
        if len(phase_walls) != repeat:
            return []
        walls = [wall + phase_wall for wall, phase_wall in zip(walls, phase_walls)]
    return walls


def validated_prepared_ios_device(device: str, readiness: Path | None) -> None:
    """Require a human-verified, closed-Duo application viewport before use."""
    if readiness is None:
        raise RuntimeError("a prepared iOS device requires --prepared-ios-readiness")
    try:
        report = json.loads(readiness.read_text(encoding="utf-8"))
        viewport = report["appViewport"]
        width = float(viewport["width"])
        height = float(viewport["height"])
        screenshot = Path(report["screenshotPath"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("prepared iOS readiness must contain simulatorUDID, appViewport, and screenshotPath") from error
    if report.get("simulatorUDID") != device or report.get("posture") != "closed":
        raise RuntimeError("prepared iOS readiness does not verify this closed Duo simulator")
    if width >= 700 or height <= width:
        raise RuntimeError(f"prepared iOS app viewport is not closed portrait: {width}x{height}")
    if not screenshot.is_absolute() or not screenshot.is_file():
        raise RuntimeError("prepared iOS readiness must retain an absolute app screenshot")


def boot_simulators(platform: str, run_id: str, prepared_ios_device: str | None = None,
                    prepared_ios_readiness: Path | None = None,
                    ios_device_type: str | None = None) -> tuple[list[str], float, float]:
    started = time.monotonic()
    if prepared_ios_device is not None:
        if platform != "iOS":
            raise RuntimeError("a prepared device can only be used for the iOS lane")
        validated_prepared_ios_device(prepared_ios_device, prepared_ios_readiness)
        status = subprocess.run(["xcrun", "simctl", "bootstatus", prepared_ios_device, "-b"],
                                cwd=ROOT, text=True, capture_output=True, timeout=300)
        if status.returncode:
            raise RuntimeError(status.stderr.strip() or "prepared iOS simulator did not finish booting")
        return [prepared_ios_device], time.monotonic() - started, 0.0

    def boot_one(number: int) -> str:
        check_cancelled()
        simulator = create_simulator(platform, run_id, number, ios_device_type)
        # Fresh iOS homes otherwise eagerly render stock widgets and index an empty library.
        # Lumina has no WidgetKit, Photos or CoreSpotlight integration under test.
        background = [f"--disabledJob={job}" for job in (
            "com.apple.chronod", "com.apple.photoanalysisd", "com.apple.corespotlightservice",
        )] if platform == "iOS" else []
        check_cancelled()
        boot = command("xcrun", "simctl", "boot", simulator, *background, capture=True)
        if boot.returncode:
            raise RuntimeError(boot.stderr.strip() or f"could not boot {platform} simulator {number}")
        status = subprocess.run(["xcrun", "simctl", "bootstatus", simulator, "-b"],
                                cwd=ROOT, text=True, capture_output=True, timeout=300)
        if status.returncode:
            raise RuntimeError(status.stderr.strip() or f"{platform} simulator {number} did not finish booting")
        check_cancelled()
        return simulator

    with ThreadPoolExecutor(max_workers=2) as pool:
        simulators = list(pool.map(boot_one, range(1, 3)))
    return simulators, time.monotonic() - started, 0.0


def xctestrun_path(derived: Path, scheme: str) -> Path:
    paths = sorted(derived.glob("Build/Products/*.xctestrun"))
    matching = [path for path in paths if scheme in path.name]
    if len(matching) == 1:
        return matching[0]
    if len(paths) == 1:
        return paths[0]
    raise RuntimeError(f"expected one {scheme} xctestrun under {derived}, found {len(matching) or len(paths)}")


def refresh_broker_fixture(xctestrun: Path, broker_file: Path) -> None:
    """Copy a fresh broker into the UI bundle selected by an xctestrun file."""
    if not broker_file.is_file():
        raise RuntimeError("fixture broker configuration is missing")
    try:
        configuration = plistlib.loads(xctestrun.read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise RuntimeError("could not read the xctestrun configuration") from error
    targets = [target for name, target in configuration.items()
               if name != "__xctestrun_metadata__" and isinstance(target, dict)
               and target.get("IsUITestBundle")]
    if len(targets) != 1:
        raise RuntimeError("expected one UI test bundle in the xctestrun configuration")
    target = targets[0]
    host_path = target.get("TestHostPath")
    bundle_path = target.get("TestBundlePath")
    if not isinstance(host_path, str) or not isinstance(bundle_path, str):
        raise RuntimeError("xctestrun is missing its UI test host or bundle path")
    test_root = xctestrun.parent.resolve()
    host = Path(host_path.replace("__TESTROOT__", str(test_root)))
    bundle = Path(bundle_path.replace("__TESTROOT__", str(test_root)).replace("__TESTHOST__", str(host)))
    if not bundle.is_relative_to(test_root):
        raise RuntimeError("xctestrun UI test bundle is outside its build products")

    environment = os.environ | {
        "SRCROOT": str(APPLE),
        "TARGET_BUILD_DIR": str(bundle.parent),
        "UNLOCALIZED_RESOURCES_FOLDER_PATH": bundle.name,
        "LUMINA_NATIVE_BROKER_FILE": str(broker_file),
    }
    copied = subprocess.run([str(APPLE / "scripts" / "copy_test_fixture.sh")], cwd=ROOT,
                            text=True, capture_output=True, env=environment, check=False)
    if copied.returncode:
        raise RuntimeError("could not refresh the UI test fixture broker")
    embedded = bundle / "FixtureBroker.json"
    try:
        matches = embedded.read_bytes() == broker_file.read_bytes()
    except OSError as error:
        raise RuntimeError("UI test bundle did not contain the refreshed fixture broker") from error
    if not matches:
        raise RuntimeError("UI test bundle fixture broker did not match the fresh broker")


def one_platform(platform: str, tier: str, repeat: int, output: Path, broker_file: Path, run_id: str,
                 derived_root: Path | None = None, cohort: threading.Barrier | None = None,
                 prepared_ios_device: str | None = None, prepared_ios_readiness: Path | None = None,
                 ios_device_type: str | None = None, lane: str | None = None,
                 label: str | None = None, repeat_failure: threading.Event | None = None,
                 shard_timeout_seconds: int = 3000) -> dict[str, object]:
    crash_since_epoch = time.time()
    scheme = f"Lumina-{platform}"
    artifact_label = label or platform
    # iPhone, iPad, and prepared Duo execute the same compiled iOS bundle.
    # Phases are serial, so one per-platform product root avoids three package
    # checkouts/unpacks without a simultaneous build race.
    derived = (derived_root or output) / f"derived-{platform}"
    build_log = output / f"{artifact_label}-build.log"
    simulators: list[str] = []
    outcome: dict[str, object] = {"buildExit": None, "tests": []}
    try:
        check_cancelled()
        setup_started = time.monotonic()
        simulators, setup_seconds, setup_wait_seconds = boot_simulators(
            platform, run_id, prepared_ios_device, prepared_ios_readiness, ios_device_type
        )
        setup_finished = time.monotonic()
        check_cancelled()
        try:
            xctestrun = xctestrun_path(derived, scheme)
            reused_build = True
        except RuntimeError:
            xctestrun = None
            reused_build = False
        build_wait_seconds = 0.0
        if reused_build:
            code, build_seconds = 0, 0.0
        else:
            base = ["xcodebuild", "-jobs", "2", "-project", "Lumina.xcodeproj", "-scheme", scheme,
                    "-destination", f"platform={platform} Simulator,id={simulators[0]}",
                    "-derivedDataPath", str(derived), f"LUMINA_NATIVE_BROKER_FILE={broker_file}"]
            code, build_seconds, _, _ = run_xcode([*base, UI_TEST_BUILD_CONDITIONS, "build-for-testing"], build_log)
        outcome = {
            "setupSeconds": round(setup_seconds, 3), "setupWaitSeconds": round(setup_wait_seconds, 3),
            "setupStartedMonotonic": setup_started, "setupFinishedMonotonic": setup_finished,
            "buildSeconds": round(build_seconds, 3), "buildWaitSeconds": round(build_wait_seconds, 3),
            "buildExit": code, "buildReused": reused_build, "runtime": latest_runtime(platform, ios_device_type), "lane": lane, "tests": [],
        }
        if code:
            if cohort is not None:
                cohort.abort()
            return outcome
        check_cancelled()
        if xctestrun is None:
            xctestrun = xctestrun_path(derived, scheme)
        refresh_broker_fixture(xctestrun, broker_file)
        discovered = enumerate_test_identifiers(xctestrun, platform, simulators[0])
        selected = selectors(selected_test_identifiers(tier, discovered, lane))
        shards = split_shards(selected, count=len(simulators))
        write_coverage_manifest(output, platform, tier, discovered, selected, shards, lane, artifact_label)
        for index in range(repeat):
            check_cancelled()
            if index and repeat_failure is not None and repeat_failure.is_set():
                outcome["cancelledRepeats"] = list(range(index + 1, repeat + 1))
                outcome["repeatStopReason"] = "unqualified-first-repeat"
                break
            wait_for_cohort(cohort, BUDGET[tier] + 600)
            check_cancelled()
            test_wait_seconds = 0.0
            started = time.monotonic()
            def run_shard(number: int) -> dict[str, object]:
                result, log = shard_artifact_paths(output, artifact_label, tier, index + 1, number + 1)
                expected_count = expected_case_count(shards[number])
                test = ["xcodebuild", "test-without-building", "-xctestrun", str(xctestrun),
                        "-destination", f"platform={platform} Simulator,id={simulators[number]}",
                        "-parallel-testing-enabled", "NO", "-collect-test-diagnostics", "never",
                        "-resultBundlePath", str(result), *shards[number]]
                shard_started = time.monotonic()
                try:
                    code, seconds, shard_started, shard_finished = run_xcode(test, log, shard_timeout_seconds)
                except subprocess.TimeoutExpired:
                    shard_finished = time.monotonic()
                    return {"shard": number + 1, "tests": expected_count, "seconds": shard_timeout_seconds,
                            "startedMonotonic": shard_started, "finishedMonotonic": shard_finished,
                            "exit": None, "passed": False, "timedOut": True,
                            "timeoutSeconds": shard_timeout_seconds}
                return {"shard": number + 1, "tests": expected_count, "seconds": round(seconds, 3),
                        "startedMonotonic": shard_started, "finishedMonotonic": shard_finished,
                        "exit": code, "passed": code == 0 and result_ok(result, log, expected_count)}
            shard_results_by_number: dict[int, dict[str, object]] = {}
            shard_cancelled = False
            with ThreadPoolExecutor(max_workers=len(shards)) as pool:
                futures = {pool.submit(run_shard, number): number for number in range(len(shards))}
                try:
                    for future in as_completed(futures):
                        shard_results_by_number[futures[future]] = future.result()
                except RunCancelled:
                    shard_cancelled = True
            if shard_cancelled:
                for future, number in futures.items():
                    if number in shard_results_by_number or future.cancelled():
                        continue
                    try:
                        shard_results_by_number[number] = future.result()
                    except RunCancelled:
                        continue
                outcome["interruptedRepeat"] = {
                    "repeat": index + 1,
                    "shards": [shard_results_by_number[number] for number in sorted(shard_results_by_number)],
                }
                raise RunCancelled("native UI tier run cancelled")
            shard_results = [shard_results_by_number[number] for number in range(len(shards))]
            repeat_outcome = {"repeat": index + 1, "seconds": round(time.monotonic() - started, 3),
                                     "startedMonotonic": min(item["startedMonotonic"] for item in shard_results),
                                     "finishedMonotonic": max(item["finishedMonotonic"] for item in shard_results),
                                     "waitSeconds": round(test_wait_seconds, 3), "shards": shard_results,
                                     "passed": all(item["passed"] for item in shard_results)}
            outcome["tests"].append(repeat_outcome)
            if index == 0 and not repeat_outcome["passed"] and repeat_failure is not None:
                repeat_failure.set()
            check_cancelled()
            wait_for_cohort(cohort, BUDGET[tier] + 600)
        return outcome
    except RunCancelled:
        if cohort is not None:
            cohort.abort()
        outcome["runCancelled"] = True
        outcome["cancelReason"] = "signal"
        return outcome
    except Exception as error:
        if cohort is not None:
            cohort.abort()
        return {"buildExit": 1, "error": f"{type(error).__name__}: {error}", "tests": []}
    finally:
        write_simulator_crash_snapshot(output, artifact_label, simulators, crash_since_epoch)
        for simulator in simulators:
            if simulator == prepared_ios_device:
                continue
            command("xcrun", "simctl", "shutdown", simulator)
            command("xcrun", "simctl", "delete", simulator)


def qualification_phases(lane_specs: list[dict[str, object]], max_active_shards: int = 4) -> list[list[dict[str, object]]]:
    """Pack complete lanes by simulator/shard demand, preserving iOS build reuse order."""
    phases: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_slots = 0
    by_label = {str(spec["label"]): spec for spec in lane_specs}
    for label in ("iPad", "tvOS", "iPhone", "Duo"):
        spec = by_label.get(label)
        if spec is None:
            continue
        slots = LANE_ACTIVE_SHARDS[label]
        if slots > max_active_shards:
            raise ValueError(f"{label} needs {slots} active shards, above cap {max_active_shards}")
        if current and current_slots + slots > max_active_shards:
            phases.append(current)
            current, current_slots = [], 0
        current.append(spec)
        current_slots += slots
    if current:
        phases.append(current)
    return phases


def iPad_build_ready(results: dict[str, dict[str, object]]) -> bool:
    """Do not launch shared-root consumers when the iPad producer failed."""
    return results.get("iPad", {}).get("buildExit") == 0


def qualification_passed(report: dict[str, object], results: dict[str, dict[str, object]],
                         platforms: list[str]) -> bool:
    return (not report.get("runCancelled", False) and report["phaseGateError"] is None and report["coverageComplete"]
            and set(results) == set(platforms) and report["withinBudget"]
            and report["repeatCoverageComplete"] and not report["repeatQualificationFailed"]
            and all(test["passed"] for platform in results.values() if platform["buildExit"] == 0
                    for test in platform["tests"]))


def positive_seconds(value: str) -> int:
    seconds = int(value)
    if seconds < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return seconds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=BUDGET, required=True)
    parser.add_argument("--platform", choices=("iOS", "tvOS", "all"), default="all")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--max-active-shards", choices=(2, 4), type=int, default=4,
                        help="maximum concurrent simulator test shards; iPad, iPhone, and tvOS lanes each use two")
    parser.add_argument("--shard-timeout-seconds", type=positive_seconds, default=3000,
                        help="per-xcodebuild test shard wall limit; build-for-testing remains fixed at 3000 seconds")
    parser.add_argument("--ios-runtime", help="pin an installed iPadOS runtime version for qualification")
    parser.add_argument("--prepared-duo-device", help="owned closed-Duo simulator UDID; never reset or deleted")
    parser.add_argument("--prepared-duo-readiness", type=Path,
                        help="JSON proof of this prepared Duo's closed application viewport")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--derived-data", type=Path, help="reuse this run-owned build root for warm qualification")
    args = parser.parse_args()
    global IOS_RUNTIME
    IOS_RUNTIME = args.ios_runtime
    if (args.prepared_duo_device is None) != (args.prepared_duo_readiness is None):
        parser.error("--prepared-duo-device and --prepared-duo-readiness must be supplied together")
    if args.prepared_duo_device is not None and args.platform == "tvOS":
        parser.error("a prepared Duo cannot run the tvOS tier")
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    budget_seconds = effective_budget(args.tier, args.max_active_shards)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be a fresh empty private directory")
    args.output.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(args.output, 0o700)
    CANCELLED.clear()
    cohort_holder: dict[str, threading.Barrier | None] = {"cohort": None}

    def cancel(_signum: int, _frame: object) -> None:
        CANCELLED.set()
        if cohort_holder["cohort"] is not None:
            cohort_holder["cohort"].abort()

    signal.signal(signal.SIGINT, cancel)
    signal.signal(signal.SIGTERM, cancel)
    run_id = f"{os.getpid()}-{int(time.time())}"
    started = time.monotonic()
    crash_since_epoch = time.time()
    results: dict[str, dict[str, object]] = {}
    brokers: list[subprocess.Popen[bytes]] = []
    phase_labels: list[list[str]] = []
    phases: list[list[dict[str, object]]] = []
    phase_gate_error: str | None = None
    phase_two_cancelled: list[str] = []
    repeat_failure = threading.Event()
    run_cancelled = False
    try:
        lane_specs: list[dict[str, object]] = []
        if args.platform in {"iOS", "all"}:
            lane_specs.extend([
                {"label": "iPhone", "platform": "iOS", "lane": "iPhone",
                 "deviceType": IOS_LANE_DEVICE_TYPES["iPhone"]},
                {"label": "iPad", "platform": "iOS", "lane": "iPad",
                 "deviceType": IOS_LANE_DEVICE_TYPES["iPad"]},
            ])
            if args.tier == "full":
                if args.prepared_duo_device is None:
                    parser.error("full iOS qualification requires --prepared-duo-device and --prepared-duo-readiness")
                lane_specs.append({"label": "Duo", "platform": "iOS", "lane": "Duo",
                                   "deviceType": None, "preparedDevice": args.prepared_duo_device,
                                   "preparedReadiness": args.prepared_duo_readiness})
        if args.platform in {"tvOS", "all"}:
            lane_specs.append({"label": "tvOS", "platform": "tvOS", "lane": None, "deviceType": None})
        broker_files: dict[str, Path] = {}
        for spec in lane_specs:
            check_cancelled()
            platform = str(spec["platform"])
            if platform in broker_files:
                continue
            broker, config = start_broker(args.output, platform)
            brokers.append(broker)
            broker_files[platform] = config

        # iPad builds the shared iOS root before the iPhone/Duo consumers.
        # A phase owns all of its simulators and deletes them before the next phase.
        phases = qualification_phases(lane_specs, args.max_active_shards)
        phase_labels = [[str(spec["label"]) for spec in phase] for phase in phases]
        for phase_index, phase in enumerate(phases):
            if not phase:
                continue
            cohort = threading.Barrier(len(phase)) if len(phase) > 1 else None
            cohort_holder["cohort"] = cohort
            if CANCELLED.is_set():
                run_cancelled = True
                break
            with ThreadPoolExecutor(max_workers=len(phase)) as pool:
                futures = {
                    str(spec["label"]): pool.submit(
                        one_platform, str(spec["platform"]), args.tier, args.repeat, args.output,
                        broker_files[str(spec["platform"])], run_id, args.derived_data, cohort,
                        spec.get("preparedDevice"), spec.get("preparedReadiness"),
                        spec.get("deviceType"), spec.get("lane"), str(spec["label"]), repeat_failure,
                        args.shard_timeout_seconds,
                    ) for spec in phase
                }
                for label, future in futures.items():
                    results[label] = future.result()
            if CANCELLED.is_set() or any(result.get("runCancelled") for result in results.values()):
                run_cancelled = True
                if phase_index + 1 < len(phases):
                    phase_two_cancelled = [str(spec["label"])
                                           for later in phases[phase_index + 1:] for spec in later]
                break
            if phase_index == 0 and any(spec["label"] == "iPad" for spec in phase):
                if not results.get("iPad", {}).get("runCancelled") and not iPad_build_ready(results):
                    phase_gate_error = "iPad build/xctestrun preflight failed"
                    phase_two_cancelled = [str(spec["label"])
                                           for later in phases[phase_index + 1:] for spec in later]
                    break
    except RunCancelled:
        run_cancelled = True
    finally:
        for broker in brokers:
            if broker.poll() is None:
                os.killpg(broker.pid, signal.SIGTERM)
                broker.wait(timeout=30)
        delete_own_simulators(run_id)
        write_host_crash_snapshot(args.output, crash_since_epoch)
    total = time.monotonic() - started
    platforms = list(results)
    repeat_walls = phased_repeat_walls(results, phase_labels, args.repeat)
    coverage_complete = args.tier != "full" or combined_full_coverage(args.output, platforms)
    report = {"tier": args.tier, "baseBudgetSeconds": BUDGET[args.tier], "budgetSeconds": budget_seconds,
              "orchestrationBudgetScale": budget_seconds / BUDGET[args.tier], "wallSeconds": round(total, 3),
              "maxActiveShards": args.max_active_shards,
              "shardTimeoutSeconds": args.shard_timeout_seconds,
              "phaseActiveShards": [sum(LANE_ACTIVE_SHARDS[str(spec["label"])] for spec in phase)
                                    for phase in phases],
              "perRepeatWallSeconds": [round(value, 3) for value in repeat_walls],
              "withinBudget": len(repeat_walls) == args.repeat and all(value < budget_seconds for value in repeat_walls),
              "repeatCoverageComplete": all(len(platform["tests"]) == args.repeat for platform in results.values()),
              "repeatQualificationFailed": repeat_failure.is_set(),
              "coverageComplete": coverage_complete,
              "phase2Cancelled": phase_two_cancelled,
              "phaseGateError": phase_gate_error,
              "runCancelled": run_cancelled or CANCELLED.is_set() or any(
                  result.get("runCancelled") for result in results.values()
              ),
              "runCancelReason": "signal" if run_cancelled or CANCELLED.is_set() or any(
                  result.get("runCancelled") for result in results.values()
              ) else None,
              "platforms": results}
    (args.output / f"{args.tier}-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    passed = qualification_passed(report, results, platforms)
    if not passed or any(platform["buildExit"] for platform in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
