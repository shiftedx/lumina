"""Request emails, in the shared layout (email_layout): stdlib smtplib on a daemon thread, after the request's change is durable.

A failure is logged by exception type only (never the password, server reply or address) and never touches request flow.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSettings, MediaRequest, MediaRequestFollower, User, UserSettings
from app.persistence import queue_after_commit
from app.services import email_layout, public_address

logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 20
SUBJECTS = {
    "pending": "New request: {title}",
    "approved": "Approved: {title}",
    "declined": "Declined: {title}",
    "available": "Ready to watch: {title}",
    "failed": "Request problem: {title}",
}
KIND_LABEL = {"movie": "Film", "show": "Series", "anime": "Anime"}  # requestsModel.ts
COPY = {  # event: (eyebrow, preheader, paragraph); the reason and the requester are added where they apply
    "pending": ("New request", "{who} asked for {title}. It's waiting for your approval.",
                "{who} would like this in the vault. It's waiting for an admin's approval in Requests → Manage."),
    "approved": ("Approved", "{title} is approved and on its way to the vault.",
                 "Good news: it's approved and on its way to the vault. We'll write again the moment it's ready to watch."),
    "declined": ("Declined", "{title} was declined.",
                 "This request was declined, so it won't be added to the vault for now."),
    "available": ("Ready to watch", "{title} is in the vault and ready to watch.",
                  "It's in the vault and ready when you are. Dim the lights."),
    "failed": ("Needs attention", "Lumina couldn't send {title} to the download server.",
               "Lumina couldn't send this to the download server. An admin can retry it from Requests → Manage."),
}


@dataclass(frozen=True)
class Smtp:
    host: str
    port: int
    security: str
    username: str | None
    password: str | None
    sender: str

    @classmethod
    def of(cls, record: AppSettings | None) -> Smtp | None:
        if record is None or not record.smtp_host or not record.smtp_from:
            return None
        security = record.smtp_security or "starttls"
        port = record.smtp_port or {"ssl": 465, "starttls": 587}.get(security, 25)
        return cls(record.smtp_host, port, security, record.smtp_username or None, record.smtp_password or None, record.smtp_from)


def send(smtp: Smtp, to: str, subject: str, body: str, html: str | None = None) -> None:
    """Send one message now (raises smtplib/OS errors). Callers off the event loop only."""
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = smtp.sender, to, subject
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    if smtp.security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=TIMEOUT_SECONDS, context=context)
    else:
        client = smtplib.SMTP(smtp.host, smtp.port, timeout=TIMEOUT_SECONDS)
    with client:
        if smtp.security == "starttls":
            client.starttls(context=context)
        if smtp.username and smtp.password:
            client.login(smtp.username, smtp.password)
        client.send_message(message)


def failure_message(exc: BaseException) -> str:
    """What an admin sees after a failed test: fixed text, never the server's reply (it may echo what was sent)."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "The mail server refused the username or password."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the recipient address."
    if isinstance(exc, (TimeoutError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return "Could not reach the mail server."
    return f"The mail server rejected the message ({type(exc).__name__})."


def _open_link(req: MediaRequest, event: str) -> str:
    """Where "Open in Lumina" lands: the admin queue, the vault title, the request's catalog page, or My requests."""
    base = public_address.link_base()
    catalog = req.anilist_id if req.kind == "anime" else req.tmdb_id
    if event == "pending":
        return f"{base}/requests/manage"
    if event == "available" and req.library_title_id:
        return f"{base}/title/{quote(req.library_title_id, safe='')}"
    return f"{base}/requests/title/{req.kind}/{catalog}" if catalog else f"{base}/requests/mine"


def request_email(req: MediaRequest, event: str, who: str) -> tuple[str, str, str]:
    """(subject, text, html) for one request event; ``who`` is the requester's display name."""
    name = " ".join(req.title.split())  # a header never carries a line break
    title = f"{name} ({req.year})" if req.year else name
    eyebrow, preheader, paragraph = (part.format(who=who, title=title) for part in COPY[event])
    seasons = req.seasons if isinstance(req.seasons, list) else None
    meta = " · ".join(str(part) for part in (
        req.year, KIND_LABEL.get(req.kind),
        "All seasons" if req.seasons == "all" else (f"Season{'s' if len(seasons) > 1 else ''} {', '.join(map(str, seasons))}" if seasons else None)) if part)
    why = ("You're getting this because you're an admin of Lumina." if event == "pending"
           else "You're getting this because you asked for this title, or follow its request.")
    subject = SUBJECTS[event].format(title=title)
    text, html = email_layout.compose(
        subject=subject, preheader=preheader, eyebrow=eyebrow, heading=name, meta=meta, poster=req.poster_url,
        paragraphs=(paragraph,), note=("Reason", req.decline_reason) if event == "declined" and req.decline_reason else None,
        button=("Open in Lumina", _open_link(req, event)), why=why,
        unsubscribe="To stop request emails, turn them off in Settings → Requests.")
    return subject, text, html


def smtp_test_email() -> tuple[str, str, str]:
    """(subject, text, html): the SMTP check, dressed like every email Lumina sends."""
    subject = "Lumina test email"
    text, html = email_layout.compose(
        subject=subject, preheader="Your mail server is set up. Lumina's emails will look like this.",
        eyebrow="Test email", heading="Your mail is on its way.",
        paragraphs=("Lumina reached your mail server and this message made it through. Every email Lumina sends will "
                    "arrive looking like this one.",),
        listing=("What Lumina will send", ["Invitations to join the household", "Request updates, from asked for to ready to watch",
                                           "Security notices for the owners"]),
        button=("Open Lumina", public_address.link_base()),
        why="You're getting this because an admin sent a test from Settings → Requests.")
    return subject, text, html


def _deliver(smtp: Smtp, messages: list[tuple[str, str, str, str]]) -> None:
    for to, subject, text, html in messages:
        try:
            send(smtp, to, subject, text, html)
        except Exception as exc:  # noqa: BLE001 - logged by type only; email never breaks request flow
            logger.warning("Request email failed: %s", type(exc).__name__)


def request_event(db: Session, req: MediaRequest, event: str) -> None:
    """Queue the emails for ``event`` (pending → admins; others → requester + followers) to send after commit."""
    try:
        smtp = Smtp.of(db.get(AppSettings, 1))
        if smtp is None:
            return
        if event == "pending":
            users = select(User.id).where(User.role == "admin", User.is_active.is_(True))
        else:
            followers = select(MediaRequestFollower.user_id).where(MediaRequestFollower.request_id == req.id)
            users = select(User.id).where(User.is_active.is_(True), (User.id == req.requested_by) | User.id.in_(followers))
        emails = list(db.scalars(select(UserSettings.notify_email).where(
            UserSettings.user_id.in_(users), UserSettings.notify_requests.is_(True), UserSettings.notify_email.is_not(None))))
        if not emails:
            return
        requester = db.get(User, req.requested_by)
        subject, text, html = request_email(req, event, requester.display_name if requester and requester.display_name else "A member")
        messages = [(to, subject, text, html) for to in sorted(set(emails)) if to]
        queue_after_commit(db, lambda: threading.Thread(target=_deliver, args=(smtp, messages), name="request-email", daemon=True).start())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Request email skipped: %s", type(exc).__name__)
