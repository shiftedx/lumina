"""App polish 6.2: the device ring cookie helpers are pure and refuse anything that is not a session bearer."""
from __future__ import annotations

import secrets

from fastapi import Response

from app import device_ring
from app.config import settings


def token() -> str:
    return secrets.token_urlsafe(48)


def test_parse_keeps_well_formed_tokens_drops_the_rest_dedupes_and_keeps_the_last_six() -> None:
    tokens = [token() for _ in range(8)]
    hostile = ".".join(["", "..", "<script>", "a;b", tokens[0], tokens[0], "x" * 20, *tokens[1:], "y" * 200])
    assert device_ring.parse_ring(hostile) == tokens[2:]
    assert device_ring.parse_ring(None) == []
    assert device_ring.parse_ring("") == []
    assert device_ring.parse_ring("." * 10_000) == []


def test_append_moves_a_repeat_to_the_end_and_evicts_the_oldest() -> None:
    tokens = [token() for _ in range(6)]
    fresh = token()
    assert device_ring.append_token(tokens, fresh) == [*tokens[1:], fresh]
    assert device_ring.append_token(tokens, tokens[0]) == [*tokens[1:], tokens[0]]
    assert device_ring.drop_token(tokens, tokens[2]) == [*tokens[:2], *tokens[3:]]


def test_serialise_round_trips() -> None:
    tokens = [token() for _ in range(3)]
    assert device_ring.parse_ring(device_ring.serialize_ring(tokens)) == tokens


def test_the_cookie_is_http_only_on_the_session_path_and_mirrors_the_session_cookie(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    monkeypatch.setattr(settings, "session_cookie_samesite", "strict")
    response = Response()
    device_ring.apply_ring_cookie(response, [token()])
    header = response.headers["set-cookie"].lower()
    assert header.startswith(f"{settings.session_cookie_name}_ring=".lower())
    for part in ("httponly", "path=/api/session", "secure", "samesite=strict", f"max-age={settings.session_duration_hours * 3600}"):
        assert part in header


def test_an_empty_ring_deletes_the_cookie() -> None:
    response = Response()
    device_ring.apply_ring_cookie(response, [])
    header = response.headers["set-cookie"].lower()
    assert "max-age=0" in header and "path=/api/session" in header


def test_the_empty_ring_deletion_carries_secure_and_samesite(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    monkeypatch.setattr(settings, "session_cookie_samesite", "strict")
    response = Response()
    device_ring.apply_ring_cookie(response, [])
    header = response.headers["set-cookie"].lower()
    assert "secure" in header and "samesite=strict" in header and "httponly" in header


def test_a_cookie_cut_off_by_the_length_cap_never_yields_a_partial_token() -> None:
    whole = [token() for _ in range(3)]
    head = ".".join(whole) + "."
    # The cap cuts 40 characters into a 100-character segment; the 40-character fragment is token-shaped.
    gap = "!" * (device_ring._MAX_COOKIE_CHARS - 40 - len(head) - 1)
    value = head + gap + "." + "B" * 100
    parsed = device_ring.parse_ring(value)
    assert parsed == whole


def test_an_owner_marker_parses_verifies_and_refuses_forgeries() -> None:
    marker = device_ring.owner_marker("3f2b8c1e-0000-4000-8000-000000000001")
    assert device_ring.parse_ring(f"{token()}.{marker}")[-1] == marker
    assert device_ring.marker_user_id(marker) == "3f2b8c1e-0000-4000-8000-000000000001"
    forged = "someone-else~" + marker.split("~")[1]
    assert device_ring.marker_user_id(forged) is None
    assert device_ring.marker_user_id(token()) is None
