"""Every email Lumina sends: both parts present, every value escaped, nothing unfilled, the logo from the public address."""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from app.models import MediaRequest, User
from app.services import email_layout, invite_email, public_address, two_factor
from app.services.requests import notify

EVIL = '<script>alert(1)</script>"&'


def every_email(**req: object) -> list[tuple[str, str, str]]:
    request = MediaRequest(id="r1", kind="movie", media_type="movie", tmdb_id=603, requested_by="u", **req)
    return [
        invite_email.render(inviter=EVIL, server="Lumina", libraries=[EVIL], link="https://lumina.example.com/#invite=t",
                            expires=datetime(2026, 10, 12)),
        *(notify.request_email(request, event, EVIL) for event in notify.COPY),
        two_factor.lock_email(User(id="u1", username=EVIL, display_name=EVIL)),
        notify.smtp_test_email(),
    ]


@pytest.fixture(autouse=True)
def public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public_address, "_origin", "https://lumina.example.com")


def test_every_value_is_escaped_and_nothing_unfilled_leaks() -> None:
    emails = every_email(title=EVIL, year=None, poster_url=None, decline_reason=EVIL)
    assert len(emails) == 8
    for subject, text, html in emails:
        assert subject and text.strip() and html.startswith("<!doctype html>")
        assert "<script>" not in html
        if subject != "Lumina test email":
            assert "&lt;script&gt;" in html
        for part in (subject, text, html):
            assert "None" not in part and not re.search(r"\{\w*\}", part)
    declined = emails[3][2]
    assert declined.count("&lt;script&gt;alert(1)&lt;/script&gt;&quot;&amp;") >= 3  # heading, title tag, reason


def test_poster_is_https_only_with_alt_text() -> None:
    ok = every_email(title="The Matrix", year=1999, poster_url="https://image.tmdb.org/t/p/w500/m.jpg")[2][2]
    assert '<img src="https://image.tmdb.org/t/p/w500/m.jpg"' in ok and 'alt="The Matrix"' in ok
    for bad in ("http://image.tmdb.org/m.jpg", "javascript:alert(1)", "/api/art/m.jpg"):
        html = every_email(title="The Matrix", year=1999, poster_url=bad)[2][2]
        assert bad not in html


def test_logo_and_links_use_the_public_address() -> None:
    _, text, html = every_email(title="Arrival", year=2016, poster_url=None, library_title_id="t-9")[4]
    assert '<img src="https://lumina.example.com/email-mark.png" width="36" height="36" alt="Lumina"' in html
    assert "https://lumina.example.com/title/t-9" in text and "https://lumina.example.com/title/t-9" in html
    assert "lumina.example.com" in text and "Settings → Requests" in html


def test_compose_text_part_mirrors_the_blocks() -> None:
    text, html = email_layout.compose(subject="S", preheader="P", eyebrow="E", heading="H", why="W", paragraphs=("Body",),
                                      note=("Reason", "R"), button=("Go", "https://x.test/a?b=1&c=2"))
    assert text.startswith("H\n\nBody\n\nReason: R\n\nGo:\nhttps://x.test/a?b=1&c=2") and "W" in text
    assert 'href="https://x.test/a?b=1&amp;c=2"' in html and ">P" in html  # the preheader
