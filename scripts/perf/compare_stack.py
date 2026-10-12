"""Create and measure the isolated Lumina/Jellyfin comparison stack.

This script owns only containers whose names start with ``lumina-compare-``.
The caller supplies a work root containing ``media/`` from compare_fixtures.py.

    python scripts/perf/compare_stack.py up WORK_ROOT
    python scripts/perf/compare_stack.py startup WORK_ROOT --runs 5
    python scripts/perf/compare_stack.py down WORK_ROOT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import time
from collections import Counter
from pathlib import Path

import httpx

PASSWORD = "Compare-synthetic-2026!"
LUMINA_NAME = "lumina-compare-lumina"
JELLYFIN_NAME = "lumina-compare-jellyfin"
LUMINA_BASE = "http://127.0.0.1:18765"
JELLYFIN_BASE = "http://127.0.0.1:18096"
AUTH = 'MediaBrowser Client="LuminaCompare", Device="setup", DeviceId="compare-setup", Version="1"'


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def wait(base: str, path: str, timeout: float = 120) -> float:
    started = time.perf_counter()
    deadline = started + timeout
    with httpx.Client(base_url=base, timeout=2, trust_env=False) as client:
        while time.perf_counter() < deadline:
            try:
                if client.get(path).status_code == 200:
                    return time.perf_counter() - started
            except httpx.HTTPError:
                pass
            time.sleep(0.02)
    raise TimeoutError(f"{base}{path} did not become ready in {timeout}s")


def wait_login(base: str, timeout: float = 120) -> float:
    started = time.perf_counter()
    deadline = started + timeout
    with httpx.Client(base_url=base, timeout=2, trust_env=False) as client:
        while time.perf_counter() < deadline:
            try:
                response = client.post(
                    "/Users/AuthenticateByName", headers={"Authorization": AUTH},
                    json={"Username": "owner", "Pw": PASSWORD},
                )
                if response.status_code == 200:
                    return time.perf_counter() - started
            except httpx.HTTPError:
                pass
            time.sleep(0.02)
    raise TimeoutError(f"{base} did not accept an authenticated API request in {timeout}s")


def wait_jellyfin(timeout: float = 120) -> float:
    """Full readiness in either first-run or configured state."""
    started = time.perf_counter()
    deadline = started + timeout
    with httpx.Client(base_url=JELLYFIN_BASE, timeout=2, trust_env=False) as client:
        while time.perf_counter() < deadline:
            try:
                if client.get("/Startup/User").status_code == 200:
                    return time.perf_counter() - started
                response = client.post(
                    "/Users/AuthenticateByName", headers={"Authorization": AUTH},
                    json={"Username": "owner", "Pw": PASSWORD},
                )
                if response.status_code == 200:
                    return time.perf_counter() - started
            except httpx.HTTPError:
                pass
            time.sleep(0.02)
    raise TimeoutError(f"{JELLYFIN_BASE} did not reach first-run or authenticated readiness in {timeout}s")


def token(client: httpx.Client, username: str = "owner") -> tuple[str, str]:
    response = client.post(
        "/Users/AuthenticateByName", headers={"Authorization": AUTH},
        json={"Username": username, "Pw": PASSWORD},
    )
    response.raise_for_status()
    body = response.json()
    return body["AccessToken"], body["User"]["Id"]


def headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f'{AUTH}, Token="{access_token}"'}


def remove(name: str) -> None:
    command("docker", "rm", "-f", name, check=False)


def docker_up(root: Path, lumina_image: str, jellyfin_image: str) -> dict:
    media = (root / "media").resolve()
    if not (media / "fixture-manifest.json").is_file():
        raise FileNotFoundError(f"run compare_fixtures.py first: {media / 'fixture-manifest.json'}")
    for directory in (root / "lumina-data", root / "jellyfin-config", root / "jellyfin-cache"):
        directory.mkdir(parents=True, exist_ok=True)
    remove(LUMINA_NAME)
    remove(JELLYFIN_NAME)
    lumina_started = time.perf_counter()
    command(
        "docker", "run", "-d", "--name", LUMINA_NAME,
        "-p", "127.0.0.1:18765:8765",
        "-e", "LUMINA_DATA_DIR=/data",
        "-e", "LUMINA_STORAGE_MOUNT_PARENTS=/media",
        "-e", f"LUMINA_ALLOWED_ORIGINS={LUMINA_BASE}",
        "-e", "LUMINA_ARTWORK_PASS=off",
        "-v", f"{(root / 'lumina-data').resolve()}:/data",
        "-v", f"{media}:/media:ro",
        lumina_image,
    )
    lumina_ready = wait(LUMINA_BASE, "/api/health")
    jellyfin_started = time.perf_counter()
    command(
        "docker", "run", "-d", "--name", JELLYFIN_NAME,
        "-p", "127.0.0.1:18096:8096",
        "-e", "JELLYFIN_PublishedServerUrl=http://127.0.0.1:18096",
        "-v", f"{(root / 'jellyfin-config').resolve()}:/config",
        "-v", f"{(root / 'jellyfin-cache').resolve()}:/cache",
        "-v", f"{media}:/media:ro",
        jellyfin_image,
    )
    # /System/Info/Public and /health answer while Jellyfin can still return 503
    # from application routes during first-run migrations.
    jellyfin_ready = wait_jellyfin()
    return {
        "lumina_run_to_ready_seconds": round(lumina_ready, 4),
        "jellyfin_run_to_ready_seconds": round(jellyfin_ready, 4),
        "sequential_setup_seconds": round(time.perf_counter() - lumina_started, 4),
        "jellyfin_started_offset_seconds": round(jellyfin_started - lumina_started, 4),
    }


def lumina_probe_snapshot(database: Path) -> dict:
    """Read a stopped/checkpointed database from the host."""
    if not database.is_file():
        return {"artifacts": 0, "codec_ready": 0, "loudness_ready": 0}
    connection = None
    try:
        connection = sqlite3.connect(database, timeout=1)
        artifacts, codec_ready, loudness_ready = connection.execute(
            "SELECT count(*), "
            "sum(CASE WHEN probe IS NOT NULL AND "
            "(json_type(probe, '$.streams') = 'array' OR json_extract(probe, '$.error') IS NOT NULL) "
            "THEN 1 ELSE 0 END), "
            "sum(CASE WHEN json_type(probe, '$.loudness') = 'object' THEN 1 ELSE 0 END) "
            "FROM media_artifacts WHERE lifecycle = 'available'"
        ).fetchone()
    except sqlite3.OperationalError:
        return {"artifacts": 0, "codec_ready": 0, "loudness_ready": 0}
    finally:
        if connection is not None:
            connection.close()
    return {
        "artifacts": artifacts,
        "codec_ready": codec_ready or 0,
        "loudness_ready": loudness_ready or 0,
    }


def lumina_probe_snapshot_live(container: str = LUMINA_NAME) -> dict:
    """Read the live WAL through Linux SQLite inside the writing container."""
    script = """import json, os, sqlite3
p = '/data/app.db'
q = \"\"\"SELECT count(*),
sum(CASE WHEN probe IS NOT NULL AND
(json_type(probe, '$.streams') = 'array' OR json_extract(probe, '$.error') IS NOT NULL)
THEN 1 ELSE 0 END),
sum(CASE WHEN json_type(probe, '$.loudness') = 'object' THEN 1 ELSE 0 END)
FROM media_artifacts WHERE lifecycle = 'available'\"\"\"
c = sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=1)
try:
    row = c.execute(q).fetchone()
    mode = c.execute('pragma journal_mode').fetchone()[0]
finally:
    c.close()
print(json.dumps({'artifacts': row[0], 'codec_ready': row[1] or 0,
                  'loudness_ready': row[2] or 0, 'observer': 'linux_container_sqlite',
                  'journal_mode': mode,
                  'wal_bytes': os.path.getsize(p + '-wal') if os.path.exists(p + '-wal') else 0}))
"""
    result = command("docker", "exec", container, "python", "-c", script, check=False)
    if result.returncode != 0:
        return {"artifacts": 0, "codec_ready": 0, "loudness_ready": 0,
                "observer": "linux_container_sqlite", "error": result.stderr.strip()[:300]}
    return json.loads(result.stdout)


def wait_lumina_scan_readiness(expected: int, started: float, timeout: float = 900) -> dict:
    deadline = time.monotonic() + timeout
    samples = []
    previous = None
    milestones: dict[str, float] = {}
    while time.monotonic() < deadline:
        snapshot = lumina_probe_snapshot_live()
        elapsed = time.perf_counter() - started
        if snapshot != previous:
            samples.append({"elapsed_seconds": round(elapsed, 4), **snapshot})
            previous = snapshot
        if snapshot["artifacts"] >= expected and "metadata_visible_seconds" not in milestones:
            milestones["metadata_visible_seconds"] = round(elapsed, 4)
        if snapshot["codec_ready"] >= expected and "codec_ready_seconds" not in milestones:
            milestones["codec_ready_seconds"] = round(elapsed, 4)
        if snapshot["loudness_ready"] >= expected:
            milestones["loudness_ready_seconds"] = round(elapsed, 4)
            return {
                **milestones,
                "final": snapshot,
                "samples": samples,
                "readiness_predicates": {
                    "metadata": "available media_artifacts count reaches expected",
                    "codec": "each available probe contains a streams array or explicit error",
                    "loudness": "each available probe contains a loudness object",
                    "worker_idle": "all database work predicates are exhausted; no public worker-state API exists",
                },
            }
        time.sleep(0.1 if "codec_ready_seconds" not in milestones else 0.25)
    raise TimeoutError(f"Lumina scan readiness did not complete: {previous}")


def setup_lumina(root: Path, *, scan_readiness: bool = False, expected: int = 0) -> dict:
    with httpx.Client(base_url=LUMINA_BASE, timeout=120, trust_env=False) as client:
        if client.get("/api/bootstrap/status").json()["needs_setup"]:
            response = client.post(
                "/api/bootstrap/admin",
                json={"username": "owner", "display_name": "Owner", "password": PASSWORD},
            )
            response.raise_for_status()
        session = client.post("/api/session/login", json={"username": "owner", "password": PASSWORD})
        session.raise_for_status()
        client.headers.update({"X-CSRF-Token": session.json()["csrf_token"], "Origin": LUMINA_BASE})
        client.put("/api/admin/media-server", json={"jellyfin_enabled": True}).raise_for_status()
        existing = client.get("/api/admin/storage/roots").json()
        if not existing:
            root_id = client.post(
                "/api/admin/storage/roots",
                json={"label": "Comparison media", "container_path": "/media", "mode": "external"},
            ).json()["id"]
            started = time.perf_counter()
            run = client.post("/api/admin/imports", json={"root_id": root_id, "visibility": "private"}).json()
            while True:
                state = client.get(run["status_url"]).json()
                if state["state"] in ("succeeded", "failed"):
                    break
                time.sleep(0.05)
            scan_seconds = time.perf_counter() - started
            if state["state"] != "succeeded":
                raise RuntimeError(f"Lumina import failed: {state}")
            readiness = wait_lumina_scan_readiness(expected, started) if scan_readiness else None
        else:
            scan_seconds = None
            readiness = None
    # A full loudness pass can outlive HTTP keep-alive. Use a new connection for
    # the Jellyfin-compatible verification request rather than reusing the admin
    # client that initiated the scan several minutes earlier.
    with httpx.Client(base_url=LUMINA_BASE, timeout=120, trust_env=False) as api_client:
        access_token, user_id = token(api_client)
        count = api_client.get(
            f"/Users/{user_id}/Items", params={"Recursive": "true"}, headers=headers(access_token),
        ).json()["TotalRecordCount"]
    return {
        "scan_seconds": round(scan_seconds, 4) if scan_seconds is not None else None,
        "library_items": count,
        "scan_readiness": readiness,
    }


def jellyfin_media_snapshot(database: Path, client: httpx.Client, auth: dict[str, str]) -> dict:
    reported_total = codec_ready = 0
    if database.is_file():
        try:
            with sqlite3.connect(database, timeout=1) as connection:
                reported_total = connection.execute(
                    "SELECT count(*) FROM BaseItems WHERE Path LIKE '/media/Movies/%'"
                ).fetchone()[0]
                codec_ready = connection.execute(
                    "SELECT count(DISTINCT item.Id) FROM BaseItems item "
                    "JOIN MediaStreamInfos stream ON stream.ItemId = item.Id "
                    "WHERE item.Path LIKE '/media/Movies/%' AND item.RunTimeTicks IS NOT NULL "
                    "AND stream.StreamType = 1 AND stream.Codec IS NOT NULL"
                ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
    tasks = client.get("/ScheduledTasks", headers=auth).json()
    scan = next((task for task in tasks if task.get("Key") == "RefreshLibrary"), None)
    return {
        "reported_total": reported_total,
        "codec_ready": codec_ready,
        "refresh_library_state": scan.get("State") if scan else None,
    }


def setup_jellyfin(root: Path, expected: int, *, scan_readiness: bool = False) -> dict:
    with httpx.Client(base_url=JELLYFIN_BASE, timeout=120, trust_env=False) as client:
        public = client.get("/System/Info/Public").json()
        if not public.get("StartupWizardCompleted", public.get("startupWizardCompleted", False)):
            client.post(
                "/Startup/Configuration",
                json={"ServerName": "Jellyfin Compare", "UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"},
            ).raise_for_status()
            client.post("/Startup/User", json={"Name": "owner", "Password": PASSWORD}).raise_for_status()
            client.post("/Startup/RemoteAccess", json={"EnableRemoteAccess": False}).raise_for_status()
            client.post("/Startup/Complete").raise_for_status()
        access_token, user_id = token(client)
        auth = headers(access_token)
        folders = client.get("/Library/VirtualFolders", headers=auth).json()
        scan_seconds = None
        if not folders:
            started = time.perf_counter()
            client.post(
                "/Library/VirtualFolders",
                params={"name": "Comparison Movies", "collectionType": "movies", "paths": "/media/Movies", "refreshLibrary": "true"},
                json={}, headers=auth,
            ).raise_for_status()
            deadline = time.monotonic() + 300
            count = 0
            metadata_visible_seconds = None
            codec_ready_seconds = None
            samples = []
            previous = None
            while time.monotonic() < deadline:
                snapshot = jellyfin_media_snapshot(root / "jellyfin-config" / "data" / "jellyfin.db", client, auth)
                elapsed = time.perf_counter() - started
                count = snapshot["reported_total"] or 0
                if scan_readiness and snapshot != previous:
                    samples.append({"elapsed_seconds": round(elapsed, 4), **snapshot})
                    previous = snapshot
                if count >= expected and metadata_visible_seconds is None:
                    metadata_visible_seconds = round(elapsed, 4)
                if snapshot["codec_ready"] >= expected and codec_ready_seconds is None:
                    codec_ready_seconds = round(elapsed, 4)
                if (count >= expected and snapshot["codec_ready"] >= expected
                        and snapshot["refresh_library_state"] == "Idle"):
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError(f"Jellyfin scan did not reach {expected} movies; reached {count}")
            scan_seconds = time.perf_counter() - started
            readiness = {
                "metadata_visible_seconds": metadata_visible_seconds,
                "codec_ready_seconds": codec_ready_seconds,
                "refresh_library_idle_seconds": round(scan_seconds, 4),
                "final": snapshot,
                "samples": samples,
                "readiness_predicates": {
                    "metadata": "movie rows under the synthetic media path reach expected",
                    "codec": "every movie has RunTimeTicks and a named video stream in MediaStreamInfos",
                    "idle": "RefreshLibrary scheduled task reports Idle",
                },
            } if scan_readiness else None
        else:
            readiness = None
        count = client.get(
            f"/Users/{user_id}/Items",
            params={"Recursive": "true", "IncludeItemTypes": "Movie"}, headers=auth,
        ).json()["TotalRecordCount"]
        options_path = root / "jellyfin-config" / "root" / "default" / "Comparison Movies" / "options.xml"
        options = options_path.read_text() if options_path.is_file() else ""
        return {
            "scan_seconds": round(scan_seconds, 4) if scan_seconds is not None else None,
            "library_items": count,
            "scan_readiness": readiness,
            "providers": {
                "internet_providers_disabled": "<EnableInternetProviders>false</EnableInternetProviders>" in options,
                "lufs_scan_enabled": "<EnableLUFSScan>true</EnableLUFSScan>" in options,
                "options_path": str(options_path.relative_to(root)) if options else None,
            },
        }


def image_metadata(image: str) -> dict:
    raw = json.loads(command("docker", "image", "inspect", image).stdout)[0]
    return {
        "reference": image,
        "id": raw["Id"],
        "repo_digests": raw.get("RepoDigests", []),
        "architecture": raw["Architecture"],
        "os": raw["Os"],
        "size_bytes": raw["Size"],
        "labels": raw.get("Config", {}).get("Labels") or {},
    }


def docker_metadata() -> dict:
    return json.loads(command(
        "docker", "info", "--format",
        '{"server_version":{{json .ServerVersion}},"operating_system":{{json .OperatingSystem}},'
        '"os_type":{{json .OSType}},"architecture":{{json .Architecture}},"logical_cpus":{{.NCPU}},'
        '"memory_bytes":{{.MemTotal}},"cgroup_version":{{json .CgroupVersion}},"storage_driver":{{json .Driver}}}',
    ).stdout)


def file_identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    stat = path.stat()
    return {"size_bytes": stat.st_size, "sha256": digest.hexdigest(), "hard_link_count": stat.st_nlink}


def container_cgroup(name: str) -> dict:
    result = command(
        "docker", "exec", name, "sh", "-c",
        "for f in cpu.max memory.max cpuset.cpus.effective; do printf '%s=' \"$f\"; cat /sys/fs/cgroup/$f 2>/dev/null || true; done",
        check=False,
    )
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip()
    top = command("docker", "top", name, "-eo", "comm", check=False)
    processes = Counter(line.strip() for line in top.stdout.splitlines()[1:] if line.strip())
    return {"limits": values, "process_commands": dict(sorted(processes.items()))}


def preflight(root: Path, fixture: dict) -> dict:
    media = root / "media"
    movie_files = sorted(path for path in (media / "Movies").rglob("*") if path.suffix.lower() in {".mp4", ".mkv"})
    inodes = Counter(path.stat().st_ino for path in movie_files)
    sizes = Counter(path.stat().st_size for path in movie_files)
    direct = file_identity(media / fixture["direct"]["path"])
    transcode = file_identity(media / fixture["transcode"]["path"])
    disk = shutil.disk_usage(root)
    validations = {
        "movie_file_count_matches": len(movie_files) == fixture["movies"],
        "media_size_distribution_matches": sizes == Counter({
            fixture["direct"]["size_bytes"]: fixture["movies"] - 1,
            fixture["transcode"]["size_bytes"]: 1,
        }),
        "direct_size_matches": direct["size_bytes"] == fixture["direct"]["size_bytes"],
        "direct_sha256_matches": direct["sha256"] == fixture["direct"]["sha256"],
        "transcode_size_matches": transcode["size_bytes"] == fixture["transcode"]["size_bytes"],
        "transcode_sha256_matches": transcode["sha256"] == fixture["transcode"]["sha256"],
    }
    return {
        "fixture": {
            "movie_file_count": len(movie_files),
            "unique_inode_count": len(inodes),
            "largest_hard_link_group": max(inodes.values(), default=0),
            "size_distribution": {str(size): count for size, count in sorted(sizes.items())},
            "hard_link_note": "Hard links are a fixture storage optimization only; copied benchmark roots may not preserve them.",
            "direct": direct,
            "transcode": transcode,
            "validations": validations,
            "all_valid": all(validations.values()),
        },
        "disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free},
        "containers": {
            "lumina": container_cgroup(LUMINA_NAME),
            "jellyfin": container_cgroup(JELLYFIN_NAME),
        },
        "database_readiness": {
            "lumina": lumina_probe_snapshot_live(),
        },
        "sanitization": "No host names, registry configuration, proxy configuration, credentials, tokens, or host process arguments are recorded.",
    }


def write_targets(root: Path) -> None:
    for name, base, container in (
        ("lumina", LUMINA_BASE, LUMINA_NAME),
        ("jellyfin", JELLYFIN_BASE, JELLYFIN_NAME),
    ):
        (root / f"target-{name}.json").write_text(json.dumps({
            "name": name, "base_url": base, "container": container,
            "username": "owner", "password": PASSWORD,
        }, indent=2) + "\n")


def up(root: Path, lumina_image: str, jellyfin_image: str, *, scan_readiness: bool = False) -> dict:
    fixture = json.loads((root / "media" / "fixture-manifest.json").read_text())
    output = {
        "schema": 1,
        "captured_at_unix": time.time(),
        "host": {"platform": platform.platform(), "machine": platform.machine()},
        "docker": docker_metadata(),
        "fixture": fixture,
        "images": {"lumina": image_metadata(lumina_image), "jellyfin": image_metadata(jellyfin_image)},
        "initial_start": docker_up(root, lumina_image, jellyfin_image),
    }
    output["setup"] = {
        "lumina": setup_lumina(root, scan_readiness=scan_readiness, expected=fixture["movies"]),
        "jellyfin": setup_jellyfin(root, fixture["movies"], scan_readiness=scan_readiness),
    }
    output["preflight"] = preflight(root, fixture)
    write_targets(root)
    (root / "environment.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    return output


def startup(root: Path, runs: int) -> dict:
    targets = (
        ("lumina", LUMINA_NAME, LUMINA_BASE),
        ("jellyfin", JELLYFIN_NAME, JELLYFIN_BASE),
    )
    output = {
        "schema": 2,
        "runs": runs,
        "readiness": "successful Jellyfin-compatible /Users/AuthenticateByName with the same credentials and client headers",
        "targets": {},
    }
    for label, container, base in targets:
        samples = []
        command("docker", "stop", container, check=False)
        for _ in range(runs):
            started = time.perf_counter()
            command("docker", "start", container)
            ready_seconds = wait_login(base)
            wall_seconds = time.perf_counter() - started
            stats = command("docker", "stats", "--no-stream", "--format", "{{json .}}", container).stdout.strip()
            samples.append({"ready_seconds": round(ready_seconds, 4), "docker_start_to_ready_seconds": round(wall_seconds, 4), "ready_stats": json.loads(stats)})
            command("docker", "stop", container)
        command("docker", "start", container)
        wait_login(base)
        output["targets"][label] = {"samples": samples}
    (root / "startup.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the isolated Lumina/Jellyfin comparison stack.")
    sub = parser.add_subparsers(dest="command", required=True)
    up_parser = sub.add_parser("up")
    up_parser.add_argument("root", type=Path)
    up_parser.add_argument(
        "--lumina-image",
        required=True,
        help="exact Lumina image reference; required so a historical default cannot contaminate a timed phase",
    )
    up_parser.add_argument("--jellyfin-image", default="jellyfin/jellyfin:12.0")
    up_parser.add_argument("--scan-readiness", action="store_true", help="wait for and record full codec/loudness readiness")
    startup_parser = sub.add_parser("startup")
    startup_parser.add_argument("root", type=Path)
    startup_parser.add_argument("--runs", type=int, default=5)
    down_parser = sub.add_parser("down")
    down_parser.add_argument("root", type=Path)
    sanitize_parser = sub.add_parser("sanitize")
    sanitize_parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "up":
        print(json.dumps(up(
            root, args.lumina_image, args.jellyfin_image, scan_readiness=args.scan_readiness,
        )["setup"], sort_keys=True))
    elif args.command == "startup":
        print(json.dumps(startup(root, args.runs)["targets"], sort_keys=True))
    elif args.command == "down":
        remove(LUMINA_NAME)
        remove(JELLYFIN_NAME)
    else:
        path = root / "environment.json"
        environment = json.loads(path.read_text())
        environment["docker"] = docker_metadata()
        path.write_text(json.dumps(environment, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
