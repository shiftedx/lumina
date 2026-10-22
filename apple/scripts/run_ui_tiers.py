#!/usr/bin/env python3
"""Run isolated native UI tiers without touching another developer's simulators.

Each invocation creates its own simulators and broker, runs two explicit test
shards in parallel, and removes only identifiers and processes it created.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APPLE = ROOT / "apple"
SMOKE = [
    "NativeAudioAlbumTrackPlaybackReportsProgressTests",
    "NativeNativePlaybackReportsResumeAndConversionProgressTests",
]
IOS_FULL = [
    "NativeFeaturedTitlePreservesDetailsAndResumesPlaybackTests",
    "NativeAudioAlbumTrackPlaybackReportsProgressTests",
    "NativeShowSeasonEpisodeBrowsingAndPlaybackTests",
    "NativeNativePlaybackReportsResumeAndConversionProgressTests",
    "NativeNativeBooksLibraryBrowsesShelvesAndMangaStacksTests",
    "NativeNativeBooksReadTurnResizeAndRestoreAllFormatsTests",
    "NativeNativeBooksKeepPhysicalRightInRTLLocaleTests",
    "NativeAccountTabAppearancePreservesHomeSelectionAndCompactHeaderTests",
    "NativeLibraryPullRevealsSearchAndRefreshesProgressTests",
    "NativeMusicPlaylistsSmartSelectionAndDownloadedPlaybackTests",
    "NativeMusicAdvancedControlsPausedRestoreAndQueueConflictTests",
    "NativeVideoWatchedFavoriteAndActivityFiltersTests",
    "NativeOriginalMKVReportsPeriodicProgressAndResumesTests",
    "NativeCompatibleHLSReplayRequestsFreshSessionTests",
    "NativeCurrentDuoLayoutKeepsControlsAndPlaybackReachableTests",
]
TVOS_FULL = [name for name in IOS_FULL if name not in {
    "NativeNativeBooksLibraryBrowsesShelvesAndMangaStacksTests",
    "NativeNativeBooksReadTurnResizeAndRestoreAllFormatsTests",
    "NativeNativeBooksKeepPhysicalRightInRTLLocaleTests",
    "NativeAccountTabAppearancePreservesHomeSelectionAndCompactHeaderTests",
    "NativeLibraryPullRevealsSearchAndRefreshesProgressTests",
    "NativeCompatibleHLSReplayRequestsFreshSessionTests",
    "NativeCurrentDuoLayoutKeepsControlsAndPlaybackReachableTests",
}]
BUDGET = {"smoke": 600, "full": 2700}
IOS_RUNTIME: str | None = None


def command(*parts: str, cwd: Path = ROOT, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(parts, cwd=cwd, text=True, capture_output=capture, check=False)


def latest_runtime(platform: str) -> str:
    data = json.loads(command("xcrun", "simctl", "list", "runtimes", "-j", capture=True).stdout)
    prefix = f"com.apple.CoreSimulator.SimRuntime.{platform}-"
    required = device_type(platform)
    candidates = [entry for entry in data["runtimes"] if entry.get("isAvailable")
                  and entry["identifier"].startswith(prefix)
                  and any(device["identifier"] == required for device in entry.get("supportedDeviceTypes", []))]
    if platform == "iOS" and IOS_RUNTIME:
        candidates = [entry for entry in candidates if entry["version"] == IOS_RUNTIME]
    if not candidates:
        raise RuntimeError(f"no compatible available {platform} simulator runtime")
    return max(candidates, key=lambda entry: tuple(int(part) for part in entry["version"].split(".")))["identifier"]


def device_type(platform: str) -> str:
    wanted = "iPad Pro 11-inch (M5)" if platform == "iOS" else "Apple TV 4K (3rd generation)"
    data = json.loads(command("xcrun", "simctl", "list", "devicetypes", "-j", capture=True).stdout)
    for entry in data["devicetypes"]:
        if entry["name"] == wanted:
            return entry["identifier"]
    raise RuntimeError(f"required simulator type unavailable: {wanted}")


def create_simulator(platform: str, run_id: str, number: int) -> str:
    name = f"Lumina UI tier {run_id} {platform} {number}"
    result = command("xcrun", "simctl", "create", name, device_type(platform), latest_runtime(platform), capture=True)
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


def run_xcode(arguments: list[str], log: Path) -> tuple[int, float, float, float]:
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(arguments, cwd=APPLE, text=True, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + 3000
            peak_load = 0.0
            while process.poll() is None:
                load = os.getloadavg()[0]
                peak_load = max(peak_load, load)
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(arguments, 3000)
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


def selectors(platform: str, tier: str) -> list[str]:
    target = f"Lumina-{platform}-UITests"
    selected = SMOKE if tier == "smoke" else (IOS_FULL if platform == "iOS" else TVOS_FULL)
    return [f"-only-testing:{target}/{name}" for name in selected]


def split_shards(values: list[str], count: int = 2) -> list[list[str]]:
    return [values[index::count] for index in range(count)]


def broker_config_path(output: Path, platform: str) -> Path:
    return output / f"{platform}-broker.json"


def start_broker(output: Path, platform: str) -> tuple[subprocess.Popen[bytes], Path]:
    config = broker_config_path(output, platform)
    broker = subprocess.Popen([sys.executable, str(APPLE / "scripts" / "ui_suite.py"), "--output", str(config)],
                              cwd=ROOT, start_new_session=True)
    deadline = time.monotonic() + 15
    while not config.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not config.exists():
        os.killpg(broker.pid, signal.SIGTERM)
        broker.wait(timeout=30)
        raise RuntimeError(f"{platform} fixture broker did not start")
    return broker, config


def wait_for_cohort(cohort: threading.Barrier | None, timeout: int) -> None:
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


def boot_simulators(platform: str, run_id: str) -> tuple[list[str], float, float]:
    started = time.monotonic()
    waited = 0.0
    simulators: list[str] = []
    for number in range(1, 3):
        simulator = create_simulator(platform, run_id, number)
        simulators.append(simulator)
        # Fresh iOS homes otherwise eagerly render stock widgets and index an empty library.
        # Lumina has no WidgetKit, Photos or CoreSpotlight integration under test.
        background = [f"--disabledJob={job}" for job in (
            "com.apple.chronod", "com.apple.photoanalysisd", "com.apple.corespotlightservice",
        )] if platform == "iOS" else []
        boot = command("xcrun", "simctl", "boot", simulator, *background, capture=True)
        if boot.returncode:
            raise RuntimeError(boot.stderr.strip() or f"could not boot {platform} simulator {number}")
        status = subprocess.run(["xcrun", "simctl", "bootstatus", simulator, "-b"],
                                cwd=ROOT, text=True, capture_output=True, timeout=180)
        if status.returncode:
            raise RuntimeError(status.stderr.strip() or f"{platform} simulator {number} did not finish booting")
    return simulators, time.monotonic() - started - waited, waited


def xctestrun_path(derived: Path, scheme: str) -> Path:
    paths = sorted(derived.glob("Build/Products/*.xctestrun"))
    matching = [path for path in paths if scheme in path.name]
    if len(matching) == 1:
        return matching[0]
    if len(paths) == 1:
        return paths[0]
    raise RuntimeError(f"expected one {scheme} xctestrun under {derived}, found {len(matching) or len(paths)}")


def one_platform(platform: str, tier: str, repeat: int, output: Path, broker_file: Path, run_id: str,
                 derived_root: Path | None = None, cohort: threading.Barrier | None = None) -> dict[str, object]:
    scheme = f"Lumina-{platform}"
    derived = (derived_root or output) / f"derived-{platform}"
    build_log = output / f"{platform}-build.log"
    simulators: list[str] = []
    try:
        setup_started = time.monotonic()
        simulators, setup_seconds, setup_wait_seconds = boot_simulators(platform, run_id)
        setup_finished = time.monotonic()
        base = ["xcodebuild", "-jobs", "2", "-project", "Lumina.xcodeproj", "-scheme", scheme,
                "-destination", f"platform={platform} Simulator,id={simulators[0]}",
                "-derivedDataPath", str(derived), f"LUMINA_NATIVE_BROKER_FILE={broker_file}"]
        build_wait_seconds = 0.0
        code, build_seconds, _, _ = run_xcode([*base, "build-for-testing"], build_log)
        outcome: dict[str, object] = {
            "setupSeconds": round(setup_seconds, 3), "setupWaitSeconds": round(setup_wait_seconds, 3),
            "setupStartedMonotonic": setup_started, "setupFinishedMonotonic": setup_finished,
            "buildSeconds": round(build_seconds, 3), "buildWaitSeconds": round(build_wait_seconds, 3),
            "buildExit": code, "runtime": latest_runtime(platform), "tests": [],
        }
        if code:
            if cohort is not None:
                cohort.abort()
            return outcome
        xctestrun = xctestrun_path(derived, scheme)
        selected = selectors(platform, tier)
        shards = split_shards(selected)
        for index in range(repeat):
            wait_for_cohort(cohort, BUDGET[tier] + 600)
            test_wait_seconds = 0.0
            started = time.monotonic()
            def run_shard(number: int) -> dict[str, object]:
                result = output / f"{platform}-{tier}-{index + 1}-shard-{number + 1}.xcresult"
                log = output / f"{platform}-{tier}-{index + 1}-shard-{number + 1}.log"
                test = ["xcodebuild", "test-without-building", "-xctestrun", str(xctestrun),
                        "-destination", f"platform={platform} Simulator,id={simulators[number]}",
                        "-parallel-testing-enabled", "NO", "-collect-test-diagnostics", "never",
                        "-resultBundlePath", str(result), *shards[number]]
                code, seconds, shard_started, shard_finished = run_xcode(test, log)
                return {"shard": number + 1, "tests": len(shards[number]), "seconds": round(seconds, 3),
                        "startedMonotonic": shard_started, "finishedMonotonic": shard_finished,
                        "exit": code, "passed": code == 0 and result_ok(result, log, len(shards[number]))}
            with ThreadPoolExecutor(max_workers=2) as pool:
                shard_results = list(pool.map(run_shard, range(2)))
            outcome["tests"].append({"repeat": index + 1, "seconds": round(time.monotonic() - started, 3),
                                     "startedMonotonic": min(item["startedMonotonic"] for item in shard_results),
                                     "finishedMonotonic": max(item["finishedMonotonic"] for item in shard_results),
                                     "waitSeconds": round(test_wait_seconds, 3), "shards": shard_results,
                                     "passed": all(item["passed"] for item in shard_results)})
            wait_for_cohort(cohort, BUDGET[tier] + 600)
        return outcome
    except BaseException:
        if cohort is not None:
            cohort.abort()
        raise
    finally:
        for simulator in simulators:
            command("xcrun", "simctl", "shutdown", simulator)
            command("xcrun", "simctl", "delete", simulator)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=BUDGET, required=True)
    parser.add_argument("--platform", choices=("iOS", "tvOS", "all"), default="all")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--ios-runtime", help="pin an installed iPadOS runtime version for qualification")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--derived-data", type=Path, help="reuse this run-owned build root for warm qualification")
    args = parser.parse_args()
    global IOS_RUNTIME
    IOS_RUNTIME = args.ios_runtime
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be a fresh empty private directory")
    args.output.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(args.output, 0o700)
    run_id = f"{os.getpid()}-{int(time.time())}"
    started = time.monotonic()
    results: dict[str, dict[str, object]] = {}
    brokers: list[subprocess.Popen[bytes]] = []
    try:
        platforms = [args.platform] if args.platform != "all" else ["tvOS", "iOS"]
        broker_files: dict[str, Path] = {}
        for platform in platforms:
            broker, config = start_broker(args.output, platform)
            brokers.append(broker)
            broker_files[platform] = config
        cohort = threading.Barrier(len(platforms)) if len(platforms) > 1 else None
        with ThreadPoolExecutor(max_workers=len(platforms)) as pool:
            futures = {
                platform: pool.submit(one_platform, platform, args.tier, args.repeat, args.output,
                                      broker_files[platform], run_id, args.derived_data, cohort)
                for platform in platforms
            }
            for platform, future in futures.items():
                results[platform] = future.result()
    finally:
        for broker in brokers:
            if broker.poll() is None:
                os.killpg(broker.pid, signal.SIGTERM)
                broker.wait(timeout=30)
        delete_own_simulators(run_id)
    total = time.monotonic() - started
    repeat_walls = per_repeat_walls(results, platforms, args.repeat)
    report = {"tier": args.tier, "budgetSeconds": BUDGET[args.tier], "wallSeconds": round(total, 3),
              "perRepeatWallSeconds": [round(value, 3) for value in repeat_walls],
              "withinBudget": len(repeat_walls) == args.repeat and all(value < BUDGET[args.tier] for value in repeat_walls),
              "platforms": results}
    (args.output / f"{args.tier}-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    passed = (set(results) == set(platforms) and report["withinBudget"]
              and all(test["passed"] for platform in results.values() if platform["buildExit"] == 0 for test in platform["tests"]))
    if not passed or any(platform["buildExit"] for platform in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
