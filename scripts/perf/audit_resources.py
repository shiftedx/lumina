"""Component benchmark for the production local playback admission registry.

This intentionally measures ``PlaybackResources`` with the real SQLite policy
queries and production ``ClientCaps`` decision code.  It does not start an
encoder, serve media, shape a network, or contact a provider.  Run it only
inside the shared performance lock (normally through ``audit_run.py``):

  mise exec -- backend/.venv/bin/python scripts/perf/audit_resources.py \
    --out output/performance-audit/resources/resource-admission.json

The result preserves every raw wall/CPU sample, checks that every admitted
lease is released, and treats required 429 capacity denials as successful
contract outcomes rather than fast failures.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import book_models, models  # noqa: F401  (register all production tables)
from app.db import Base
from app.models import AppSettings, User
from app.persistence import persistence_metrics_snapshot, reset_persistence_metrics
from app.services.playback_decision import BROWSER_DEFAULT
from app.services.playback_resources import Admission, PlaybackResources
from app.services.resource_policy import HouseholdResources, ResourceAllowances, configure_runtime


DEFAULT_OUTPUT = ROOT / "output" / "performance-audit" / "resources" / "resource-admission.json"
MIN_SAMPLES = 20
C6 = 6


def _facts(*, direct: bool = False) -> dict[str, Any]:
    """Synthetic normalized ffprobe facts matching the playback contract tests."""
    if direct:
        return {
            "container": "mov,mp4,m4a,3gp,3g2,mj2", "bit_rate": 2_000_000,
            "streams": [
                {"index": 0, "type": "video", "codec": "h264", "height": 720, "width": 1280, "pix_fmt": "yuv420p", "default": True},
                {"index": 1, "type": "audio", "codec": "aac", "channels": 2, "default": True},
            ],
        }
    return {
        "container": "mov,mp4,m4a,3gp,3g2,mj2", "bit_rate": 100_000_000,
        "streams": [
            {"index": 0, "type": "video", "codec": "h264", "height": 2160, "width": 3840, "pix_fmt": "yuv420p", "default": True},
            {"index": 1, "type": "audio", "codec": "aac", "channels": 2, "default": True},
        ],
    }


def _request(*, local: bool = False) -> Request:
    host = "192.168.70.9" if local else "203.0.113.9"
    return Request({"type": "http", "method": "GET", "scheme": "https", "path": "/", "headers": [], "client": (host, 443)})


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _aggregate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [sample for sample in samples if sample["ok"]]

    def summary(key: str) -> dict[str, float | None]:
        values = [float(sample[key]) for sample in successful]
        return {
            "median": round(statistics.median(values), 3) if values else None,
            "p95": round(_percentile(values, 0.95), 3) if len(values) >= MIN_SAMPLES else None,
            "max": round(max(values), 3) if values else None,
        }

    return {
        "n": len(samples), "successful": len(successful), "errors": len(samples) - len(successful),
        "wall_ms": summary("wall_ms"), "cpu_ms": summary("cpu_ms"), "samples": samples,
    }


@dataclass
class Fixture:
    factory: sessionmaker[Session]
    policy: HouseholdResources

    def user(self, db: Session, user_id: str) -> User:
        user = db.get(User, user_id)
        if user is None:  # The fixture owns all identities; missing one invalidates the run.
            raise RuntimeError("synthetic member missing")
        return user


def _fixture(data_dir: Path) -> Fixture:
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "resources.sqlite3"
    engine = create_engine(
        f"sqlite:///{database}", future=True, connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    policy = HouseholdResources(
        upload_bps=48_000_000, lan_networks=["192.168.70.0/24"],
        defaults=ResourceAllowances(remote_stream_bps=8_000_000, remote_member_bps=8_000_000, playback_sessions=2, video_encodes=2),
    )
    with factory.begin() as db:
        db.add(AppSettings(id=1, temp_root=str(data_dir / "temp"), archive_path=str(data_dir / "archive"), resource_policy=policy.model_dump(mode="json")))
        db.add_all(User(id=f"member-{index}", username=f"member-{index}", display_name=f"Member {index}", is_active=True) for index in range(7))
    # ``admit_direct`` consults the production fast-path runtime hint before
    # loading facts.  This process is benchmark-only, so publishing this
    # durable policy is the same lifecycle main.py uses at startup.
    configure_runtime(policy)
    return Fixture(factory=factory, policy=policy)


def _admit(fixture: Fixture, resources: PlaybackResources, user_id: str, logical_id: str, *, direct: bool = False, local: bool = False) -> Admission:
    with fixture.factory() as db:
        user = fixture.user(db, user_id)
        if direct:
            admission = resources.admit_direct(db, user, _request(local=local), f"item-{logical_id}", "audit-device", _facts(direct=True), BROWSER_DEFAULT)
            if admission is None or admission.decision.mode != "direct":
                raise RuntimeError("synthetic direct source was not direct-admitted")
            return admission
        admission = resources.admit(db, user, _request(local=local), f"item-{logical_id}", "audit-device", logical_id, _facts(), BROWSER_DEFAULT)
        if local:
            # The source is a browser-compatible MP4.  LAN playback has no
            # protected remote bitrate ceiling, so production policy preserves
            # it as direct while still reserving the member session slot.
            if admission.decision.mode != "direct":
                raise RuntimeError("synthetic local source was not direct-admitted")
        elif admission.decision.mode != "transcode" or admission.decision.video != "encode":
            raise RuntimeError("synthetic constrained remote source did not choose video conversion")
        return admission


def _assert_released(resources: PlaybackResources) -> None:
    if resources._items:  # private inspection is solely a postcondition; policy decisions remain production calls.
        raise RuntimeError("admitted lease remained after release")


def _sample(label: str, attempts: int, action: Callable[[], None]) -> dict[str, Any]:
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    try:
        action()
        return {"label": label, "ok": True, "attempts": attempts,
                "wall_ms": round((time.perf_counter() - wall_started) * 1000, 3),
                "cpu_ms": round((time.process_time() - cpu_started) * 1000, 3)}
    except Exception as error:  # Content-free evidence; inputs are entirely synthetic.
        return {"label": label, "ok": False, "attempts": attempts,
                "wall_ms": round((time.perf_counter() - wall_started) * 1000, 3),
                "cpu_ms": round((time.process_time() - cpu_started) * 1000, 3),
                "error": type(error).__name__}


def _c1(fixture: Fixture, iteration: int) -> None:
    resources = PlaybackResources()
    admission = _admit(fixture, resources, "member-0", f"c1-{iteration}")
    resources.release(admission)
    _assert_released(resources)


def _c6(fixture: Fixture, iteration: int) -> None:
    resources = PlaybackResources()

    def one(index: int) -> Admission:
        return _admit(fixture, resources, f"member-{index}", f"c6-{iteration}-{index}")

    with ThreadPoolExecutor(max_workers=C6) as pool:
        admissions = list(pool.map(one, range(C6)))
    if sum(admission.allocation_bps or 0 for admission in admissions) != 48_000_000:
        raise RuntimeError("C6 admission did not reserve the expected household ceiling")
    for admission in admissions:
        resources.release(admission)
    _assert_released(resources)


def _denial_contracts(fixture: Fixture) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    # Local session slots use the same production policy but no remote byte
    # allocation.  A third logical playback must be refused at the configured
    # per-member ceiling, then cancellation frees both admitted leases.
    local = PlaybackResources()
    first = _admit(fixture, local, "member-0", "local-1", local=True)
    second = _admit(fixture, local, "member-0", "local-2", local=True)
    try:
        _admit(fixture, local, "member-0", "local-denied", local=True)
    except HTTPException as error:
        if error.status_code != 429:
            raise RuntimeError("session ceiling returned an unexpected status") from error
        results.append({"contract": "member_session_ceiling", "status": error.status_code, "expected": "denied"})
    else:
        raise RuntimeError("member session ceiling admitted a third playback")
    local.release(first)
    local.release(second)
    _assert_released(local)

    # Six remote starts consume the 48 Mbps household allocation.  The seventh
    # is an expected capacity denial; releasing the six is cancellation cleanup.
    remote = PlaybackResources()
    admitted = [_admit(fixture, remote, f"member-{index}", f"ceiling-{index}") for index in range(C6)]
    try:
        _admit(fixture, remote, "member-6", "ceiling-denied")
    except HTTPException as error:
        if error.status_code != 429:
            raise RuntimeError("household bandwidth ceiling returned an unexpected status") from error
        results.append({"contract": "household_bandwidth_ceiling", "status": error.status_code, "expected": "denied"})
    else:
        raise RuntimeError("household bandwidth ceiling admitted a seventh remote playback")
    for admission in admitted:
        remote.release(admission)
    _assert_released(remote)

    direct = PlaybackResources()
    admission = _admit(fixture, direct, "member-0", "direct", direct=True)
    direct.release(admission)
    _assert_released(direct)
    results.append({"contract": "direct_admission_then_cancel", "status": 200, "expected": "released"})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--data-dir", type=Path, help="isolated fixture directory; temporary when omitted")
    parser.add_argument("--samples", type=int, default=MIN_SAMPLES)
    args = parser.parse_args()
    if args.samples < MIN_SAMPLES:
        parser.error(f"--samples must be at least {MIN_SAMPLES} so p95 has adequate N")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="lumina-resource-audit-")
        data_dir = Path(temporary.name)
    else:
        data_dir = args.data_dir
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "case": "resource_admission", "mode": "component-only", "samples_per_concurrency": args.samples,
        "scope": "Production PlaybackResources, resource_policy household/effective queries, synthetic normalized facts, and production BROWSER_DEFAULT ClientCaps.",
        "limitations": [
            "Component-only: excludes HTTP route overhead, actual encoder startup, media range delivery, bandwidth throughput, and provider calls.",
            "CPU samples are current-process CPU deltas; use audit_run.py process-tree RSS/CPU evidence for an environment observation.",
            "The registry is intentionally in-memory; restart/recovery behavior and real stream cancellation are outside this component fixture.",
            "C6 reports 20 concurrent rounds (120 admission attempts), not a single 120-way burst.",
        ],
        "blocked_cases": [
            "actual encoder startup and throughput require a qualified local media/ffmpeg fixture",
            "range-response cancellation requires a real streaming route and client disconnect harness",
            "provider/remote-source admission is excluded by design",
        ],
    }
    stage = "fixture"
    try:
        fixture = _fixture(data_dir)
        reset_persistence_metrics()
        stage = "c1"
        c1 = [_sample("c1_admit_release", 1, lambda index=index: _c1(fixture, index)) for index in range(args.samples)]
        stage = "c6"
        c6 = [_sample("c6_admit_release", C6, lambda index=index: _c6(fixture, index)) for index in range(args.samples)]
        result["runs"] = {"c1": _aggregate(c1), "c6": _aggregate(c6)}
        result["attempt_totals"] = {"c1": args.samples, "c6": args.samples * C6}
        stage = "denial_contracts"
        result["denial_contracts"] = _denial_contracts(fixture)
        stage = "persistence_metrics"
        result["persistence_metrics"] = persistence_metrics_snapshot()
        result["ok"] = all(sample["ok"] for sample in c1 + c6)
    except Exception as error:
        result.update({"ok": False, "fatal": {"stage": stage, "error": type(error).__name__}})
    finally:
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if temporary is not None:
            temporary.cleanup()
    print(f"resource admission audit: {'ok' if result.get('ok') else 'failed'}; evidence {args.out}", flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
