"""Pre-S72 leak fixes: redaction gaps, search caps, AI default (audit findings 9/11/13/19)."""
from __future__ import annotations

from app.config import EnvSettings
from app.services.library_search import MAX_MATCH_TOKENS, build_match
from app.services.redaction import redact


def test_redaction_covers_ai_key_api_key_headers_discord_webhook_and_lsig() -> None:
    text = (
        "ai_api_key=hunter2\nX-Api-Key: k1\nApi-Key: k2\n"
        "https://discord.com/api/webhooks/123456/AbC-def_ghi "
        "https://cdn.example/v.mp4?lsig=zz9&token=abc123&Signature=qq"
    )
    out = redact(text)
    for secret in ("hunter2", "k1", "k2", "AbC-def_ghi", "zz9", "abc123", "qq"):
        assert secret not in out, (secret, out)
    assert "/api/webhooks/123456/[redacted]" in out


def test_redaction_paths_hides_quoted_paths_with_spaces() -> None:
    message = "ERROR: unable to open '/data/.staging/9f1c/Secret Title [abc].f137.mp4.part' and /srv/lib/x.mp4"
    out = redact(message, paths=True)
    assert "Secret" not in out and "/srv" not in out
    assert "'[path]'" in out


def test_build_match_caps_tokens() -> None:
    query = " ".join(f"t{i}" for i in range(5000))
    assert build_match([query]).count(" OR ") == MAX_MATCH_TOKENS - 1
    assert build_match(["  "]) is None
    assert build_match(["a b a"]) == "a* OR b*"


def test_owner_dto_has_no_server_path_and_search_rejects_overlong_queries(db_factory, api_client) -> None:
    from app.models import LibraryItem, User

    owner = User(id="leak-owner", username="owner", display_name="Owner", role="admin", is_active=True)
    with db_factory.begin() as session:
        session.add_all([owner, LibraryItem(
            id="leak-item", user_id=owner.id, visibility="private", title="Mine",
            file_path="/srv/media/owner/secret.mp4", metadata_json={"filepath": "/srv/media/owner/secret.mp4"}, status="available",
        )])

    client = api_client(user=owner, base_url="http://localhost")
    detail = client.get("/api/library/leak-item")
    assert detail.status_code == 200
    assert "/srv/media" not in detail.text
    assert client.get("/api/library?search=" + "x" * 200).status_code == 200
    assert client.get("/api/library?search=" + "x" * 201).status_code == 422
    assert client.get("/api/search?q=" + "x" * 201).status_code == 422


def test_ai_endpoint_has_no_code_default(monkeypatch) -> None:
    monkeypatch.delenv("LUMINA_AI_BASE_URL", raising=False)
    monkeypatch.delenv("LUMINA_AI_MODEL", raising=False)
    fresh = EnvSettings(_env_file=None)
    assert fresh.ai_base_url == "" and fresh.ai_model == ""
