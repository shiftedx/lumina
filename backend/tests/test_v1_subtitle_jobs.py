"""Subtitle job routes: subtitle jobs (generate, sync, translate), job polling, bulk enrichment, captions ingestion."""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import time
import uuid

import pytest

from app import db as db_module
from app.config import settings
from app.models import AsrJob, LibraryItem, LibraryItemArtifact, MediaArtifact, Transcript, UserSettings
from app.services import enrichment, library_import, subtitle_translate
from app.services.transcripts import TranscriptService
from app.services.yt_dlp_service import YtDlpService
from tests.test_v1_artifacts import _client, _download, household  # noqa: F401

DONE = {"succeeded", "failed", "canceled", "interrupted"}


def _item(name: str = "episode.mkv", owner: str = "alice") -> str:
    return _download(owner, settings.library_root / owner / name, remote_id=name)


def _store(item_id: str, **kwargs) -> str:  # noqa: ANN003
    with db_module.SessionLocal() as db:
        return TranscriptService(db).store(item_id, **kwargs).id


def _settle(client, job_id: str) -> dict:  # noqa: ANN001
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get(f"/api/enrichment/jobs/{job_id}").json()
        if body["state"] in DONE:
            return body
        time.sleep(0.02)
    raise AssertionError("enrichment job did not settle")


def _configure_ai() -> None:
    with db_module.SessionLocal() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model = "http://127.0.0.1:9/v1", "fake-model"
        db.commit()


def _speech(seed: int = 1, minutes: int = 10) -> list[tuple[int, int]]:
    rnd, at, out = random.Random(seed), 2000, []
    while at < minutes * 60_000:
        length = rnd.randint(500, 4000)
        out.append((at, at + length))
        at += length + rnd.randint(300, 3000)
    return out


def test_sync_aligns_a_caption_track_to_asr_speech(household: None) -> None:
    item_id = _item()
    speech = _speech()
    _store(item_id, language="english", source_kind="asr", cues=[(s, e, "speech") for s, e in speech])
    caption = _store(item_id, language="en", source_kind="source_caption", cues=[(s + 2500, e + 2500, f"line {n}") for n, (s, e) in enumerate(speech)])
    with _client("alice") as client:
        started = client.post(f"/api/library/{item_id}/subtitle-tracks/t:{caption}/sync")
        assert started.status_code == 202 and started.json()["kind"] == "sync"
        done = _settle(client, started.json()["id"])
        assert done["state"] == "succeeded", done
        again = client.post(f"/api/library/{item_id}/subtitle-tracks/t:{caption}/sync")
        assert (again.status_code, again.json()["id"]) == (200, done["id"])
    with db_module.SessionLocal() as db:
        synced = db.get(Transcript, done["transcript_id"])
        assert (synced.source_kind, synced.derived_from, synced.model_label) == ("synced", caption, "sync-v1")
        cues = TranscriptService(db).cue_tuples(synced.id)
        labels = [track.label for track in TranscriptService(db).subtitle_tracks(item_id)]
    assert max(abs(cue[0] - start) for cue, (start, _end) in zip(cues, speech, strict=True)) <= 100
    assert "English (synced)" in labels


def test_translate_needs_ai_validates_language_and_stores_a_track(household: None, monkeypatch: pytest.MonkeyPatch) -> None:
    item_id = _item()
    source = _store(item_id, language="en", source_kind="source_caption", cues=[(0, 900, "Hello"), (1000, 1900, "Goodbye")])
    url = f"/api/library/{item_id}/subtitle-tracks/t:{source}/translate"

    def fake_chat(config, messages, *, max_tokens):  # noqa: ANN001, ANN202
        request = json.loads(re.search(r"<cues>\n(.*)\n</cues>", messages[1]["content"], re.S)[1])
        return json.dumps({"cues": [{"id": cue["id"], "text": cue["text"].upper()} for cue in request["cues"]]})

    monkeypatch.setattr(subtitle_translate, "chat", fake_chat)
    with _client("alice") as client:
        unconfigured = client.post(url, json={"target_language": "es"})
        assert (unconfigured.status_code, unconfigured.json()["detail"]) == (409, "ai_not_configured")
        _configure_ai()
        assert client.post(url, json={"target_language": "Spanish"}).status_code == 422  # the contract pattern
        assert client.post(url, json={"target_language": "xx"}).status_code == 422  # not in the ISO table
        started = client.post(url, json={"target_language": "es"})
        assert started.status_code == 202
        done = _settle(client, started.json()["id"])
    assert done["state"] == "succeeded", done
    with db_module.SessionLocal() as db:
        translated = db.get(Transcript, done["transcript_id"])
        assert (translated.source_kind, translated.language, translated.derived_from) == ("translated", "spa", source)
        assert TranscriptService(db).cue_tuples(translated.id) == [(0, 900, "HELLO"), (1000, 1900, "GOODBYE")]


def test_generate_without_asr_is_409(household: None) -> None:
    item_id = _item()
    with _client("alice") as client:
        response = client.post(f"/api/library/{item_id}/subtitle-tracks/generate")
    assert (response.status_code, response.json()["detail"]) == (409, "asr_not_configured")


def test_track_ids_and_visibility(household: None) -> None:
    item_id = _item()
    caption = _store(item_id, language="en", source_kind="source_caption", cues=[(0, 900, "Hello")])
    base = f"/api/library/{item_id}/subtitle-tracks"
    with _client("alice") as client:
        assert client.post(f"{base}/x:1/sync").status_code == 422
        assert client.post(f"{base}/t:{uuid.uuid4()}/sync").status_code == 404
        assert client.post(f"{base}/i:3/sync").status_code == 404  # image subtitles have no text
        job_id = client.post(f"{base}/t:{caption}/sync").json()["id"]
        _settle(client, job_id)
    with _client("bob") as client:  # alice's download is private to her
        assert client.post(f"{base}/t:{caption}/sync").status_code == 404
        assert client.post(f"{base}/t:{caption}/translate", json={"target_language": "es"}).status_code == 404
        assert client.get(f"/api/enrichment/jobs/{job_id}").status_code == 404


def test_sidecar_outside_media_folder_is_not_a_track(household: None) -> None:
    item_id = _item()
    folder = settings.library_root / "alice"
    secret = settings.library_root / "secret.srt"
    secret.write_text("1\n00:00:01,000 --> 00:00:02,000\nsecret\n")
    (folder / "link.en.srt").symlink_to(secret)
    (folder / "episode.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nfine\n")
    entry = {"language": "en", "forced": False, "hearing_impaired": False, "default": False, "format": "srt"}
    with db_module.SessionLocal() as db:
        db.get(LibraryItem, item_id).metadata_json = {"lumina_subtitles": [
            {**entry, "filename": "../secret.srt"}, {**entry, "filename": "link.en.srt"},
            {**entry, "filename": ".."}, {**entry, "filename": "episode.en.srt"},
        ]}
        db.commit()
    base = f"/api/library/{item_id}/subtitle-tracks"
    with _client("alice") as client:
        assert [client.post(f"{base}/s:{n}/sync").status_code for n in range(3)] == [404, 404, 404]
        assert client.post(f"{base}/s:9/sync").status_code == 404
        fine = client.post(f"{base}/s:3/sync")
        assert fine.status_code == 202
        settled = _settle(client, fine.json()["id"])  # no speech reference for a placeholder file: it fails, harmlessly
    assert settled["state"] == "failed" and settled["transcript_id"] is None, settled
    with db_module.SessionLocal() as db:
        item = db.get(LibraryItem, item_id)
        assert [enrichment.resolve_track(db, item, f"s:{n}") for n in range(3)] == [None, None, None]  # the track is not found
        assert db.query(Transcript).filter(Transcript.library_item_id == item_id).count() == 0


def test_bulk_enrichment_route(household: None) -> None:
    first, second = _item("a.mkv"), _item("b.mkv")
    with db_module.SessionLocal() as db:
        root_id = db.query(MediaArtifact.root_id).first()[0]
    body = {"root_id": root_id, "kinds": ["segments"]}
    with _client("alice") as client:
        assert client.post("/api/admin/enrichment/bulk", json=body).status_code == 403
    with _client("admin") as client:
        assert client.post("/api/admin/enrichment/bulk", json=body).json() == {"queued": 2, "skipped": 0}
        assert client.post("/api/admin/enrichment/bulk", json=body).json() == {"queued": 0, "skipped": 2}
        assert client.post("/api/admin/enrichment/bulk", json={"root_id": root_id, "kinds": ["sync"]}).status_code == 422
        unconfigured = client.post("/api/admin/enrichment/bulk", json={"root_id": root_id, "kinds": ["asr"]})
        assert (unconfigured.status_code, unconfigured.json()["detail"]) == (409, "asr_not_configured")
    with db_module.SessionLocal() as db:
        assert sorted(job.library_item_id for job in db.query(AsrJob).all()) == sorted([first, second])


def test_captions_job_ingests_embedded_text_streams(household: None, monkeypatch: pytest.MonkeyPatch) -> None:
    item_id = _item()
    extracted: list[int] = []

    def fake_extract(db, item, stream_index):  # noqa: ANN001, ANN202
        extracted.append(stream_index)
        return b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello there\n"

    monkeypatch.setattr(enrichment, "_extract_stream", fake_extract)
    media = settings.library_root / "alice" / "episode.mkv"
    info = media.stat()
    with db_module.SessionLocal() as db:
        artifact = db.query(MediaArtifact).join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id).filter(
            LibraryItemArtifact.library_item_id == item_id).one()
        artifact.probe = {"fingerprint": f"{info.st_size}:{info.st_mtime_ns}", "streams": [
            {"index": 2, "type": "subtitle", "codec": "subrip", "language": "eng"},
            {"index": 3, "type": "subtitle", "codec": "hdmv_pgs_subtitle", "language": "eng"},
        ]}
        artifact.last_seen_run_id = "run-1"
        db.get(LibraryItem, item_id).title_id = str(uuid.uuid4())
        db.commit()
    enrichment.queue_captions_after_import("run-1")  # After-import hook
    assert enrichment.pump_pending() == 1
    with _client("alice") as client:
        (job_id,) = [job.id for job in db_module.SessionLocal().query(AsrJob).filter_by(kind="captions").all()]
        assert _settle(client, job_id)["state"] == "succeeded"
        resynced = client.post(f"/api/library/{item_id}/subtitle-tracks/e:2/sync")  # resolves through the mirror
        assert resynced.status_code == 202
        _settle(client, resynced.json()["id"])
    assert extracted == [2]  # the image stream is never extracted
    with db_module.SessionLocal() as db:
        (mirror,) = db.query(Transcript).filter_by(library_item_id=item_id, source_kind="source_caption").all()
        assert (mirror.derived_from, mirror.language) == ("stream:2", "eng")
        assert TranscriptService(db).subtitle_tracks(item_id) == []  # e:2 is already a track; no t: duplicate


def _srt(cues: list[tuple[int, int, str]]) -> str:
    def stamp(ms: int) -> str:
        return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    return "".join(f"{n}\n{stamp(s)} --> {stamp(e)}\n{text}\n\n" for n, (s, e, text) in enumerate(cues, 1))


def _sidecar_item(speech: list[tuple[int, int]]) -> tuple[str, list[tuple[int, int, str]]]:
    item_id = _item()
    cues = [(s + 2500, e + 2500, f"line {n}") for n, (s, e) in enumerate(speech)]
    (settings.library_root / "alice" / "episode.en.srt").write_text(_srt(cues))
    entry = {"language": "en", "forced": False, "hearing_impaired": False, "default": False, "format": "srt", "filename": "episode.en.srt"}
    with db_module.SessionLocal() as db:
        db.get(LibraryItem, item_id).metadata_json = {"lumina_subtitles": [entry]}
        db.commit()
    return item_id, cues


def test_sidecar_sync_resolves_through_its_mirror_and_lists_the_result(household: None) -> None:
    speech = _speech(seed=2)
    item_id, cues = _sidecar_item(speech)
    _store(item_id, language="english", source_kind="asr", cues=[(s, e, "speech") for s, e in speech])
    mirror = _store(item_id, language="en", source_kind="source_caption", cues=cues, derived_from="sidecar:episode.en.srt")
    with _client("alice") as client:
        done = _settle(client, client.post(f"/api/library/{item_id}/subtitle-tracks/s:0/sync").json()["id"])
    assert done["state"] == "succeeded", done
    with db_module.SessionLocal() as db:
        assert db.get(Transcript, done["transcript_id"]).derived_from == mirror
        assert [track.label for track in TranscriptService(db).subtitle_tracks(item_id)] == ["English (generated)", "English (synced)"]


def test_a_derived_row_is_never_taken_for_the_sidecar_mirror(household: None, monkeypatch: pytest.MonkeyPatch) -> None:
    speech = _speech(seed=3)
    item_id, cues = _sidecar_item(speech)
    _store(item_id, language="english", source_kind="asr", cues=[(s, e, "speech") for s, e in speech])
    monkeypatch.setattr(subtitle_translate, "chat", lambda config, messages, *, max_tokens: json.dumps({"cues": [
        {"id": cue["id"], "text": "x"} for cue in json.loads(re.search(r"<cues>\n(.*)\n</cues>", messages[1]["content"], re.S)[1])["cues"]
    ]}))
    _configure_ai()
    base = f"/api/library/{item_id}/subtitle-tracks/s:0"
    with _client("alice") as client:
        synced = _settle(client, client.post(f"{base}/sync").json()["id"])
        translated = _settle(client, client.post(f"{base}/translate", json={"target_language": "es"}).json()["id"])
    assert (synced["state"], translated["state"]) == ("succeeded", "succeeded")
    with db_module.SessionLocal() as db:
        assert db.get(Transcript, synced["transcript_id"]).derived_from == "sidecar:episode.en.srt"
        # the synced row carries sidecar:… too, but only a source_caption row is a mirror: translate reads the file
        assert [cue[:2] for cue in TranscriptService(db).cue_tuples(translated["transcript_id"])] == [cue[:2] for cue in cues]


def test_disabled_ai_features_are_409(household: None) -> None:
    item_id = _item()
    caption = _store(item_id, language="en", source_kind="source_caption", cues=[(0, 900, "Hello")])
    _configure_ai()
    with db_module.SessionLocal() as db:
        YtDlpService(db).ensure_app_settings().ai_features_disabled = ["sync", "translate", "subtitles_from_speech"]
        db.commit()
    base = f"/api/library/{item_id}/subtitle-tracks"
    with _client("alice") as client:
        responses = [
            client.post(f"{base}/t:{caption}/sync"),
            client.post(f"{base}/t:{caption}/translate", json={"target_language": "es"}),
            client.post(f"{base}/generate"),
        ]
    assert [(r.status_code, r.json()["detail"]) for r in responses] == [(409, "ai_feature_disabled")] * 3


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_embedded_captions_become_masked_transcript_tracks(household: None) -> None:
    folder = settings.library_root / "alice"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "embedded.srt").write_text("1\n00:00:00,500 --> 00:00:01,500\nWhat the shit\n\n")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=15:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-i", str(folder / "embedded.srt"),
         "-map", "0", "-map", "1", "-map", "2", "-c:v", "mpeg4", "-c:a", "aac", "-c:s", "srt", str(folder / "embedded.mkv")],
        check=True, timeout=60,
    )
    item_id = _item("embedded.mkv")
    assert enrichment.queue_captions_after_import in library_import.after_import_hooks
    with db_module.SessionLocal() as db:
        enrichment.queue_bulk(db, [item_id], ["captions"], None)
    assert enrichment.pump_pending() == 1
    with _client("alice") as client:
        (job_id,) = [job.id for job in db_module.SessionLocal().query(AsrJob).filter_by(kind="captions").all()]
        assert _settle(client, job_id)["state"] == "succeeded"
        asr = _store(item_id, language="english", source_kind="asr", cues=[(500, 1500, "what the shit")])
        tracks = client.get(f"/api/library/{item_id}/subtitle-tracks").json()
        assert [track["id"] for track in tracks] == ["e:2", f"t:{asr}"]  # the captions mirror is not listed twice
        assert "What the shit" in client.get(f"/api/library/{item_id}/subtitle-tracks/e:2.vtt").text
        assert "what the shit" in client.get(f"/api/library/{item_id}/subtitle-tracks/t:{asr}.vtt").text
        with db_module.SessionLocal() as db:
            record = db.query(UserSettings).filter_by(user_id="alice").one_or_none() or UserSettings(id=str(uuid.uuid4()), user_id="alice")
            record.ui_prefs = {"profanity": {"enabled": True, "words": []}}
            db.add(record)
            db.commit()
        assert "What the ****" in client.get(f"/api/library/{item_id}/subtitle-tracks/e:2.vtt").text
        assert "what the ****" in client.get(f"/api/library/{item_id}/subtitle-tracks/t:{asr}.vtt").text
    with _client("bob") as client:
        assert client.get(f"/api/library/{item_id}/subtitle-tracks/t:{asr}.vtt").status_code == 404
