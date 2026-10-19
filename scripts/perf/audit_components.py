"""Run existing finite media component benchmarks, retaining logs and failures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

PERF = Path(__file__).resolve().parent
ROOT = PERF.parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip-titles", action="store_true", help="production containers lack the pytest driver")
    parser.add_argument("--titles-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    results = []

    def run(name: str, command: list[str], *, env=None, json_stdout: bool = False) -> None:
        started = time.monotonic()
        with (args.out / f"{name}.log").open("w") as log:
            try:
                completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
                status = completed.returncode
            except subprocess.TimeoutExpired:
                status = 124
        results.append({"name": name, "exit_code": status, "wall_seconds": time.monotonic() - started})
        if status == 0 and json_stdout:
            lines = (args.out / f"{name}.log").read_text().splitlines()
            try:
                payload = json.loads(lines[-1])
                (args.out / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
            except (ValueError, IndexError):
                results[-1]["output_parse_error"] = True
        (args.out / "execution.json").write_text(json.dumps(results, indent=2) + "\n")
        print(f"{name}: exit {status}", flush=True)

    if not args.titles_only:
        run("media-stream", [sys.executable, str(PERF / "media_stream_bench.py"), "--size-mib", "64", "--count", "20"], json_stdout=True)
        run("renditions", [sys.executable, str(PERF / "rendition_bench.py"), "--count", "20"], json_stdout=True)
        for scenario in ("relist", "remove"):
            run(f"watch-{scenario}", [sys.executable, str(PERF / "watch_relist.py"), "--scenario", scenario, "--output", str(args.out / f"watch-{scenario}.json")])
        for name in ("media_services", "resources"):
            script = PERF / f"audit_{name}.py"
            if script.exists():
                run(name, [sys.executable, str(script), "--out", str(args.out / f"{name}.json")])
            else:
                results.append({"name": name, "blocked": "benchmark helper absent"})
    if not args.skip_titles:
        import os

        with tempfile.TemporaryDirectory(prefix="lumina-audit-titles-") as temporary:
            data = Path(temporary) / "fixture"
            with (args.out / "titles-seed.log").open("w") as log:
                seed = subprocess.run([sys.executable, str(PERF / "seed_titles.py"), str(data)], stdout=log, stderr=subprocess.STDOUT, timeout=300)
            if seed.returncode == 0:
                tests = [f"backend/tests/perf/test_{part}_budgets.py" for part in ("title", "reco", "reco_title", "live_youtube")]
                run("titles", [sys.executable, "-m", "pytest", *tests, "-q", "-s", "-p", "no:cacheprovider", "-p", "audit_pytest_plugin", "--durations=0", f"--junitxml={args.out / 'titles.xml'}"],
                    env={**os.environ, "PERF_TITLES_ROOT": str(data), "PYTHONPATH": os.pathsep.join([str(ROOT / "backend"), str(PERF)]),
                         "AUDIT_TITLE_REQUESTS": str(args.out / "title-requests.jsonl")})
            else:
                results.append({"name": "titles", "exit_code": seed.returncode, "phase": "seed"})
    (args.out / "execution.json").write_text(json.dumps(results, indent=2) + "\n")
    return int(any(row.get("exit_code", 0) or row.get("output_parse_error") for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
