"""Qualify synthetic media through one real Caddy TLS proxy and one Lumina process.

Requires caddy, openssl, ffmpeg and the backend venv. No existing server/data is used.
Run: mise exec -- backend/.venv/bin/python scripts/qualify_household_resources.py
"""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
BURST = 32768


def server(root: Path, port: int) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.db import init_db, SessionLocal
    from app.models import DeviceToken, LibraryItem, LibraryItemArtifact, MediaArtifact, StorageRoot, User
    from app.security import hash_password, session_digest
    from app.services.yt_dlp_service import YtDlpService
    import uvicorn

    init_db()
    ids = json.loads((root / "ids.json").read_text())
    media = root / "media"
    with SessionLocal() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.jellyfin_enabled = True
        record.min_free_disk_mb = 0
        record.hwaccel = "off"
        record.resource_policy = {"upload_bps": 512000, "defaults": {"remote_stream_bps": 256000, "remote_member_bps": 384000}}
        db.add(StorageRoot(id=ids["root"], label="Synthetic qualification", path=str(media), mode="external", enabled=True,
                           identity=str(media.stat().st_dev), observation={"state": "available"}))
        for index, user_id in enumerate(ids["users"]):
            db.add(User(id=user_id, username=f"fixture{index}", display_name=f"Fixture {index}", role="admin" if index == 0 else "viewer",
                        password_hash=hash_password(os.environ["QUALIFY_PASSWORD"]), is_active=True, onboarding_status="completed"))
            db.add(DeviceToken(id=str(uuid.uuid4()), user_id=user_id, kind="jellyfin", scope="write", token_digest=session_digest(ids["tokens"][index]),
                               device_id=f"fixture{index}", device_name="Qualification", client="Qualification"))
        for key, filename in (("download", "download.bin"), ("video", "video.mp4")):
            path = media / filename
            stat = path.stat()
            artifact_id = str(uuid.uuid4())
            db.add(MediaArtifact(id=artifact_id, root_id=ids["root"], relative_path=filename, ownership="external", lifecycle="available",
                                 size=stat.st_size, mtime_ns=stat.st_mtime_ns))
            db.add(LibraryItem(id=ids[key], user_id=ids["users"][0], title=f"Synthetic {key}", visibility="shared", kind="video", status="available",
                               file_size=stat.st_size, metadata_json={}))
            db.add(LibraryItemArtifact(library_item_id=ids[key], artifact_id=artifact_id))
        db.commit()
    from app.main import app

    class Measured:
        async def __call__(self, scope, receive, send):
            scope.setdefault("state", {})
            async def measured(message):
                await send(message)
                if message["type"] == "http.response.body" and "/download" in scope.get("path", "").lower():
                    with (root / "send.jsonl").open("a") as output:
                        output.write(json.dumps([time.monotonic(), scope["state"].get("resource_user_id"), len(message.get("body", b""))]) + "\n")
            await app(scope, receive, measured)
    uvicorn.run(Measured(), host="127.0.0.1", port=port, log_level="warning", lifespan="on", proxy_headers=False)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(root: Path) -> dict:
    import httpx
    backend_port, proxy_port = free_port(), free_port()
    media = root / "media"
    media.mkdir()
    (media / "download.bin").write_bytes(bytes(range(256)) * 8192)
    # Constant-rate filler makes an otherwise compatible H.264 source genuinely ~100 Mbps.
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=12", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "100M",
                    "-minrate", "100M", "-maxrate", "100M", "-bufsize", "100M", "-x264-params", "nal-hrd=cbr:force-cfr=1",
                    "-c:a", "aac", "-movflags", "+faststart", str(media / "video.mp4")], check=True)
    ids = {key: str(uuid.uuid4()) for key in ("root", "video", "download")}
    ids.update(users=[str(uuid.uuid4()), str(uuid.uuid4())], tokens=[secrets.token_urlsafe(32), secrets.token_urlsafe(32)])
    (root / "ids.json").write_text(json.dumps(ids))
    password = secrets.token_urlsafe(24)
    origin = f"https://localhost:{proxy_port}"
    env = {**os.environ, "LUMINA_DATA_DIR": str(root / "data"), "LUMINA_PORT": str(backend_port),
           "LUMINA_APP_PUBLIC_URL": origin, "LUMINA_SESSION_COOKIE_SECURE": "true", "LUMINA_TRUSTED_PROXY_IPS": "127.0.0.1/32",
           "LUMINA_STORAGE_MOUNT_PARENTS": str(media), "LUMINA_ARTWORK_PASS": "off", "QUALIFY_PASSWORD": password}
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(root / "key.pem"),
                    "-out", str(root / "cert.pem"), "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    config = root / "Caddyfile"
    config.write_text(f"""{{
 admin off
 auto_https off
}}
https://localhost:{proxy_port} {{
 bind 127.0.0.1
 tls {root / 'cert.pem'} {root / 'key.pem'}
 reverse_proxy 127.0.0.1:{backend_port} {{
  header_up X-Forwarded-For {{remote_host}}
  header_up -CF-Connecting-IP
  header_up -Forwarded
  flush_interval -1
 }}
}}
""")
    processes = []
    with (root / "server.log").open("w") as log:
        try:
            processes.append(subprocess.Popen([sys.executable, __file__, "--server", str(root), str(backend_port)], env=env, stdout=log, stderr=log))
            processes.append(subprocess.Popen(["caddy", "run", "--config", str(config), "--adapter", "caddyfile"], stdout=log, stderr=log))
            with httpx.Client(base_url=origin, verify=False, trust_env=False, timeout=60) as client:
                deadline = time.monotonic() + 60
                while True:
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline or any(process.poll() is not None for process in processes):
                        raise RuntimeError("qualification servers did not start; inspect " + str(root / "server.log"))
                    time.sleep(0.1)
                url = f"{origin}/Items/{ids['download'].replace('-', '')}/Download"
                measurements = []
                for label, length, parallel in (("short", 65536, 4), ("sustained", 524288, 4), ("full", 2097152, 2)):
                    events = []
                    lock = threading.Lock()
                    start = time.monotonic()
                    def fetch(index):
                        with httpx.Client(verify=False, trust_env=False, timeout=60) as reader:
                            headers = {"X-Emby-Token": ids["tokens"][index % 2]}
                            if label != "full":
                                headers["Range"] = f"bytes=0-{length-1}"
                            with reader.stream("GET", url, headers=headers) as response:
                                assert response.status_code == (200 if label == "full" else 206), response.status_code
                                assert response.headers.get("x-accel-buffering") == "no"
                                count = 0
                                for chunk in response.iter_bytes(16384):
                                    count += len(chunk)
                                    with lock:
                                        events.append((time.monotonic(), index % 2, len(chunk)))
                                assert count == length
                    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
                        list(pool.map(fetch, range(parallel)))
                    elapsed = time.monotonic() - start
                    total = 0
                    per_member = [0, 0]
                    excess = 0.0
                    member_excess = 0.0
                    for at, member, count in sorted(events):
                        total += count
                        per_member[member] += count
                        excess = max(excess, total - (512000 / 8 * (at-start) + BURST))
                        member_excess = max(member_excess, per_member[member] - (384000 / 8 * (at-start) + BURST))
                    assert excess <= 1024 and member_excess <= 1024, (excess, member_excess)
                    server_total, server_excess = 0, 0.0
                    for at, user_id, count in sorted(json.loads(line) for line in (root / "send.jsonl").read_text().splitlines()):
                        if start <= at <= start + elapsed:
                            server_total += count
                            server_excess = max(server_excess, server_total - (512000 / 8 * (at-start) + BURST))
                    assert server_total == total and server_excess <= 1024, (server_total, total, server_excess)
                    measurements.append(dict(window=label, bytes=total, seconds=round(elapsed, 3), parallel=parallel,
                                             household_bps=512000, member_bps=384000, burst_bytes=BURST,
                                             client_envelope_excess_bytes=round(excess), member_envelope_excess_bytes=round(member_excess),
                                             server_send_bytes=server_total, server_envelope_excess_bytes=round(server_excess)))
                auth = ("fixture0", password)
                response = client.put("/api/admin/resources", auth=auth, json={"upload_bps": 20_000_000})
                assert response.status_code == 200, response.text
                options = client.get(f"/api/library/{ids['video']}/playback-options", auth=auth)
                assert options.status_code == 200 and options.json()["mode"] == "transcode", options.text
                forbidden = client.get(f"/api/library/{ids['video']}/media?max_bitrate=999999999", auth=auth)
                assert forbidden.status_code == 409, forbidden.status_code
                started = client.post(f"/api/library/{ids['video']}/playback-sessions", auth=auth, json={})
                assert started.status_code == 201, started.text
                playback = started.json()
                manifest = client.get(playback["playback_url"], auth=auth)
                assert manifest.status_code == 200
                segment = next(line for line in manifest.text.splitlines() if line.endswith(".m4s"))
                segment_response = client.get(playback["playback_url"].rsplit("/", 1)[0] + "/" + segment, auth=auth)
                assert segment_response.status_code == 200 and segment_response.content
                stopped = client.delete(f"/api/playback-sessions/{playback['session_id']}", auth=auth)
                assert stopped.status_code == 204, stopped.text
                return {"topology": "Caddy TLS -> one loopback Lumina process", "proxy_buffering": "flush_interval -1; no proxy cache",
                        "measurements": measurements, "quality": {"source": "100 Mbps synthetic H.264/AAC", "ceiling_bps": 8000000,
                        "mode": options.json()["mode"], "original_refused": forbidden.status_code, "hls_segment_bytes": len(segment_response.content)},
                        "send_log": str(root / "send.jsonl")}
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        server(Path(sys.argv[2]), int(sys.argv[3]))
        return
    for program in ("caddy", "openssl", "ffmpeg"):
        if not shutil.which(program):
            raise SystemExit(f"{program} is required")
    # Retain isolated artifacts for reproducibility/debugging; never touch a configured server.
    root = Path(tempfile.mkdtemp(prefix="lumina-resource-qualification-")).resolve()
    print(f"artifacts={root}", flush=True)
    result = run(root)
    (root / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
