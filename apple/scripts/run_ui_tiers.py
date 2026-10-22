#!/usr/bin/env python3
"""Run isolated native UI tiers without touching another developer's simulators.

Each invocation creates its own simulator and broker, serializes xcodebuild on
this Mac, and removes only identifiers and processes it created.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[2]
APPLE = ROOT / "apple"
XCODE_LOCK = Path("/tmp/lumina-xcodebuild.lock")
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


@contextmanager
def xcode_slot(timeout: int = 1800) -> Iterator[None]:
    deadline = time.monotonic() + timeout
    acquired = False
    while time.monotonic() < deadline:
        if os.getloadavg()[0] < 20:
            try:
                XCODE_LOCK.mkdir()
                acquired = True
                break
            except FileExistsError:
                pass
        time.sleep(2)
    if not acquired:
        raise RuntimeError("timed out waiting for load1 <20 and this runner's lock")
    try:
        yield
    finally:
        XCODE_LOCK.rmdir()


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


def create_simulator(platform: str, run_id: str) -> str:
    name = f"Lumina UI tier {run_id} {platform}"
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


def wait_for_quiet_host(seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if os.getloadavg()[0] < 20:
            return
        time.sleep(2)
    raise RuntimeError("timed out waiting for a quiet host")


def run_xcode(arguments: list[str], log: Path, host_wait_seconds: int) -> tuple[int, float]:
    started = time.monotonic()
    # User authorized concurrent Xcode jobs from other agents; retain the load gate.
    wrapped = [sys.executable, str(Path(__file__).resolve()), "--wait-host-then-exec", str(host_wait_seconds), *arguments]
    with xcode_slot(host_wait_seconds):
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(wrapped, cwd=APPLE, text=True, stdout=stream, stderr=subprocess.STDOUT,
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


def one_platform(platform: str, tier: str, repeat: int, output: Path, broker_file: Path, run_id: str,
                 host_wait_seconds: int) -> dict[str, object]:
    simulator = create_simulator(platform, run_id)
    scheme = f"Lumina-{platform}"
    derived = output / f"derived-{platform}"
    build_log = output / f"{platform}-build.log"
    try:
        base = ["xcodebuild", "-jobs", "2", "-project", "Lumina.xcodeproj", "-scheme", scheme,
                "-destination", f"platform={platform} Simulator,id={simulator}",
                "-derivedDataPath", str(derived), f"LUMINA_NATIVE_BROKER_FILE={broker_file}"]
        code, build_seconds = run_xcode([*base, "build-for-testing"], build_log, host_wait_seconds)
        outcome: dict[str, object] = {"buildSeconds": round(build_seconds, 3), "buildExit": code, "tests": []}
        if code:
            return outcome
        for index in range(repeat):
            result = output / f"{platform}-{tier}-{index + 1}.xcresult"
            log = output / f"{platform}-{tier}-{index + 1}.log"
            test = [*base, "-parallel-testing-enabled", "YES", "-parallel-testing-worker-count", "2",
                    "-collect-test-diagnostics", "never", "-resultBundlePath", str(result), *selectors(platform, tier), "test-without-building"]
            code, seconds = run_xcode(test, log, host_wait_seconds)
            passed = code == 0 and result_ok(result, log, len(selectors(platform, tier)))
            outcome["tests"].append({"repeat": index + 1, "seconds": round(seconds, 3), "exit": code, "passed": passed})
        return outcome
    finally:
        command("xcrun", "simctl", "shutdown", simulator)
        command("xcrun", "simctl", "delete", simulator)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--wait-host-then-exec":
        wait_for_quiet_host(int(sys.argv[2]))
        os.execvp(sys.argv[3], sys.argv[3:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=BUDGET, required=True)
    parser.add_argument("--platform", choices=("iOS", "tvOS", "all"), default="all")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--host-wait-seconds", type=int, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    args.output.mkdir(parents=True, mode=0o700)
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
        results = {platform: one_platform(platform, args.tier, args.repeat, args.output, broker_file, run_id, args.host_wait_seconds) for platform in platforms}
    finally:
        if broker.poll() is None:
            os.killpg(broker.pid, signal.SIGTERM)
            broker.wait(timeout=30)
        delete_own_simulators(run_id)
    total = time.monotonic() - started
    repeat_walls = [sum(platform["tests"][index]["seconds"] for platform in results.values() if platform["buildExit"] == 0 and len(platform["tests"]) > index) for index in range(args.repeat)]
    report = {"tier": args.tier, "budgetSeconds": BUDGET[args.tier], "wallSeconds": round(total, 3), "perRepeatWallSeconds": repeat_walls, "withinBudget": len(repeat_walls) == args.repeat and all(value < BUDGET[args.tier] for value in repeat_walls), "platforms": results}
    (args.output / f"{args.tier}-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    passed = report["withinBudget"] and all(test["passed"] for platform in results.values() if platform["buildExit"] == 0 for test in platform["tests"])
    if not passed or any(platform["buildExit"] for platform in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
