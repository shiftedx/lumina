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
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APPLE = ROOT / "apple"
SMOKE = [
    "NativeFeaturedTitlePreservesDetailsAndResumesPlaybackTests",
    "NativeAudioAlbumTrackPlaybackReportsProgressTests",
    "NativeShowSeasonEpisodeBrowsingAndPlaybackTests",
    "NativeNativePlaybackReportsResumeAndConversionProgressTests",
]
IOS_FULL = SMOKE + [
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


def command(*parts: str, cwd: Path = ROOT, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(parts, cwd=cwd, text=True, capture_output=capture, check=False)


def latest_runtime(platform: str) -> str:
    data = json.loads(command("xcrun", "simctl", "list", "runtimes", "-j", capture=True).stdout)
    prefix = f"com.apple.CoreSimulator.SimRuntime.{platform}-"
    required = device_type(platform)
    candidates = [entry for entry in data["runtimes"] if entry.get("isAvailable")
                  and entry["identifier"].startswith(prefix)
                  and any(device["identifier"] == required for device in entry.get("supportedDeviceTypes", []))]
    if not candidates:
        raise RuntimeError(f"no available {platform} simulator runtime")
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


def wait_for_load(seconds: int, ceiling: float, purpose: str) -> float:
    started = time.monotonic()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if os.getloadavg()[0] < ceiling:
            return time.monotonic() - started
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for load1 < {ceiling:g} before {purpose}")


def run_xcode(arguments: list[str], log: Path) -> tuple[int, float]:
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
                if load >= 20:
                    stream.write(f"\nHost load1 exceeded 20 ({load:.2f}); cancelling this owned run.\n")
                    stream.flush()
                    raise RuntimeError("host load exceeded the qualification ceiling")
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
    return process.returncode, time.monotonic() - started


def result_ok(result: Path, log: Path, expected_count: int) -> bool:
    text = log.read_text(encoding="utf-8", errors="replace")
    if "** TEST SUCCEEDED **" not in text or "skipped" in text.lower():
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


def boot_simulators(platform: str, run_id: str, host_wait_seconds: int) -> tuple[list[str], float, float]:
    started = time.monotonic()
    waited = 0.0
    simulators: list[str] = []
    for number in range(1, 3):
        waited += wait_for_load(host_wait_seconds, 20, f"booting {platform} simulator {number}")
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
    return simulators, time.monotonic() - started, waited


def xctestrun_path(derived: Path, scheme: str) -> Path:
    paths = sorted(derived.glob("Build/Products/*.xctestrun"))
    matching = [path for path in paths if scheme in path.name]
    if len(matching) == 1:
        return matching[0]
    if len(paths) == 1:
        return paths[0]
    raise RuntimeError(f"expected one {scheme} xctestrun under {derived}, found {len(matching) or len(paths)}")


def one_platform(platform: str, tier: str, repeat: int, output: Path, broker_file: Path, run_id: str,
                 host_wait_seconds: int, derived_root: Path | None = None) -> dict[str, object]:
    scheme = f"Lumina-{platform}"
    derived = (derived_root or output) / f"derived-{platform}"
    build_log = output / f"{platform}-build.log"
    simulators: list[str] = []
    try:
        simulators, setup_seconds, setup_wait_seconds = boot_simulators(platform, run_id, host_wait_seconds)
        base = ["xcodebuild", "-jobs", "2", "-project", "Lumina.xcodeproj", "-scheme", scheme,
                "-destination", f"platform={platform} Simulator,id={simulators[0]}",
                "-derivedDataPath", str(derived), f"LUMINA_NATIVE_BROKER_FILE={broker_file}"]
        build_wait_seconds = wait_for_load(host_wait_seconds, 20, f"building {platform}")
        code, build_seconds = run_xcode([*base, "build-for-testing"], build_log)
        outcome: dict[str, object] = {
            "setupSeconds": round(setup_seconds, 3), "setupWaitSeconds": round(setup_wait_seconds, 3),
            "buildSeconds": round(build_seconds, 3), "buildWaitSeconds": round(build_wait_seconds, 3),
            "buildExit": code, "tests": [],
        }
        if code:
            return outcome
        xctestrun = xctestrun_path(derived, scheme)
        selected = selectors(platform, tier)
        shards = split_shards(selected)
        for index in range(repeat):
            test_wait_seconds = wait_for_load(host_wait_seconds, 10, f"starting {platform} repeat {index + 1}")
            started = time.monotonic()
            def run_shard(number: int) -> dict[str, object]:
                result = output / f"{platform}-{tier}-{index + 1}-shard-{number + 1}.xcresult"
                log = output / f"{platform}-{tier}-{index + 1}-shard-{number + 1}.log"
                test = ["xcodebuild", "test-without-building", "-xctestrun", str(xctestrun),
                        "-destination", f"platform={platform} Simulator,id={simulators[number]}",
                        "-parallel-testing-enabled", "NO", "-collect-test-diagnostics", "never",
                        "-resultBundlePath", str(result), *shards[number]]
                code, seconds = run_xcode(test, log)
                return {"shard": number + 1, "tests": len(shards[number]), "seconds": round(seconds, 3),
                        "exit": code, "passed": code == 0 and result_ok(result, log, len(shards[number]))}
            with ThreadPoolExecutor(max_workers=2) as pool:
                shard_results = list(pool.map(run_shard, range(2)))
            outcome["tests"].append({"repeat": index + 1, "seconds": round(time.monotonic() - started, 3),
                                     "waitSeconds": round(test_wait_seconds, 3), "shards": shard_results,
                                     "passed": all(item["passed"] for item in shard_results)})
        return outcome
    finally:
        for simulator in simulators:
            command("xcrun", "simctl", "shutdown", simulator)
            command("xcrun", "simctl", "delete", simulator)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=BUDGET, required=True)
    parser.add_argument("--platform", choices=("iOS", "tvOS", "all"), default="all")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--host-wait-seconds", type=int, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--derived-data", type=Path, help="reuse this run-owned build root for warm qualification")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be a fresh empty private directory")
    args.output.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(args.output, 0o700)
    run_id = f"{os.getpid()}-{int(time.time())}"
    broker_file = args.output / "broker.json"
    broker = subprocess.Popen([sys.executable, str(APPLE / "scripts" / "ui_suite.py"), "--output", str(broker_file)], cwd=ROOT, start_new_session=True)
    started = time.monotonic()
    results: dict[str, object] = {}
    try:
        deadline = time.monotonic() + 15
        while not broker_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not broker_file.exists():
            raise RuntimeError("fixture broker did not start")
        platforms = [args.platform] if args.platform != "all" else ["iOS", "tvOS"]
        results = {platform: one_platform(platform, args.tier, args.repeat, args.output, broker_file, run_id, args.host_wait_seconds, args.derived_data) for platform in platforms}
    finally:
        if broker.poll() is None:
            os.killpg(broker.pid, signal.SIGTERM)
            broker.wait(timeout=30)
        delete_own_simulators(run_id)
    total = time.monotonic() - started
    repeat_walls = [sum(platform["tests"][index]["seconds"] + (platform["setupSeconds"] if index == 0 else 0)
                        for platform in results.values() if platform["buildExit"] == 0 and len(platform["tests"]) > index)
                    for index in range(args.repeat)]
    report = {"tier": args.tier, "budgetSeconds": BUDGET[args.tier], "wallSeconds": round(total, 3),
              "perRepeatWallSeconds": [round(value, 3) for value in repeat_walls],
              "withinBudget": len(repeat_walls) == args.repeat and all(value < BUDGET[args.tier] for value in repeat_walls),
              "platforms": results}
    (args.output / f"{args.tier}-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    passed = report["withinBudget"] and all(test["passed"] for platform in results.values() if platform["buildExit"] == 0 for test in platform["tests"])
    if not passed or any(platform["buildExit"] for platform in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
