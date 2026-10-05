"""S72 release gate: cross-user privacy matrix over the by-id surfaces the browser matrix
(frontend/e2e/realstack/privacy.spec.ts) cannot seed, plus shipping boundaries.

Third-party auth absence is `test_v1_public_options.py::test_auth_profile_surface_absent`;
the public network boundary is `test_public_source_policy.py` + `test_v1_security_fixes.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.models import DownloadJob, IdeaGraph, LibraryItem, LiveRecording, Summary
from app.services.transcripts import TranscriptService
from support import make_user

REPO = Path(__file__).resolve().parents[2]
SECRETS = ("secret-title", "secret-cue", "secret-note", "secret-shelf", "secret-watch", "secret-graph", "secret-point")


def test_privacy_matrix_all_surfaces(db_factory, api_client, monkeypatch) -> None:
    from app.main import live_recording_manager

    monkeypatch.setattr(live_recording_manager, "session_factory", db_factory)
    alice, bob, admin = make_user("alice"), make_user("bob"), make_user("root", role="admin")
    with db_factory() as db:
        db.add_all([alice, bob, admin])
        db.add(LibraryItem(id="priv", user_id="alice", visibility="private", title="secret-title", metadata_json={}, status="available"))
        db.commit()
        transcript = TranscriptService(db).store("priv", language="en", source_kind="source_caption", cues=[(0, 900, "secret-cue")])
        db.add_all([
            Summary(id="sum", library_item_id="priv", transcript_id=transcript.id, transcript_revision=transcript.revision, model_id="m",
                    state="succeeded", overview="o", key_points=[{"text": "secret-point", "cue_ordinals": [0], "start_ms": 0}], chapters=[]),
            IdeaGraph(id="graph", library_item_id="priv", summary_id="sum", transcript_id=transcript.id, transcript_revision=transcript.revision,
                      model_id="m", state="succeeded", nodes=[{"id": "n", "label": "secret-graph", "cue_ordinals": [0], "start_ms": 0}], edges=[]),
            LiveRecording(id="rec", user_id="alice", source_url="https://example.com/secret-watch", source_identity="youtube:x",
                          source_identity_key="x", status="partial", library_item_id="priv"),
            DownloadJob(id="job", user_id="alice", source_url="https://example.com/secret-watch", status="failed", format_selection={},
                        output_profile={}, error="ERROR: [generic] secret-title: HTTP 403 for https://example.com/secret-watch"),
        ])
        db.commit()
    current = {"user": alice}
    client = api_client(user=lambda: current["user"], base_url="http://localhost")
    created = client.post("/api/library/priv/notes", json={"body": "secret-note", "visibility": "household"})
    assert created.status_code in (200, 201), (created.status_code, created.text)
    note = created.json()["id"]
    shelf = client.post("/api/collections", json={"name": "secret-shelf"}).json()["id"]
    assert client.post(f"/api/collections/{shelf}/items/priv").status_code == 200
    tid = transcript.id

    denied = [
        ("GET", "/api/library/priv"), ("GET", "/api/library/priv/notes"), ("PUT", f"/api/library/notes/{note}"),
        ("DELETE", f"/api/library/notes/{note}"), ("GET", "/api/library/priv/provenance"), ("GET", "/api/library/priv/transcripts"),
        ("GET", f"/api/transcripts/{tid}/cues"), ("GET", f"/api/transcripts/{tid}/search?q=secret"), ("GET", "/api/library/priv/summary"),
        ("GET", "/api/summaries/sum"), ("GET", "/api/library/priv/idea-graph"), ("GET", "/api/idea-graphs/graph"),
        ("POST", "/api/library/priv/idea-graphs"), ("POST", "/api/library/priv/summaries"), ("POST", "/api/library/priv/transcripts/asr"),
        ("GET", "/api/live-recordings/rec"), ("PUT", "/api/live-recordings/rec/keep"), ("POST", "/api/live-recordings/rec/stop"),
        ("POST", "/api/live-recordings/rec/cancel"), ("GET", f"/api/collections/{shelf}"), ("PUT", f"/api/collections/{shelf}/name"),
        ("POST", f"/api/collections/{shelf}/remote-items"), ("DELETE", f"/api/collections/{shelf}"), ("POST", "/api/jobs/job/retry"),
        ("POST", "/api/jobs/job/cancel"), ("POST", "/api/library/priv/playback-sessions"),
    ]
    bodies = {"notes": {"body": "x", "visibility": "household"}, "keep": {"kept": True}, "name": {"name": "x"}, "remote-items": {"url": "https://example.com/v"}, "summaries": {}}
    lists = ["/api/library", "/api/library?search=secret", "/api/library/groups?kind=episode", "/api/search?q=secret", "/api/collections",
             "/api/live-recordings", "/api/jobs", "/api/me/export", "/api/me/watch-queue"]
    for user in (bob, admin):
        current["user"] = user
        for method, url in denied:
            response = client.request(method, url, json=next((b for key, b in bodies.items() if method != "DELETE" and key in url.split("/")[-2:]), None))
            assert response.status_code in (403, 404), (user.id, method, url, response.status_code)
            assert not [s for s in SECRETS if s in response.text], (user.id, url)
        for url in lists:
            response = client.get(url)
            assert response.status_code == 200, (user.id, url, response.status_code)
            assert not [s for s in SECRETS if s in response.text], (user.id, url, response.text[:300])

    # The admin task view keeps another member's failure reason but never their title or source.
    tasks = client.get("/api/admin/tasks?kind=download").json()["items"]
    assert [t["error"] for t in tasks] == ["ERROR: [source] HTTP 403 for [source]"]

    current["user"] = alice  # nothing above changed the owner's data
    assert [n["body"] for n in client.get("/api/library/priv/notes").json()] == ["secret-note"]
    assert client.get(f"/api/collections/{shelf}").json()["name"] == "secret-shelf"
    assert client.get("/api/live-recordings/rec").json()["kept"] is False


def test_realstack_runner_never_ships() -> None:
    """The S71 fixture launcher (loopback guard, wiped temp roots) stays a test tool."""
    app_source = "\n".join(path.read_text() for path in (REPO / "backend" / "app").rglob("*.py"))
    assert not re.search(r"run_realstack|test_safety|REALSTACK", app_source)
    copies = re.findall(r"^COPY\s+(?:--\S+\s+)*(\S+)", (REPO / "Dockerfile").read_text(), re.MULTILINE)
    assert copies and not [source for source in copies if source.startswith("scripts")], copies
