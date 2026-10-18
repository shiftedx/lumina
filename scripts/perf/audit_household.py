"""Run the existing household workload at one and six members on disposable data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx

PERF = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--ui-only", action="store_true", help="run the browser workflow against a fresh seed without C1/C6 load")
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error("seconds must be positive")
    if args.ui and args.ui_only:
        parser.error("--ui and --ui-only cannot be combined")
    args.out.mkdir(parents=True, exist_ok=True)
    failed = False
    with tempfile.TemporaryDirectory(prefix="lumina-audit-household-") as temporary:
        data = Path(temporary) / "data"
        # The seed's human-readable summary contains its synthetic login password.
        subprocess.run([sys.executable, str(PERF / "seed_household.py"), str(data)], stdout=subprocess.DEVNULL, check=True)
        if args.ui_only:
            with sqlite3.connect(f"file:{data / 'app.db'}?mode=ro", uri=True) as db:
                row = db.execute("""
                    SELECT t.library_item_id
                    FROM transcripts AS t
                    JOIN library_items AS i ON i.id = t.library_item_id
                    WHERE i.visibility = 'shared'
                      AND i.status = 'available'
                      AND i.kind IN ('video', 'movie', 'episode')
                    ORDER BY t.id
                    LIMIT 1
                """).fetchone()
            if row is None:
                raise RuntimeError("household seed has no visible available video with a transcript")
            port = 5924
            base = f"http://127.0.0.1:{port}"
            destination = args.out / "ui.json"
            server = subprocess.Popen([sys.executable, str(PERF / "serve.py"), str(data), str(port)])
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError("perf backend did not start for UI audit")
                status = subprocess.run([
                    "node", str(PERF / "ui.mjs"), base, row[0], "--out", str(destination),
                ]).returncode
                if destination.exists():
                    result = json.loads(destination.read_text())
                    failed |= not result.get("ok", False)
                else:
                    failed = True
                failed |= status != 0
            finally:
                server.terminate()
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=30)
            return int(failed)
        for members in (1, 6):
            destination = args.out / f"c{members}.json"
            command = [sys.executable, str(PERF / "load.py"), str(data), "--seconds", str(args.seconds),
                       "--think", "0.02", "--members", str(members), "--port", "5924", "--json", str(destination)]
            if args.ui and members == 6:
                command.append("--ui")
            status = subprocess.run(command).returncode
            if destination.exists():
                result = json.loads(destination.read_text())
                failed |= any(row["non_2xx"] for row in result["endpoints"].values())
            else:
                failed = True
            failed |= status != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
