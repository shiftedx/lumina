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
    assert '<img src="https://lumina.example.com/email/mark-v1.png" width="36" height="28" alt="Lumina"' in html
    assert "https://lumina.example.com/title/t-9" in text and "https://lumina.example.com/title/t-9" in html
    assert "lumina.example.com" in text and "Settings → Requests" in html


def test_compose_text_part_mirrors_the_blocks() -> None:
    text, html = email_layout.compose(subject="S", preheader="P", eyebrow="E", heading="H", why="W", paragraphs=("Body",),
                                      note=("Reason", "R"), button=("Go", "https://x.test/a?b=1&c=2"))
    assert text.startswith("H\n\nBody\n\nReason: R\n\nGo:\nhttps://x.test/a?b=1&c=2") and "W" in text
    assert 'href="https://x.test/a?b=1&amp;c=2"' in html and ">P" in html  # the preheader


def test_facts_are_escaped_and_available_without_images() -> None:
    text, html = email_layout.compose(subject="S", preheader="P", eyebrow="Security notice", heading="Codes paused", why="W",
                                      facts=((EVIL, EVIL), ("Pause", "15 minutes")), status="security", button=("Review member", "https://x.test/member"))
    assert f"{EVIL}: {EVIL}" in text and "Pause: 15 minutes" in text
    assert "<script>" not in html and html.count("&lt;script&gt;") == 2
    assert "15 minutes" in html and ">Review member" in html


def test_media_vault_art_is_public_and_request_progress_is_live_text() -> None:
    emails = every_email(title="Arrival", year=2016, poster_url="https://image.tmdb.org/poster.jpg")
    assert "https://lumina.example.com/email/vault-v1.jpg" in emails[0][2]
    assert "https://lumina.example.com/email/security-keys-v1.jpg" in emails[6][2]
    assert "https://lumina.example.com/email/film-archive-v1.jpg" in emails[7][2]
    for index in (1, 2, 4):
        assert all(label in emails[index][2] for label in ("Requested", "Approved", "Ready"))
    assert "Watch now" not in emails[3][2] and "Watch now" not in emails[5][2]
    assert all("border:1px solid" not in html and "border-left:" not in html for _, _, html in emails)


def test_invitation_copy_includes_friends_and_keeps_the_link_out_of_visible_html_copy() -> None:
    link = "https://lumina.example.com/invite#invite=private-link"
    _, text, html = invite_email.render(inviter="Maya", server="Lumina", libraries=["Films"], link=link, expires=datetime(2026, 10, 12))
    assert "Maya invited you to Lumina" in text and "October 12, 2026" in text and link in text
    assert "household" not in text and "Accept invitation" in html
    assert f'Paste this into your browser: {link}' not in html
    assert "Single use" in text and "Single use" in html


def test_only_an_available_library_title_gets_a_watch_action() -> None:
    request = MediaRequest(id="r", kind="movie", media_type="movie", tmdb_id=603, requested_by="u", title="Arrival")
    _, text, html = notify.request_email(request, "available", "Maya")
    assert "View request" in text and "Watch now" not in html
    request.library_title_id = "title-9"
    _, text, html = notify.request_email(request, "available", "Maya")
    assert "Watch now" in text and "https://lumina.example.com/title/title-9" in html


def test_subject_names_cannot_carry_header_line_breaks() -> None:
    subject, _, _ = invite_email.render(inviter="Maya\r\nBcc: Other", server="Lumina", libraries=[], link="https://lumina.example.com/invite", expires=datetime(2026, 10, 12))
    assert "\n" not in subject and "\r" not in subject
    subject, _, _ = two_factor.lock_email(User(id="u", username="sam", display_name="Sam\nOther"))
    assert "\n" not in subject


def test_empty_library_names_do_not_promise_unrestricted_access() -> None:
    _, text, html = invite_email.render(inviter="Maya", server="Lumina", libraries=[], link="https://lumina.example.com/invite", expires=datetime(2026, 10, 12))
    assert "Everything" not in text and "Shared with you" not in html


def test_email_variants_share_the_brand_accent_and_neutral_actions() -> None:
    for _, _, html in every_email(title="Example film", year=2026):
        rendered = html.lower()
        assert "#dbb688" in rendered
        assert not any(retired in rendered for retired in ("#e5a00d", "#f0b43a", "#8a5a00"))
        if "<v:roundrect" in rendered:
            assert 'fillcolor="#efe9dd"' in rendered
            assert "background:#efe9dd;color:#0f0e0c" in rendered
