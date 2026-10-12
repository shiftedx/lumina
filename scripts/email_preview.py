#!/usr/bin/env python3
"""Render every email Lumina sends, with sample data, into one self-contained HTML page for eyeballing.

    backend/.venv/bin/python scripts/email_preview.py /tmp/email-preview.html

Each email sits in an iframe at desktop width and at 375 px; the invite also shows the forced-light palette, and one
request shows how it reads with images blocked. The logo is inlined as a data: URI so the page renders offline; posters
are the public TMDB images the request flow stores.
"""
from __future__ import annotations

import base64
import sys
from datetime import datetime
from html import escape
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY / "backend"))

from app.models import MediaRequest, User  # noqa: E402
from app.services import email_layout, invite_email, public_address, two_factor  # noqa: E402
from app.services.requests import notify  # noqa: E402

BASE = "https://lumina.example.com"
POSTER = "https://image.tmdb.org/t/p/w342/{}.jpg"
LIGHT = "@media (prefers-color-scheme: light)"


def request(event: str, title: str, year: int | None, kind: str, poster: str | None, **extra: object) -> MediaRequest:
    return MediaRequest(id=f"r-{event}", kind=kind, media_type="movie" if kind == "movie" else "tv", title=title, year=year,
                        poster_url=POSTER.format(poster) if poster else None, requested_by="u", **extra)


def emails() -> list[tuple[str, tuple[str, str, str]]]:
    public_address.set_origin(BASE)
    return [
        ("Invite", invite_email.render(inviter="Maya", server=email_layout.SERVER_NAME, libraries=["Films", "Series", "Anime"],
                                       link=f"{BASE}/#invite=Qm9vdHN0cmFwLXNhbXBsZS10b2tlbg", expires=datetime(2026, 10, 12))),
        ("Request: pending (admins)", notify.request_email(
            request("pending", "The Matrix", 1999, "movie", "f89U3ADr1oiB1s9GkdPOEpXUk5H", tmdb_id=603), "pending", "Jordan")),
        ("Request: approved", notify.request_email(
            request("approved", "Game of Thrones", 2011, "show", "1XS1oqL89opfnbLl8WnZY1O1uJx", tmdb_id=1399, seasons=[1, 2]),
            "approved", "Jordan")),
        ("Request: declined", notify.request_email(
            request("declined", "Spirited Away", 2001, "anime", "39wmItIWsg5sZMyRUHLkWBcuVCM", anilist_id=199,
                    decline_reason="It's already in the vault: look under Anime, in the Studio Ghibli collection."),
            "declined", "Jordan")),
        ("Request: available", notify.request_email(
            request("available", "Arrival", 2016, "movie", "x2FJsf1ElAgr63Y3PNPtJrcmpoe", tmdb_id=329865, library_title_id="t-1"),
            "available", "Jordan")),
        ("Request: failed (no poster)", notify.request_email(
            request("failed", "Planet Earth II", 2016, "show", None, tmdb_id=68595, seasons="all"), "failed", "Jordan")),
        ("Two-step lockout (owners)", two_factor.lock_email(User(id="u-sam", username="sam", display_name="Sam"))),
        ("SMTP test", notify.smtp_test_email()),
    ]


def frame(html: str, width: int, *, light: bool = False, blocked: bool = False) -> str:
    logo = "data:image/png;base64," + base64.b64encode((REPOSITORY / "frontend/public/email-mark.png").read_bytes()).decode()
    html = html.replace(f"{BASE}{email_layout.LOGO_PATH}", logo).replace(LIGHT, "@media all" if light else "@media not all")
    if blocked:
        html = html.replace(' src="', ' data-src="')
    return (f'<iframe title="preview" width="{width}" style="width:{width}px" srcdoc="{escape(html, quote=True)}" '
            'onload="this.style.height=\'0\';this.style.height=this.contentDocument.documentElement.scrollHeight+\'px\'"></iframe>')


def card(name: str, subject: str, frames: list[tuple[str, str]]) -> str:
    cells = "".join(f'<figure><figcaption>{escape(label)}</figcaption>{body}</figure>' for label, body in frames)
    return f'<section><p class="name">{escape(name)}</p><h2>{escape(subject)}</h2><div class="row">{cells}</div></section>'


def page() -> str:
    rendered = emails()
    sections = [card(name, subject, [("Desktop", frame(html, 640)), ("Phone · 375 px", frame(html, 375))])
                for name, (subject, _, html) in rendered]
    invite, available = rendered[0][1], rendered[4][1]
    sections.insert(1, card("Invite · forced light", invite[0], [("Desktop", frame(invite[2], 640, light=True)),
                                                                ("Phone · 375 px", frame(invite[2], 375, light=True))]))
    sections.insert(6, card("Request: available · images blocked", available[0], [("Desktop", frame(available[2], 640, blocked=True)),
                                                                                  ("Phone · 375 px", frame(available[2], 375, blocked=True))]))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lumina Email Templates</title>
<style>
body {{ margin:0; background:#0a0908; color:#efe9dd; font:400 14px/1.45 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
main {{ max-width:1120px; margin:0 auto; padding:48px 16px 96px; }}
h1 {{ font:italic 600 44px/1 Georgia, serif; margin:0 0 8px; letter-spacing:-.015em; }}
.lede {{ color:#9d968a; margin:0 0 40px; }}
section {{ border-top:1px solid #2a2722; padding:32px 0 8px; }}
.name {{ margin:0; color:#f0b43a; font-weight:600; font-size:11px; letter-spacing:.18em; text-transform:uppercase; }}
h2 {{ font:italic 600 24px/1.2 Georgia, serif; margin:6px 0 20px; }}
h2::before {{ content:'Subject: '; font:400 13px/1 -apple-system, sans-serif; color:#9d968a; font-style:normal; }}
.row {{ display:flex; gap:32px; align-items:flex-start; flex-wrap:wrap; }}
figure {{ margin:0; max-width:100%; overflow-x:auto; }}
figcaption {{ color:#9d968a; font-size:12px; margin-bottom:8px; }}
iframe {{ border:1px solid #2a2722; display:block; height:900px; background:#0f0e0c; }}
</style></head><body><main>
<h1>Lumina Email Templates</h1>
<p class="lede">Every email Lumina sends, rendered with sample data. Fonts fall back to Georgia, as in Gmail.</p>
{"".join(sections)}
</main></body></html>"""


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(page(), encoding="utf-8")
