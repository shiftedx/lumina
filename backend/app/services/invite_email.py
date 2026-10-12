"""The invite email, in the shared layout (email_layout): a text part and an HTML part. Every name is escaped there."""
from __future__ import annotations

from datetime import datetime

from app.services import email_layout


def render(*, inviter: str, server: str, libraries: list[str], link: str, expires: datetime) -> tuple[str, str, str]:
    """(subject, text, html). Only supplied library names are listed; an empty list makes no access promise."""
    when = f"{expires:%B} {expires.day}, {expires.year}"
    subject = f"{' '.join(inviter.split())} invited you to {' '.join(server.split())}"
    text, html = email_layout.compose(
        subject=subject,
        preheader=f"{inviter} shared a media vault with you. Accept by {when}.",
        eyebrow="You're invited",
        heading="Step inside the vault.",
        paragraphs=(f"{inviter} invited you to {server}. Movies, music, and favorites, ready to discover.",),
        facts=(("Accept by", when), ("Access link", "Single use")),
        status="invite",
        listing=("Shared with you", libraries) if libraries else None,
        button=("Accept invitation", link),
        fineprint=("Choose your password when you accept. Your invitation is personal; keep the link private.",),
        why=f"You're getting this because {inviter} invited this address to {server}. Not expecting it? Ignore this email "
            "and nothing happens.",
    )
    return subject, text, html
