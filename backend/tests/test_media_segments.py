"""Media segments: storage and precedence, Lumina and Jellyfin shapes, watched at credits, detection."""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid

import pytest

from app import db as db_module
from app.config import settings
from app.media_schemas import MediaSegment
from app.models import LibraryItem, MediaTitle, User
from app.schemas import PlaybackProgressUpdateRequest
from app.services import media_segments
from app.services.media_titles import jellyfin_id, synthetic_id
from app.services.playback import PlaybackProgressService
from app.services.yt_dlp_service import YtDlpService
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401


def _item(name: str = "episode.mkv", owner: str = "alice", duration: int | None = None) -> str:
    item_id = _download(owner, settings.library_root / owner / name, remote_id=name)
    if duration is not None:
        with db_module.SessionLocal() as db:
            db.get(LibraryItem, item_id).duration = duration
            db.commit()
    return item_id


def _seg(kind: str, start_ms: int, end_ms: int, source: str, confidence: float = 0.5) -> dict:
    return {"type": kind, "start_ms": start_ms, "end_ms": end_ms, "source": source, "confidence": confidence}


def _seed(item_id: str, segments: list[dict], **extra: object) -> None:
    with db_module.SessionLocal() as db:
        artifact, fingerprint, _ = media_segments.current_analysis(db, item_id)
        artifact.analysis = {"fingerprint": fingerprint, "segments": segments, **extra}
        db.commit()


def _progress(item_id: str, position: int) -> bool:
    with db_module.SessionLocal() as db:
        progress = PlaybackProgressService(db).update(item_id, PlaybackProgressUpdateRequest(position_seconds=position), db.get(User, "alice"))
        db.commit()
        return progress.completed


def test_best_source_wins_per_type(household: None) -> None:
    item_id = _item()
    _seed(item_id, [
        _seg("intro", 1000, 20_000, "heuristic"), _seg("intro", 2000, 21_000, "fingerprint", 1.0),
        _seg("intro", 500, 19_000, "introdb", 0.8), _seg("credits", 60_000, 70_000, "heuristic"),
    ])
    with db_module.SessionLocal() as db:
        segments = media_segments.segments_for(db, item_id)
    assert [(s.type, s.source, s.start_seconds, s.end_seconds) for s in segments] == [
        ("intro", "fingerprint", 2.0, 21.0), ("credits", "heuristic", 60.0, 70.0),
    ]


def test_segment_routes_edit_replaces_detection_and_respect_visibility(household: None) -> None:
    item_id = _item()
    _seed(item_id, [_seg("intro", 1000, 20_000, "fingerprint", 1.0)])
    url = f"/api/library/{item_id}/segments"
    with _client("alice") as client:
        assert client.get(url).json() == {"item_id": item_id, "segments": [
            {"type": "intro", "start_seconds": 1.0, "end_seconds": 20.0, "source": "fingerprint", "confidence": 1.0},
        ]}
        edited = client.put(url, json={"segments": [{"type": "credits", "start_seconds": 50, "end_seconds": 60}]})
        assert edited.status_code == 200
        assert [(s["type"], s["source"]) for s in edited.json()["segments"]] == [("credits", "user")]  # the wrong intro is gone
        assert client.put(url, json={"segments": [{"type": "intro", "start_seconds": 30, "end_seconds": 10}]}).status_code == 422
    with _client("bob") as client:
        assert client.get(url).status_code == 404 and client.put(url, json={"segments": []}).status_code == 404
    with db_module.SessionLocal() as db:
        db.get(LibraryItem, item_id).visibility = "shared"
        db.commit()
    with _client("bob") as client:
        assert client.get(url).status_code == 200
        assert client.put(url, json={"segments": []}).status_code == 403


def test_progress_at_credits_start_counts_as_watched(household: None) -> None:
    item_id = _item(duration=1000)
    _seed(item_id, [_seg("credits", 900_000, 1_000_000, "fingerprint", 1.0)])
    assert _progress(item_id, 899) is False
    assert _progress(item_id, 900) is True
    _seed(item_id, [_seg("credits", 400_000, 1_000_000, "heuristic")])  # before half-way: not trusted
    assert _progress(item_id, 700) is False
    assert _progress(item_id, 950) is True  # the 95% rule still applies


def test_changed_file_drops_segments(household: None) -> None:
    item_id = _item(duration=1000)
    _seed(item_id, [_seg("credits", 900_000, 1_000_000, "fingerprint", 1.0)])
    with (settings.library_root / "alice" / "episode.mkv").open("ab") as media:
        media.write(b"re-encoded")
    with db_module.SessionLocal() as db:
        assert media_segments.segments_for(db, item_id) == []
    assert _progress(item_id, 905) is False


def test_jellyfin_segments_mapping_never_exposes_mute() -> None:
    item, title = str(uuid.uuid4()), str(uuid.uuid4())
    kinds = ["intro", "credits", "recap", "preview", "commercial"]
    segments = [MediaSegment(type=kind, start_seconds=10 * n, end_seconds=10 * n + 5, source="user", confidence=1.0) for n, kind in enumerate(kinds)]
    body = media_segments.jellyfin_segments(title, item, segments)
    assert [entry["Type"] for entry in body["Items"]] == ["Intro", "Outro", "Recap", "Preview", "Commercial"]
    assert body["Items"][0] == {
        "Id": jellyfin_id(synthetic_id(f"segment:{item}:intro:0")), "ItemId": uuid.UUID(title).hex,
        "Type": "Intro", "StartTicks": 0, "EndTicks": 50_000_000,
    }
    assert (body["TotalRecordCount"], body["StartIndex"]) == (5, 0)
    assert media_segments.jellyfin_segments(title, item, segments, include=["outro", "Intro"])["TotalRecordCount"] == 2


needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=120)


def _media(name: str, *args: str) -> str:
    path = settings.library_root / "alice" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(*args, str(path))
    return _item(name)


def _noise_episode(name: str, seed: int, intro_at: int, total: int = 240) -> str:
    """A seeded anoisesrc intro (20 s) shared by every episode, at a different offset in each."""
    return _media(
        name,
        "-f", "lavfi", "-i", f"anoisesrc=d={intro_at}:r=16000:a=0.3:s={seed + 10}",
        "-f", "lavfi", "-i", "anoisesrc=d=20:r=16000:a=0.3:s=7",
        "-f", "lavfi", "-i", f"anoisesrc=d={total - intro_at - 20}:r=16000:a=0.3:s={seed + 20}",
        "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", "-c:a", "aac",
    )


def _season(item_ids: list[str], tmdb: str | None = None) -> None:
    with db_module.SessionLocal() as db:
        db.add(MediaTitle(id="series", type="series", key="r:Show", name="Show", provider_ids={"Tmdb": tmdb} if tmdb else {}))
        db.add(MediaTitle(id="season", type="season", parent_id="series", key="r:Show#s1", name="Season 1", index_number=1))
        for n, item_id in enumerate(item_ids, 1):
            db.add(MediaTitle(id=f"e{n}", type="episode", parent_id="season", key=f"r:Show#s1e{n}", name=f"Episode {n}", index_number=n))
            db.get(LibraryItem, item_id).title_id = f"e{n}"
        db.commit()


def _detect(item_id: str) -> None:
    media_segments.detect(f"job-{item_id}", item_id, lambda: None)


def _segments(item_id: str) -> list[tuple]:
    with db_module.SessionLocal() as db:
        return [(s.type, s.source, s.start_seconds, s.end_seconds) for s in media_segments.segments_for(db, item_id)]


@needs_ffmpeg
@pytest.mark.parametrize("method", ["bandpass", "chromaprint"])
def test_fingerprint_finds_a_shared_intro_at_different_offsets(household: None, monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    if method == "chromaprint" and not media_segments.has_chromaprint("ffmpeg"):
        pytest.skip("this ffmpeg has no chromaprint muxer (jellyfin-ffmpeg has)")
    monkeypatch.setattr(media_segments, "has_chromaprint", lambda ffmpeg: method == "chromaprint")
    offsets = {"e1.mka": 5, "e2.mka": 12, "e3.mka": 30}
    items = [_noise_episode(name, seed, at) for seed, (name, at) in enumerate(offsets.items())]
    _season(items)
    for item_id, at in zip(items, offsets.values(), strict=True):
        _detect(item_id)
        ((kind, source, start, end),) = _segments(item_id)
        assert (kind, source) == ("intro", "fingerprint")
        assert abs(start - at) <= 1 and abs(end - (at + 20)) <= 1


@needs_ffmpeg
def test_black_tail_becomes_credits(household: None) -> None:
    item_id = _media(
        "fade.mkv",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=62",
        "-f", "lavfi", "-i", "color=black:size=320x240:rate=25:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=70",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "mpeg4", "-g", "25", "-c:a", "aac",
    )
    _detect(item_id)
    ((kind, source, start, end),) = _segments(item_id)
    assert (kind, source) == ("credits", "heuristic") and abs(start - 62) <= 1 and abs(end - 70) <= 0.2


def test_recap_and_preview_follow_the_intro_and_credits() -> None:
    cues = [(5000, 6000, "Previously on Show..."), (200_000, 201_000, "Previously on is not a recap here"), (3_000_000, 3_001_000, "Next time on Show")]
    found = [
        {"type": "intro", "start_ms": 30_000, "end_ms": 60_000, "source": "fingerprint", "confidence": 1.0},
        {"type": "credits", "start_ms": 2_900_000, "end_ms": 3_100_000, "source": "heuristic", "confidence": 0.5},
    ]
    assert media_segments.text_segments(cues, found, 3100) == [
        {"type": "recap", "start_ms": 5000, "end_ms": 30_000, "source": "heuristic", "confidence": 0.5},
        {"type": "preview", "start_ms": 3_000_000, "end_ms": 3_100_000, "source": "heuristic", "confidence": 0.5},
    ]
    assert media_segments.text_segments(cues, [], 3100) == []  # no intro bounds a recap; no credits, no preview


@needs_ffmpeg
def test_introdb_is_opt_in_and_rechecked_monthly(household: None, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_get(query: dict) -> dict:
        calls.append(query)
        return {"tmdb_id": 1396, "intro": [{"start_ms": 1000, "end_ms": 3000}], "credits": [{"start_ms": 4000, "end_ms": None}],
                "recap": [{"start_ms": None, "end_ms": 60_000}]}

    monkeypatch.setattr(media_segments, "_introdb_get", fake_get)
    item_id = _media("short.mka", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:a", "aac")
    _season([item_id], tmdb="1396")
    _detect(item_id)
    assert calls == []  # off by default: a lookup tells a third party what the household owns
    with db_module.SessionLocal() as db:
        YtDlpService(db).ensure_app_settings().introdb_enabled = True
        db.commit()
    _detect(item_id)
    _detect(item_id)
    assert calls == [{"tmdb_id": "1396", "season": 1, "episode": 1}]  # one request, then the 30-day cache
    segments = _segments(item_id)
    assert [(kind, source, start) for kind, source, start, _end in segments] == [("intro", "introdb", 1.0), ("credits", "introdb", 4.0)]
    assert segments[1][3] == pytest.approx(5.0, abs=0.1)  # a null end is the file's end; the recap ends past it and is rejected


@needs_ffmpeg
def test_detection_discarded_when_file_changes_mid_run(household: None, monkeypatch: pytest.MonkeyPatch) -> None:
    item_id = _media("changing.mka", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:a", "aac")
    _seed(item_id, [_seg("intro", 0, 1000, "heuristic")])

    def rewrite_during_detection(*args: object) -> None:
        with (settings.library_root / "alice" / "changing.mka").open("ab") as media:
            media.write(b"x")

    monkeypatch.setattr(media_segments, "blackdetect_credits", rewrite_during_detection)
    with pytest.raises(media_segments.SegmentError, match="changed"):
        _detect(item_id)
    with db_module.SessionLocal() as db:
        assert db.get(LibraryItem, item_id) is not None
        artifact = media_segments.current_analysis(db, item_id)[0]
        assert artifact.analysis["segments"] == [_seg("intro", 0, 1000, "heuristic")]


@needs_ffmpeg
def test_detect_route_runs_a_segments_job(household: None) -> None:
    item_id = _media("routed.mka", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:a", "aac")
    with _client("bob") as client:
        assert client.post(f"/api/library/{item_id}/segments/detect").status_code == 404
    with _client("alice") as client:
        started = client.post(f"/api/library/{item_id}/segments/detect")
        assert started.status_code == 202 and started.json()["kind"] == "segments"
        deadline = time.monotonic() + 20
        while (body := client.get(f"/api/enrichment/jobs/{started.json()['id']}").json())["state"] in {"queued", "running"}:
            assert time.monotonic() < deadline
            time.sleep(0.05)
    assert body["state"] == "succeeded", body
