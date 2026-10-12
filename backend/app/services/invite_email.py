"""The invite email, in the shared layout (email_layout): a text part and an HTML part. Every name is escaped there."""
from __future__ import annotations

from datetime import datetime

from app.services import email_layout


def render(*, inviter: str, server: str, libraries: list[str], link: str, expires: datetime) -> tuple[str, str, str]:
    """(subject, text, html). ``libraries`` are human names; empty means everything the household shares."""
    when = f"{expires:%B} {expires.day}, {expires.year}"
    subject = f"{inviter} invited you to {server}"
    text, html = email_layout.compose(
        subject=subject,
        preheader=f"Join {inviter} on {server}. The link works once and expires on {when}.",
        eyebrow="You're invited",
        heading=subject,
        paragraphs=(f"{server} is where {inviter}'s household keeps its films, shows and more. "
                    "Accept the invitation, choose a password, and pull up a chair.",),
        listing=("You can watch", libraries or ["Everything the household shares"]),
        button=("Accept invitation", link),
        fineprint=(f"This link works once and expires on {when}.", f"Button not working? Paste this into your browser: {link}"),
        why=f"You're getting this because {inviter} invited this address to {server}. Not expecting it? Ignore this email "
            "and nothing happens.",
    )
    return subject, text, html
