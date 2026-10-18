"""Bounded real-HTTP backend audit for workflow gaps outside ``make perf``.

This runner is intentionally separate from the ordinary verification gate. It
creates a disposable household fixture, starts ``audit_backend_server.py`` on
loopback, and writes raw samples plus aggregate evidence under
``output/performance-audit/backend``.  It never contacts a provider or uses
operator credentials.  Use ``scripts/perf/audit_run.py`` and the shared perf
lock for an actual timing run; see ``--help`` for the ready command.

The runner marks a cell ``blocked`` when a faithful real-HTTP workload would
need a missing script-local fake (ARR/Lidarr, HLS/DVR, or admission media). A
blocked cell is evidence of absent coverage, never a passing zero-cost result.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parents[2]
PERF = Path(__file__).resolve().parent
PASSWORD = "perf-household-password"  # synthetic household fixture only; never emitted
MIN_P95_SAMPLES = 20
MAX_SAMPLES = 30
MAX_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_OUTPUT = ROOT / "output" / "performance-audit" / "backend"


@dataclass(frozen=True)
class AuditCase:
    name: str
    category: str
    mode: str
    description: str
    blocker: str | None = None


CASES = (
    AuditCase("auth_login", "auth/policy", "http", "Synthetic login followed by session bootstrap."),
    AuditCase("visibility_policy", "auth/policy", "http", "Visible library page under the normal member predicate."),
    AuditCase("music_manifest", "music/sessions/exports", "http", "Visible track manifest serialization."),
    AuditCase("music_session_cas", "music/sessions/exports", "http", "Durable per-member listening-session revision write."),
    AuditCase("music_queue_5000", "music/sessions/exports", "http", "One bounded 5,000-entry session response; payload cap is asserted."),
    AuditCase("listening_export_ledger", "music/sessions/exports", "http", "Eligible event/replay ledger writes against a script-local accepted receipt fake."),
    AuditCase("book_reader_progress", "books/images/documents", "http", "EPUB import, reader/chapter response, and durable reading progress."),
    AuditCase("art_cold_warm", "books/images/documents", "blocked", "Artwork cache miss/hit needs generated title artwork and rendition tooling.", "Household seed has no safe image/rendition fixture."),
    AuditCase("external_import", "imports/acquisition/connectors", "http", "Bounded synthetic WAV import run and terminal result."),
    AuditCase("arr_snapshot_and_operation", "imports/acquisition/connectors", "http", "10k loopback Arr observation refresh, paged local reads, and one durable operation/reconcile."),
    AuditCase("connector_snapshot", "imports/acquisition/connectors", "http", "10k loopback Lidarr queue observation refresh and paged local reads."),
    AuditCase("live_dvr_jobs", "live/DVR/jobs", "blocked", "Loopback HLS capture, scheduler, and worker fairness under reads.", "Needs a deterministic HLS fixture plus ffmpeg availability qualification."),
    AuditCase("resource_admission", "concurrent resource admission", "blocked", "Direct/range/transcode admission and release under concurrent clients.", "Needs real eligible media and deterministic shaping; the runner will not infer admission from a unit fake."),
    AuditCase("backup_create_verify", "backups/admin", "http", "Disposable database backup creation and checksum/integrity verification."),
    AuditCase("backup_restore", "backups/admin", "offline-component", "Offline restore to a fresh disposable target after the server exits."),
)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def aggregate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [sample for sample in samples if sample.get("ok")]
    timings = [float(sample["elapsed_ms"]) for sample in successful]
    sql_counts = [int(sample["sql_count"]) for sample in successful if sample.get("sql_count") is not None]
    return {
        "n": len(samples), "successful": len(successful), "errors": len(samples) - len(successful),
        "median_ms": round(statistics.median(timings), 3) if timings else None,
        "p95_ms": round(percentile(timings, 0.95), 3) if len(timings) >= MIN_P95_SAMPLES else None,
        "p95_reason": None if len(timings) >= MIN_P95_SAMPLES else f"requires at least {MIN_P95_SAMPLES} successful samples",
        "max_ms": round(max(timings), 3) if timings else None,
        "bytes": sum(int(sample.get("bytes", 0)) for sample in successful),
        "sql_count_samples": len(sql_counts),
        "max_sql_count": max(sql_counts, default=None),
        # Raw response timings and cumulative server writer telemetry make the
        # result auditable; response bodies and credentials are never retained.
        "samples": samples,
    }


class Audit:
    def __init__(self, *, data_dir: Path, output: Path, port: int, samples: int, concurrency: tuple[int, ...]) -> None:
        self.data_dir, self.output, self.port = data_dir, output, port
        self.samples, self.concurrency = samples, concurrency
        self.base = f"http://127.0.0.1:{port}"
        self.selectors = json.loads((data_dir / "audit-selectors.json").read_text(encoding="utf-8"))
        self.server: subprocess.Popen[bytes] | None = None
        self.backup_name: str | None = None
        self.book_id: str | None = None

    def start(self) -> None:
        command = [sys.executable, str(PERF / "audit_backend_server.py"), str(self.data_dir), str(self.port)]
        self.server = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                error = (self.server.stderr.read() if self.server.stderr else b"").decode("utf-8", "replace")[-2000:]
                raise RuntimeError(f"audit server exited during startup: {error}")
            try:
                if httpx.get(self.base + "/api/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError("audit server did not become healthy within 60 seconds")

    def stop(self) -> None:
        if self.server is None or self.server.poll() is not None:
            return
        os.killpg(self.server.pid, signal.SIGTERM)
        try:
            self.server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(self.server.pid, signal.SIGKILL)
            self.server.wait(timeout=5)

    def client(self, username: str) -> httpx.Client:
        client = httpx.Client(base_url=self.base, timeout=60, headers={"Origin": self.base})
        response = client.post("/api/session/login", json={"username": username, "password": PASSWORD})
        self.expect(response, {200}, "login")
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    @staticmethod
    def expect(response: httpx.Response, expected: set[int], label: str) -> None:
        if response.status_code not in expected:
            # The fixture uses only synthetic values, but audit error evidence
            # must remain content-free if a future workload reaches a secret.
            raise RuntimeError(f"{label}: expected {sorted(expected)}, got {response.status_code}")

    def telemetry(self) -> dict[str, Any]:
        response = httpx.get(self.base + "/_perf/audit/metrics", timeout=10)
        self.expect(response, {200}, "telemetry")
        return response.json()

    def audit_counter(self, path: str) -> dict[str, Any]:
        response = httpx.get(self.base + path, timeout=10)
        self.expect(response, {200}, path)
        return response.json()

    def sample(self, label: str, call: Callable[[], httpx.Response], expected: set[int]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = call()
            self.expect(response, expected, label)
            raw_sql = response.headers.get("x-perf-queries")
            try:
                sql_count = int(raw_sql) if raw_sql is not None else None
            except ValueError:
                sql_count = None
            sample = {"ok": True, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                      "status": response.status_code, "bytes": len(response.content),
                      "sql_count": sql_count, "sql_count_status": "measured" if sql_count is not None else "unavailable"}
            if sample["bytes"] > MAX_HTTP_RESPONSE_BYTES:
                return {**sample, "ok": False, "error": "ResponseCapExceeded",
                        "detail": f"response exceeded {MAX_HTTP_RESPONSE_BYTES} bytes"}
            # The telemetry call happens after timing the workload and is not
            # included in its latency. Metrics are cumulative by write name,
            # including total/max writer-admission wait and busy timeouts.
            sample["server_after"] = self.telemetry()
            return sample
        except (httpx.HTTPError, RuntimeError) as error:
            return {"ok": False, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error": type(error).__name__, "detail": str(error)[:300]}

    def repeated(self, label: str, call: Callable[[], httpx.Response], expected: set[int], *, count: int | None = None) -> list[dict[str, Any]]:
        return [self.sample(label, call, expected) for _ in range(count or self.samples)]

    def concurrent(self, label: str, clients: list[httpx.Client], call: Callable[[httpx.Client], httpx.Response], expected: set[int]) -> list[dict[str, Any]]:
        def one(client: httpx.Client) -> dict[str, Any]:
            return self.sample(label, lambda: call(client), expected)
        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            return list(pool.map(one, clients))

    def setup_book(self, client: httpx.Client) -> None:
        if self.book_id:
            return
        source = Path(self.selectors["book_upload"])
        response = client.put("/api/books/import", params={"filename": "audit-book.epub"}, content=source.read_bytes(), headers={"Content-Type": "application/epub+zip"})
        self.expect(response, {201}, "book import")
        self.book_id = response.json()["id"]

    def run_http_cases(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        admin = self.client(self.selectors["admin"])
        member = self.client(self.selectors["members"][0])
        track = self.selectors["track_ids"][0]
        try:
            def login_then_session() -> httpx.Response:
                client = self.client(self.selectors["members"][1])
                try:
                    return client.get("/api/session/me")
                finally:
                    client.close()

            result["auth_login"] = {"case": "auth_login", "mode": "http", "runs": {
                "c1": aggregate(self.repeated("login", login_then_session, {200}))}}
            result["visibility_policy"] = {"case": "visibility_policy", "mode": "http", "runs": {
                "c1": aggregate(self.repeated("visible library", lambda: member.get("/api/library", params={"limit": 60, "kind": "track"}), {200}))}}
            result["music_manifest"] = {"case": "music_manifest", "mode": "http", "runs": {
                "c1": aggregate(self.repeated("music manifest", lambda: member.get(f"/api/music/tracks/{track}"), {200}))}}

            # One writer per member is a meaningful C6 admission measure: each operation is durable and has an independent revision.
            writers = [admin, *[self.client(name) for name in self.selectors["members"]]]
            def session_write(client: httpx.Client) -> httpx.Response:
                current = client.get("/api/me/music-session")
                self.expect(current, {200}, "music session read")
                revision = current.json()["revision"]
                return client.put("/api/me/music-session", json={"base_revision": revision, "item_ids": [track], "current_index": 0,
                    "position_seconds": 0, "repeat": "off", "shuffle": False, "active_output": {"kind": "browser"}})
            music_runs = {"c1": aggregate(self.repeated("music session", lambda: session_write(member), {200}))}
            if 6 in self.concurrency:
                music_runs["c6"] = aggregate([sample for _ in range(self.samples) for sample in self.concurrent("music session", writers, session_write, {200})])
            def session_conflict() -> httpx.Response:
                current = member.get("/api/me/music-session"); self.expect(current, {200}, "music conflict read")
                body = {"base_revision": current.json()["revision"], "item_ids": [track], "current_index": 0,
                        "position_seconds": 0, "repeat": "off", "shuffle": False, "active_output": {"kind": "browser"}}
                self.expect(member.put("/api/me/music-session", json=body), {200}, "music conflict winner")
                return member.put("/api/me/music-session", json=body)
            # A 409 here is the asserted, successful outcome—not an audit
            # error—so stale CAS behavior cannot be hidden in the error count.
            music_runs["expected_conflict_c1"] = aggregate(self.repeated("music session conflict", session_conflict, {409}))
            result["music_session_cas"] = {"case": "music_session_cas", "mode": "http", "runs": music_runs}

            queue = [track] * 5_000
            queue_response = self.sample("5k music queue", lambda: member.put("/api/me/music-session", json={"base_revision": member.get("/api/me/music-session").json()["revision"],
                "item_ids": queue, "current_index": 0, "position_seconds": 0, "repeat": "off", "shuffle": False, "active_output": {"kind": "browser"}}), {200})
            if queue_response["ok"] and queue_response["bytes"] > 8 * 1024 * 1024:
                queue_response = {**queue_response, "ok": False, "error": "ResponseCapExceeded", "detail": "5k queue response exceeded 8 MiB"}
            result["music_queue_5000"] = {"case": "music_queue_5000", "mode": "http", "runs": {"c1": aggregate([queue_response])}}
            result["music_queue_5000"]["note"] = "5,000 repeated references to one track; wall time includes revision GET plus PUT."
            distinct = self.selectors["track_ids"]
            def distinct_queue() -> httpx.Response:
                revision = member.get("/api/me/music-session").json()["revision"]
                return member.put("/api/me/music-session", json={"base_revision": revision, "item_ids": distinct,
                    "current_index": 0, "position_seconds": 0, "repeat": "off", "shuffle": False, "active_output": {"kind": "browser"}})
            result["music_queue_distinct"] = {"case": "music_queue_distinct", "mode": "http", "unique_tracks": len(distinct),
                "runs": {"c1": aggregate(self.repeated("distinct music queue", distinct_queue, {200}, count=3))},
                "note": "All available shared track identities in the seed; wall time includes revision GET plus PUT. Not 5,000 unique tracks unless the declared count reaches that size."}

            # Every event has its own durable identity. C6 needs a barrier:
            # another client's *fresh* event can legitimately increment the
            # shared fake-provider counter while this client's replay runs.
            ledger_clients = [admin, *[self.client(name) for name in self.selectors["members"]]]
            def export_payload(client: httpx.Client) -> dict[str, Any]:
                session = client.get("/api/session/me"); self.expect(session, {200}, "export session")
                connection = client.get("/api/me/listening-export"); self.expect(connection, {200}, "export connection")
                generation = next(row["generation"] for row in connection.json()["items"] if row["provider"] == "lastfm")
                return {"id": str(uuid4()), "owner_id": session.json()["user"]["id"], "account_generations": {"lastfm": generation},
                        "item_id": track, "started_at": int(time.time()) - 300, "listened_seconds": 240}

            def mark_duplicate(samples: list[dict[str, Any]], before: int, after: int) -> None:
                if after != before:
                    samples.append({"ok": False, "elapsed_ms": 0, "error": "ReplayDispatchedAgain",
                                    "detail": "replay phase changed provider dispatch count"})

            fresh_c1, replay_c1 = [], []
            for _ in range(self.samples):
                payload = export_payload(member)
                fresh_c1.append(self.sample("export ledger fresh", lambda payload=payload: member.post("/api/me/listening-events", json=payload), {200}))
                before = self.audit_counter("/_perf/audit/export-dispatches")["dispatches"]
                replay_c1.append(self.sample("export ledger replay", lambda payload=payload: member.post("/api/me/listening-events", json=payload), {200}))
                after = self.audit_counter("/_perf/audit/export-dispatches")["dispatches"]
                mark_duplicate(replay_c1, before, after)
            ledger_runs = {"fresh_c1": aggregate(fresh_c1), "replay_c1": aggregate(replay_c1)}
            if 6 in self.concurrency:
                fresh_c6, replay_c6 = [], []
                for _ in range(self.samples):
                    payloads = [(client, export_payload(client)) for client in ledger_clients]
                    fresh_c6.extend(self.concurrent("export ledger fresh", ledger_clients,
                        lambda client, payloads=payloads: client.post("/api/me/listening-events", json=next(payload for owner, payload in payloads if owner is client)), {200}))
                    before = self.audit_counter("/_perf/audit/export-dispatches")["dispatches"]
                    replay_round = self.concurrent("export ledger replay", ledger_clients,
                        lambda client, payloads=payloads: client.post("/api/me/listening-events", json=next(payload for owner, payload in payloads if owner is client)), {200})
                    replay_c6.extend(replay_round)
                    after = self.audit_counter("/_perf/audit/export-dispatches")["dispatches"]
                    mark_duplicate(replay_c6, before, after)
                ledger_runs["fresh_c6"] = aggregate(fresh_c6)
                ledger_runs["replay_c6"] = aggregate(replay_c6)
            result["listening_export_ledger"] = {"case": "listening_export_ledger", "mode": "http", "runs": ledger_runs}

            self.setup_book(member)
            assert self.book_id
            book_runs = {"reader": aggregate(self.repeated("book reader", lambda: member.get(f"/api/books/{self.book_id}/reader"), {200})),
                         "chapter": aggregate(self.repeated("book chapter", lambda: member.get(f"/api/books/{self.book_id}/chapters/0"), {200})),
                         "progress": aggregate(self.repeated("book progress", lambda: member.patch(f"/api/books/{self.book_id}/state", json={"unit_index": 0, "offset": 0.5}), {200}))}
            result["book_reader_progress"] = {"case": "book_reader_progress", "mode": "http", "runs": book_runs}

            import_response = []
            def start_import() -> httpx.Response:
                response = admin.post("/api/admin/imports", json={"root_id": self.selectors["external_root_id"], "visibility": "shared"})
                import_response.append(response)
                return response
            import_started = time.perf_counter()
            imported = self.sample("external import", start_import, {202})
            import_runs = {"c1": aggregate([imported])}
            terminal = {}
            if imported["ok"]:
                run_id = import_response[0].json()["id"]
                def await_import() -> httpx.Response:
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        response = admin.get(f"/api/admin/imports/{run_id}")
                        self.expect(response, {200}, "import status")
                        body = response.json()
                        if body["state"] not in {"running", "cancel_requested"}:
                            terminal.update(state=body["state"], coverage=body["coverage"], counters=body["counters"])
                            return response
                        time.sleep(0.1)
                    raise RuntimeError("synthetic import did not reach a terminal state within 120 seconds")
                completed = self.sample("external import terminal", await_import, {200})
                terminal["start_to_observed_terminal_ms"] = round((time.perf_counter() - import_started) * 1000, 3)
                if completed["ok"] and (terminal.get("state") not in {"succeeded", "needs_confirmation"}
                        or terminal.get("counters", {}).get("indexed") != self.selectors["import_file_count"]
                        or terminal.get("counters", {}).get("failed", 0)):
                    completed.update(ok=False, error="UnexpectedImportOutcome")
                import_runs["terminal_poll_n1"] = aggregate([completed])
            result["external_import"] = {"case": "external_import", "mode": "http", "runs": import_runs,
                "terminal": terminal,
                "note": "32 new one-second PCM WAV files plus reconciliation against the existing synthetic external-root records. Poll interval 100 ms; terminal may need missing-item confirmation. Not a large-file or cold-storage throughput baseline."}

            # Refresh is intentionally N=3: each sample replaces a 10k local
            # snapshot and would otherwise dominate the bounded audit window.
            refreshes = self.repeated("Arr refresh", lambda: admin.post("/_perf/audit/arr/refresh"), {200}, count=3)
            arr_reads = {"refresh_n3": aggregate(refreshes),
                         "c1": aggregate(self.repeated("Arr observed page", lambda: admin.get("/api/admin/arr/observations", params={"server_id": "audit-arr", "page": 100, "page_size": 100}), {200}))}
            if 6 in self.concurrency:
                arr_admins = [self.client(self.selectors["admin"]) for _ in range(6)]
                arr_reads["c6"] = aggregate([sample for _ in range(self.samples) for sample in self.concurrent("Arr observed page", arr_admins,
                    lambda client: client.get("/api/admin/arr/observations", params={"server_id": "audit-arr", "page": 100, "page_size": 100}), {200})])
            # One state-changing external operation plus a read-only reconcile
            # confirms the durable-operation path rather than only snapshot reads.
            state = admin.get("/api/admin/arr/servers/audit-operation/items/101"); self.expect(state, {200}, "Arr operation state")
            def operation() -> httpx.Response:
                payload = {"operation_id": str(uuid4()), "base_revision": state.json()["revision"], "action": "monitor", "monitored": True}
                outcome = admin.post("/api/admin/arr/servers/audit-operation/items/101/operations", json=payload)
                self.expect(outcome, {200}, "Arr operation")
                self.expect(admin.post(f"/api/admin/arr/operations/{outcome.json()['id']}/reconcile"), {200}, "Arr reconcile")
                return outcome
            arr_reads["operation_reconcile"] = aggregate([self.sample("Arr operation/reconcile", operation, {200})])
            result["arr_snapshot_and_operation"] = {"case": "arr_snapshot_and_operation", "mode": "http", "runs": arr_reads}

            connector_refresh = self.repeated("connector refresh", lambda: admin.post("/_perf/audit/connector/refresh"), {200}, count=3)
            connector_runs = {"refresh_n3": aggregate(connector_refresh),
                              "c1": aggregate(self.repeated("connector queue page", lambda: admin.get("/api/admin/connectors/audit-lidarr", params={"dataset": "queue", "page": 100, "page_size": 100}), {200}))}
            if 6 in self.concurrency:
                connector_admins = [self.client(self.selectors["admin"]) for _ in range(6)]
                connector_runs["c6"] = aggregate([sample for _ in range(self.samples) for sample in self.concurrent("connector queue page", connector_admins,
                    lambda client: client.get("/api/admin/connectors/audit-lidarr", params={"dataset": "queue", "page": 100, "page_size": 100}), {200})])
            result["connector_snapshot"] = {"case": "connector_snapshot", "mode": "http", "runs": connector_runs}

            def backup_create() -> httpx.Response:
                response = admin.post("/api/admin/backups")
                self.expect(response, {201}, "backup create")
                name = response.json().get("name")
                if not isinstance(name, str):
                    raise RuntimeError("backup create did not return its generated name")
                self.backup_name = name
                return response
            created = self.sample("backup create", backup_create, {201})
            result["backup_create_verify"] = {"case": "backup_create_verify", "mode": "http", "runs": {"c1": aggregate([created])},
                                               "note": "One backup is intentionally insufficient for a p95; offline restore follows server shutdown."}
        finally:
            admin.close(); member.close()
        return result

    def offline_restore(self) -> dict[str, Any]:
        if not self.backup_name:
            return {"case": "backup_restore", "mode": "offline-component", "blocked": "No successful backup identifier was available."}
        backup = self.data_dir / "backups" / f"{self.backup_name}.db"
        target = self.data_dir.parent / "audit-restored"
        started = time.perf_counter()
        env = {**os.environ, "LUMINA_DATA_DIR": str(self.data_dir)}
        command = [sys.executable, "-m", "app.restore", str(backup), "--data-dir", str(target)]
        completed = subprocess.run(command, cwd=ROOT / "backend", env=env, capture_output=True, text=True, timeout=120)
        ok = completed.returncode == 0 and (target / "app.db").is_file()
        integrity = None
        if ok:
            with sqlite3.connect(target / "app.db") as db:
                integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            ok = integrity == "ok"
        sample = {"ok": ok, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "bytes": backup.stat().st_size if backup.exists() else 0,
                  **({"integrity": integrity} if integrity else {"error": "RestoreFailed", "detail": completed.stderr[-300:]})}
        return {"case": "backup_restore", "mode": "offline-component", "runs": {"c1": aggregate([sample])},
                "note": "Offline component metric: the server was stopped and restore used a distinct disposable target."}


def blocked_cases() -> dict[str, Any]:
    return {case.name: {"case": case.name, "category": case.category, "mode": case.mode, "blocked": case.blocker}
            for case in CASES if case.blocker}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="empty disposable audit directory; --prepare seeds it")
    parser.add_argument("--prepare", action="store_true", help="create the disposable household/audit fixture before running")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--port", type=int, default=5923)
    parser.add_argument("--samples", type=int, default=20, choices=range(1, MAX_SAMPLES + 1))
    parser.add_argument("--concurrency", default="1,6", help="comma-separated C levels; only 1 and 6 are supported")
    args = parser.parse_args()
    levels = tuple(sorted({int(value) for value in args.concurrency.split(",")}))
    if any(level not in (1, 6) for level in levels) or 1 not in levels:
        parser.error("--concurrency must include 1 and may include 6")
    data_dir = (args.data_dir or Path(tempfile.mkdtemp(prefix="lumina-audit-backend-"))).resolve()
    if args.prepare:
        subprocess.run([sys.executable, str(PERF / "audit_backend_seed.py"), str(data_dir)], cwd=ROOT, check=True)
    if not (data_dir / "audit-selectors.json").is_file():
        parser.error("run with --prepare against an empty disposable --data-dir")
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    audit = Audit(data_dir=data_dir, output=output, port=args.port, samples=args.samples, concurrency=levels)
    result: dict[str, Any] = {"schema": 1, "started_at": datetime.now(UTC).isoformat(), "data_dir_kind": "disposable synthetic",
                               "samples_requested": args.samples, "concurrency": levels, "cases": blocked_cases(),
                               "limitations": ["No real providers, operator credentials, or private media are used.",
                                               "p95 is omitted until at least 20 successful samples.",
                                               "Composite workflow wall time covers all subcalls, while response SQL count and bytes describe the final response only.",
                                               "C6 aggregate n is request count (six times the number of rounds), not a round-latency percentile.",
                                               "The 8 MiB response check runs after httpx buffers the response; it detects oversized output but is not a streaming transport cap.",
                                               "Import terminal polling covers small synthetic WAV files and existing-root reconciliation, not large-file or remote-storage throughput.",
                                               "Server telemetry is process-local; scripts/perf/audit_run.py supplies process-tree evidence and quiet-host qualification."]}
    phase = "startup"
    fatal: dict[str, str] | None = None
    try:
        audit.start()
        phase = "http_cases"
        result["cases"].update(audit.run_http_cases())
        phase = "server_telemetry"
        result["server_telemetry_after_http"] = audit.telemetry()
    except BaseException as error:
        # Retain machine-readable failure evidence without copying server text,
        # source URLs, or credentials into an artifact.
        fatal = {"phase": phase, "error": type(error).__name__}
        result["fatal"] = fatal
    finally:
        audit.stop()
    try:
        phase = "offline_restore"
        result["cases"]["backup_restore"] = audit.offline_restore()
    except BaseException as error:
        result["cases"]["backup_restore"] = {"case": "backup_restore", "mode": "offline-component", "fatal": type(error).__name__}
        fatal = fatal or {"phase": phase, "error": type(error).__name__}
        result.setdefault("fatal", fatal)
    result["finished_at"] = datetime.now(UTC).isoformat()
    destination = output / f"backend-audit-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    failures = [name for name, case in result["cases"].items() if case.get("runs") and any(run.get("errors", 0) for run in case["runs"].values())]
    print(f"backend audit evidence: {destination}")
    print("blocked coverage: " + ", ".join(sorted(name for name, case in result["cases"].items() if case.get("blocked"))))
    return 1 if fatal or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
