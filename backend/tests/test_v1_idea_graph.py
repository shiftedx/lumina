"""The idea graph keeps only concepts/relations grounded in cited transcript lines, bounded and scoped."""
import time

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import IdeaGraph, LibraryItem, Summary, User
from app.security import get_current_user
from app.services import idea_graph
from app.services.idea_graph import MAX_NODES, validate_graph
from app.services.summaries import SummaryError
from app.services.transcripts import TranscriptService
from app.services.yt_dlp_service import YtDlpService
from test_v1_ai_config import MODEL, FakeAi
from test_v1_summary_worker import INJECTION, reply

OWNER = User(id="owner", username="owner", display_name="Owner", role="viewer", is_active=True)
OTHER = User(id="other", username="other", display_name="Other", role="admin", is_active=True)

GRAPH = {
    "concepts": [
        {"id": "a", "label": "Stone bridge", "cue_ordinals": [2]},
        {"id": "b", "label": "Spring floods", "cue_ordinals": [2, 99]},
        {"id": "c", "label": "Invented", "cue_ordinals": [99]},
        {"id": "d", "label": "stone BRIDGE", "cue_ordinals": [0]},
        {"id": "a", "label": "Duplicate id", "cue_ordinals": [0]},
    ],
    "relations": [
        {"from": "b", "to": "a", "label": "washes away", "cue_ordinals": [2]},
        {"from": "a", "to": "c", "label": "to dropped", "cue_ordinals": [2]},
        {"from": "a", "to": "b", "label": "no evidence", "cue_ordinals": [7]},
        {"from": "a", "to": "a", "label": "self", "cue_ordinals": [2]},
    ],
}


def test_graph_edges_have_evidence() -> None:
    result = validate_graph(GRAPH, {0: 0, 2: 2000})
    assert result["nodes"] == [
        {"id": "n0", "label": "Stone bridge", "cue_ordinals": [2], "start_ms": 2000},
        {"id": "n1", "label": "Spring floods", "cue_ordinals": [2], "start_ms": 2000},
    ]
    assert result["edges"] == [{"source": "n1", "target": "n0", "label": "washes away", "cue_ordinals": [2], "start_ms": 2000}]
    assert result["dropped"] == 6
    for bad in (None, [], {"concepts": [{"id": "x", "label": "x", "cue_ordinals": [5]}]}):
        with pytest.raises(SummaryError):
            validate_graph(bad, {0: 0})


def test_graph_bounded_render() -> None:
    dense = {
        "concepts": [{"id": f"c{i}", "label": f"Concept {i}", "cue_ordinals": [0]} for i in range(60)],
        "relations": [{"from": f"c{i}", "to": f"c{j}", "label": "links", "cue_ordinals": [0]} for i in range(24) for j in range(24) if i != j],
    }
    result = validate_graph(dense, {0: 0})
    assert len(result["nodes"]) == MAX_NODES and len(result["edges"]) == idea_graph.MAX_EDGES
    assert [node["id"] for node in result["nodes"]] == [f"n{i}" for i in range(MAX_NODES)]  # stable ids


@pytest.fixture
def env():
    fake = FakeAi()
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model = fake.url, MODEL
        db.add_all([
            User(**{k: getattr(OWNER, k) for k in ("id", "username", "display_name", "role", "is_active")}),
            User(**{k: getattr(OTHER, k) for k in ("id", "username", "display_name", "role", "is_active")}),
            LibraryItem(id="priv", user_id="owner", visibility="private", title="p", metadata_json={}, status="available"),
        ])
    with db_module.session_scope() as db:
        transcript = TranscriptService(db).store(
            "priv", language="en", source_kind="source_caption",
            cues=[(0, 900, "Welcome"), (1000, 1900, INJECTION), (2000, 2900, "The bridge floods every spring"), (3000, 3900, "Uncited line")],
        )
        db.add(Summary(
            id="sum", library_item_id="priv", transcript_id=transcript.id, transcript_revision=transcript.revision, model_id=MODEL, state="succeeded",
            overview="o", key_points=[{"text": "Bridge floods", "cue_ordinals": [1, 2], "start_ms": 1000}], chapters=[{"title": "Intro", "cue_ordinal": 0, "start_ms": 0}],
        ))
    current = {"user": OWNER}
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    try:
        yield TestClient(app, base_url="http://localhost"), fake, current, transcript
    finally:
        app.dependency_overrides.clear()
        fake.server.shutdown()
        fake.server.server_close()


def _settle(http: TestClient, graph_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = http.get(f"/api/idea-graphs/{graph_id}").json()
        if body["state"] not in {"queued", "running"}:
            return body
        time.sleep(0.02)
    raise AssertionError("graph did not settle")


def test_graph_private_scope_and_revision(env) -> None:
    http, fake, current, transcript = env
    fake.handler = lambda *_: reply(GRAPH)
    started = http.post("/api/library/priv/idea-graphs")
    assert started.status_code == 202
    done = _settle(http, started.json()["id"])
    assert done["state"] == "succeeded" and done["summary_id"] == "sum" and done["transcript_revision"] == transcript.revision
    assert [node["label"] for node in done["nodes"]] == ["Stone bridge", "Spring floods"]
    assert http.post("/api/library/priv/idea-graphs").status_code == 200  # cached for this summary + model
    assert http.get("/api/library/priv/idea-graph").json()["id"] == done["id"]

    (_, _, _, body), = fake.requests
    assert set(body) == {"model", "messages", "max_tokens", "temperature", "stream"}
    evidence = body["messages"][1]["content"]
    assert INJECTION in evidence and INJECTION not in body["messages"][0]["content"]
    assert "Uncited line" not in evidence  # only the summary's cited lines reach the model

    current["user"] = OTHER
    assert http.post("/api/library/priv/idea-graphs").status_code == 404
    assert http.get("/api/library/priv/idea-graph").status_code == 404
    assert http.get(f"/api/idea-graphs/{done['id']}").status_code == 404

    current["user"] = OWNER
    fake.handler = lambda *_: reply("no json")
    with db_module.session_scope() as db:
        db.query(IdeaGraph).delete()
    failed = _settle(http, http.post("/api/library/priv/idea-graphs").json()["id"])
    assert failed["state"] == "failed" and failed["error"] == "Model output is not a valid idea graph"
    with db_module.session_scope() as db:
        db.query(Summary).delete()
    assert http.post("/api/library/priv/idea-graphs").status_code == 409
