#!/usr/bin/env python3
"""Run backend pytest tiers in isolated xdist workers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from bootstrap import REPOSITORY, virtualenv_python

MANIFEST = REPOSITORY / "backend" / "tests" / "pytest-tiers.json"
BUDGETS = {"smoke": 600, "full": 2700}
GRACE_SECONDS = 10


class RunError(RuntimeError):
    pass


def private_directory(path: Path | None) -> Path:
    if path is None:
        root = Path(tempfile.mkdtemp(prefix="lumina-backend-tests-"))
    else:
        root = path.resolve()
        if root.exists() and any(root.iterdir()):
            raise RunError(f"output directory must be empty: {root}")
        root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def group_exists(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    return True


def stop_owned_group(process: subprocess.Popen[object]) -> None:
    """Stop the detached pytest process group created by this runner only."""
    group = process.pid
    try:
        os.killpg(group, signal.SIGINT)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + GRACE_SECONDS
    while group_exists(group) and time.monotonic() < deadline:
        time.sleep(0.05)
    if group_exists(group):
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def child(command: list[str], *, cwd: Path, env: dict[str, str], log: Path, timeout: float) -> int:
    """Run a command in its own process group and bound its wall time."""
    with log.open("w") as output:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=max(timeout, 0))
        except KeyboardInterrupt:
            stop_owned_group(process)
            raise
        except subprocess.TimeoutExpired:
            stop_owned_group(process)
            raise RunError(f"timed out after {timeout:.1f}s: {' '.join(command)}")


def collected_nodeids(log: Path) -> list[str]:
    return [line.strip() for line in log.read_text().splitlines() if line.startswith("tests/") and "::" in line]


def junit_cases(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise RunError(f"pytest did not write JUnit XML: {path}")
    cases: list[dict[str, object]] = []
    for testcase in ET.parse(path).iter("testcase"):
        file, name = testcase.get("file"), testcase.get("name")
        if not file or not name:
            raise RunError("JUnit xunit1 testcase lacks file or name")
        skipped = testcase.find("skipped")
        if skipped is not None:
            outcome = "skipped"
            reason = skipped.get("message") or (skipped.text or "").strip()
        elif testcase.find("failure") is not None:
            outcome, reason = "failure", ""
        elif testcase.find("error") is not None:
            outcome, reason = "error", ""
        else:
            outcome, reason = "passed", ""
        cases.append(
            {
                "nodeid": f"{file}::{name}",
                "outcome": outcome,
                "reason": reason,
                "seconds": float(testcase.get("time", "0")),
            }
        )
    return cases


def validate_cases(
    selected: list[str], cases: list[dict[str, object]], expected_skips: list[dict[str, str]]
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Fail closed if collection, xdist results, or allowed skips diverge."""
    got = [str(case["nodeid"]) for case in cases]
    if not selected or len(selected) != len(set(selected)):
        raise RunError("collection did not produce a unique, nonempty node list")
    if not got or len(got) != len(set(got)) or len(got) != len(selected) or set(got) != set(selected):
        raise RunError(f"JUnit identity mismatch: selected={len(selected)} results={len(got)}")
    failures = [case for case in cases if case["outcome"] in {"failure", "error"}]
    skipped = [
        {"nodeid": str(case["nodeid"]), "reason": str(case["reason"])}
        for case in cases
        if case["outcome"] == "skipped"
    ]
    allowed = {(row["nodeid"], row["reason"]) for row in expected_skips}
    unexpected = sorted(
        (row["nodeid"], row["reason"]) for row in skipped if (row["nodeid"], row["reason"]) not in allowed
    )
    if unexpected:
        raise RunError(f"unexpected skips: {unexpected}")
    if failures:
        raise RunError(f"pytest reported failures/errors: {len(failures)}")
    return failures, skipped


def selected_manifest(tier: str) -> tuple[list[str], list[dict[str, str]]]:
    document = json.loads(MANIFEST.read_text())
    entry = document.get("tiers", {}).get(tier)
    if not isinstance(entry, dict):
        raise RunError(f"missing tier in {MANIFEST}: {tier}")
    selectors = entry.get("selectors")
    skips = entry.get("expected_skips", [])
    if not isinstance(selectors, list) or not selectors or not all(isinstance(selector, str) for selector in selectors):
        raise RunError(f"{tier} needs nonempty string selectors")
    if not isinstance(skips, list) or not all(
        isinstance(skip, dict) and isinstance(skip.get("nodeid"), str) and isinstance(skip.get("reason"), str)
        for skip in skips
    ):
        raise RunError(f"{tier} expected_skips must contain nodeid and reason")
    return selectors, skips


def one_run(
    *,
    python: Path,
    root: Path,
    tier: str,
    workers: int,
    ordinal: int,
    selectors: list[str],
    expected_skips: list[dict[str, str]],
) -> dict[str, object]:
    started = time.monotonic()
    run = root / f"run-{ordinal}"
    run.mkdir(mode=0o700)
    app_root = run / "app-data"
    app_root.mkdir(mode=0o700)
    # Some safety contracts require pytest tmp_path to live below the platform
    # temporary directory, which is not guaranteed for a caller-supplied report root.
    basetemp = Path(tempfile.mkdtemp(prefix=f"lumina-pytest-{ordinal}-"))
    basetemp.chmod(0o700)
    environment = os.environ.copy() | {"PY_COLORS": "0", "LUMINA_DATA_DIR": str(app_root)}
    collection_log = run / "collection.log"
    pytest_log = run / "pytest.log"
    junit = run / "junit.xml"
    result: dict[str, object]
    selected: list[str] = []
    cases: list[dict[str, object]] = []
    try:
        remaining = BUDGETS[tier] - (time.monotonic() - started)
        collect = [
            str(python),
            "-m",
            "pytest",
            *selectors,
            "--collect-only",
            "-q",
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
        ]
        if child(collect, cwd=REPOSITORY / "backend", env=environment, log=collection_log, timeout=remaining) != 0:
            raise RunError("collection failed; see collection.log")
        selected = collected_nodeids(collection_log)
        if not selected or len(selected) != len(set(selected)):
            raise RunError("collection did not produce a unique, nonempty node list")
        remaining = BUDGETS[tier] - (time.monotonic() - started)
        command = [
            str(python),
            "-m",
            "pytest",
            *selectors,
            "-q",
            "-n",
            str(workers),
            "--dist",
            "worksteal",
            "-p",
            "no:cacheprovider",
            "-o",
            "junit_family=xunit1",
            f"--junitxml={junit}",
            f"--basetemp={basetemp}",
        ]
        exit_code = child(command, cwd=REPOSITORY / "backend", env=environment, log=pytest_log, timeout=remaining)
        cases = junit_cases(junit)
        failures, skipped = validate_cases(selected, cases, expected_skips)
        if exit_code != 0:
            raise RunError(f"pytest failed: exit={exit_code} failures={len(failures)}")
        shutil.rmtree(app_root)
        shutil.rmtree(basetemp)
        wall = time.monotonic() - started
        if wall > BUDGETS[tier]:
            raise RunError(f"{tier} exceeded {BUDGETS[tier]}s budget: {wall:.3f}s")
        result = {
            "ordinal": ordinal,
            "passed": True,
            "wallSeconds": wall,
            "budgetSeconds": BUDGETS[tier],
            "selected": selected,
            "counts": {
                "selected": len(selected),
                "passed": len(selected) - len(skipped),
                "skipped": len(skipped),
                "failed": 0,
                "errors": 0,
            },
            "skipped": skipped,
            "junit": str(junit),
            "appData": str(app_root),
            "baseTemp": str(basetemp),
            "collectionLog": str(collection_log),
            "pytestLog": str(pytest_log),
        }
        (run / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    except KeyboardInterrupt:
        result = {
            "ordinal": ordinal,
            "passed": False,
            "wallSeconds": time.monotonic() - started,
            "budgetSeconds": BUDGETS[tier],
            "counts": {
                "selected": len(selected),
                "passed": sum(case["outcome"] == "passed" for case in cases),
                "skipped": sum(case["outcome"] == "skipped" for case in cases),
                "failed": sum(case["outcome"] == "failure" for case in cases),
                "errors": sum(case["outcome"] == "error" for case in cases),
            },
            "skipped": [
                {"nodeid": str(case["nodeid"]), "reason": str(case["reason"])}
                for case in cases if case["outcome"] == "skipped"
            ],
            "error": "cancelled",
            "appData": str(app_root),
            "baseTemp": str(basetemp),
            "collectionLog": str(collection_log),
            "pytestLog": str(pytest_log),
            "junit": str(junit),
        }
        (run / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        raise
    except Exception as error:
        result = {
            "ordinal": ordinal,
            "passed": False,
            "wallSeconds": time.monotonic() - started,
            "budgetSeconds": BUDGETS[tier],
            "counts": {
                "selected": len(selected),
                "passed": sum(case["outcome"] == "passed" for case in cases),
                "skipped": sum(case["outcome"] == "skipped" for case in cases),
                "failed": sum(case["outcome"] == "failure" for case in cases),
                "errors": sum(case["outcome"] == "error" for case in cases),
            },
            "skipped": [
                {"nodeid": str(case["nodeid"]), "reason": str(case["reason"])}
                for case in cases if case["outcome"] == "skipped"
            ],
            "error": str(error),
            "appData": str(app_root),
            "baseTemp": str(basetemp),
            "collectionLog": str(collection_log),
            "pytestLog": str(pytest_log),
            "junit": str(junit),
        }
        (run / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=sorted(BUDGETS), default="full")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.workers < 1 or args.repeat < 1:
        raise SystemExit("--workers and --repeat must be positive")
    if os.environ.get("PERF_TITLES_ROOT"):
        raise SystemExit("seeded performance measurements share a fixture and must run serially: use make perf-titles")
    python = virtualenv_python(REPOSITORY / "backend" / ".venv")
    if not python.is_file():
        raise SystemExit("backend dependencies are missing; run make bootstrap first")
    root: Path | None = None
    runs: list[dict[str, object]] = []
    try:
        selectors, expected_skips = selected_manifest(args.tier)
        root = private_directory(args.output)
        for ordinal in range(1, args.repeat + 1):
            result = one_run(
                python=python,
                root=root,
                tier=args.tier,
                workers=args.workers,
                ordinal=ordinal,
                selectors=selectors,
                expected_skips=expected_skips,
            )
            runs.append(result)
            if not result["passed"]:
                break
        overall = {
            "tier": args.tier,
            "workers": args.workers,
            "repeat": args.repeat,
            "budgetSeconds": BUDGETS[args.tier],
            "passed": len(runs) == args.repeat and all(bool(run["passed"]) for run in runs),
            "runs": runs,
        }
        (root / "report.json").write_text(json.dumps(overall, indent=2) + "\n")
        print(json.dumps({"passed": overall["passed"], "report": str(root / "report.json")}, indent=2))
        return 0 if overall["passed"] else 1
    except KeyboardInterrupt:
        if root is not None:
            report = {"tier": args.tier, "workers": args.workers, "repeat": args.repeat, "passed": False, "cancelled": True, "runs": runs}
            (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        return 130
    except RunError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
