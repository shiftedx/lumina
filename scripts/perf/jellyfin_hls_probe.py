"""Replay a Jellyfin app's HLS session against a running `playperf_server.py ROOT PORT` and time every step.

    backend/.venv/bin/python scripts/perf/playperf_server.py ROOT PORT &
    backend/.venv/bin/python scripts/perf/jellyfin_hls_probe.py ROOT PORT [--runs 3]

Per scenario (a title, a DeviceProfile, a resume position) it posts PlaybackInfo with StartTimeTicks, opens master.m3u8,
reads the variant playlist, then fetches the init segment and the segment a client lands on after resuming, after seeking
back to 0, far ahead and back again. It checks each segment's first timestamp against the playlist: a timeline that is
not the whole file from 0, or a segment from the wrong place, shows as MISMATCH. Medians of --runs fresh PlaySessionIds.
"""
from __future__ import annotations

import argparse
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

PASSWORD = "Lantern-harbor-2026"
AUTH = 'MediaBrowser Client="Swiftfin", Device="probe", DeviceId="probe-1", Version="1.0"'
HLS = {"Type": "Video", "Container": "mp4", "Protocol": "hls", "AudioCodec": "aac"}
# Plays HEVC and H.264 in HLS: Perf Gop (H.264, AC-3) copies its video; Perf Hevc copies HEVC and encodes E-AC-3.
COPY = {"DirectPlayProfiles": [{"Type": "Video", "Container": "mp4", "VideoCodec": "h264,hevc", "AudioCodec": "aac"}],
        "TranscodingProfiles": [{**HLS, "VideoCodec": "h264,hevc"}]}
# H.264 only, 480p: every title is encoded.
ENCODE = {"DirectPlayProfiles": [{"Type": "Video", "Container": "mp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
          "TranscodingProfiles": [{**HLS, "VideoCodec": "h264"}],
          "CodecProfiles": [{"Type": "Video", "Codec": "h264", "Conditions": [{"Condition": "LessThanEqual", "Property": "Height", "Value": "480"}]}]}
SCENARIOS = [("Perf Gop", "copy video, long GOP", COPY, 100), ("Perf Hevc", "copy HEVC", COPY, 100), ("Perf Gop", "encode 480p", ENCODE, 100)]


def start(client: httpx.Client, media: Path) -> dict[str, str]:
    """Owner, Jellyfin on, media imported; the Jellyfin headers for a signed-in app."""
    if client.get("/api/bootstrap/status").json()["needs_setup"]:
        client.post("/api/bootstrap/admin", json={"username": "owner", "display_name": "Owner", "password": PASSWORD}).raise_for_status()
    csrf = client.post("/api/session/login", json={"username": "owner", "password": PASSWORD}).json()["csrf_token"]
    client.headers.update({"X-CSRF-Token": csrf, "Origin": str(client.base_url).rstrip("/")})
    client.put("/api/admin/media-server", json={"jellyfin_enabled": True}).raise_for_status()
    if not client.get("/api/library").json()["items"]:
        root = client.post("/api/admin/storage/roots", json={"label": "Media", "container_path": str(media), "mode": "external"}).json()["id"]
        run = client.post("/api/admin/imports", json={"root_id": root, "visibility": "private"}).json()
        while client.get(run["status_url"]).json()["state"] not in ("succeeded", "failed"):
            time.sleep(0.5)
    body = {"Username": "owner", "Pw": PASSWORD}
    token = httpx.post(f"{client.base_url}/Users/AuthenticateByName", json=body, headers={"Authorization": AUTH}).json()["AccessToken"]
    return {"Authorization": f'{AUTH}, Token="{token}"'}


def first_time(init: bytes, segment: bytes, work: Path) -> float:
    """Earliest video timestamp of one fMP4 segment, as ffprobe reads it with the init segment."""
    (work / "i.mp4").write_bytes(init)
    (work / "s.m4s").write_bytes(segment)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time", "-of", "csv=p=0",
                          f"concat:{work / 'i.mp4'}|{work / 's.m4s'}"], capture_output=True, text=True).stdout
    return min(float(line.split(",")[1]) for line in out.split() if line.startswith("video"))


def run_once(jf: httpx.Client, headers: dict[str, str], item: str, profile: dict, resume: float, work: Path) -> dict[str, float]:
    timings: dict[str, float] = {}

    def timed(label: str, request) -> httpx.Response:  # noqa: ANN001
        started = time.perf_counter()
        response = request()
        timings[label] = (time.perf_counter() - started) * 1000
        assert response.status_code == 200, (label, response.status_code, response.text[:200])
        return response

    info = timed("PlaybackInfo", lambda: jf.post(f"/Items/{item}/PlaybackInfo", headers=headers, json={"DeviceProfile": profile, "StartTimeTicks": int(resume * 1e7)})).json()
    source = info["MediaSources"][0]
    base = source["TranscodingUrl"].split("/master.m3u8")[0]
    session = info["PlaySessionId"]
    timed("master.m3u8", lambda: jf.get(source["TranscodingUrl"], headers=headers))
    playlist = timed("index.m3u8", lambda: jf.get(f"{base}/hls/{session}/index.m3u8", headers=headers)).text
    durations = [float(value) for value in re.findall(r"EXTINF:([\d.]+)", playlist)]
    starts = [sum(durations[:i]) for i in range(len(durations))]
    timings["timeline s"] = sum(durations)
    timings["ENDLIST"] = float("#EXT-X-ENDLIST" in playlist)
    init = timed("init.mp4", lambda: jf.get(f"{base}/hls/{session}/init.mp4", headers=headers)).content
    landed = max(i for i, t in enumerate(starts) if t <= resume)
    wrong = 0
    for label, index in (("resume segment", landed), ("next segment", landed + 1), ("seek to 0", 0), ("seek far ahead", len(starts) - 2), ("seek back", landed)):
        index = min(index, len(starts) - 1)
        started = time.perf_counter()
        response = jf.get(f"{base}/hls/{session}/seg{index}.m4s", headers=headers)
        timings[label] = (time.perf_counter() - started) * 1000
        if response.status_code != 200:  # a segment the playlist lists but the server will not produce (-1 ms)
            timings[label], wrong = -1, wrong + 1
            print(f"  MISSING {label}: seg{index} answered {response.status_code}")
            continue
        actual = first_time(init, response.content, work)
        if abs(actual - starts[index]) > 0.5:
            wrong += 1
            print(f"  MISMATCH {label}: seg{index} playlist {starts[index]:.2f} s, segment starts {actual:.2f} s")
    timings["MISMATCH"] = float(wrong)  # segments missing or from another time than the playlist says
    jf.delete("/Videos/ActiveEncodings", params={"PlaySessionId": session}, headers=headers)
    return timings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("port")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    with httpx.Client(base_url=base, timeout=120) as admin, httpx.Client(base_url=base, timeout=120) as jf, tempfile.TemporaryDirectory() as work:
        headers = start(admin, Path(args.root).resolve() / "media")
        movies = {m["Name"]: m["Id"] for m in jf.get("/Items", params={"IncludeItemTypes": "Movie", "Recursive": "true"}, headers=headers).json()["Items"]}
        for title, label, profile, resume in SCENARIOS:
            runs = [run_once(jf, headers, movies[title], profile, resume, Path(work)) for _ in range(args.runs)]
            print(f"\n{title}: {label}, resume at {resume} s (median of {args.runs})")
            for key in runs[0]:
                print(f"  {key:16s} {statistics.median(run[key] for run in runs):9.1f}")


if __name__ == "__main__":
    sys.exit(main())
