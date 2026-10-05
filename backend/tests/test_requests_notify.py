"""Request emails: recipients per event, SMTP security modes, failures never break request flow nor leak secrets."""
from __future__ import annotations

import logging
import smtplib
import time

import pytest

from arr_fake import FakeTmdb
from support import file_backed_session_factory, make_user, seed_app_settings

from app.models import MediaRequestFollower, User, UserSettings
from app.services import tmdb
from app.services.requests import engine, notify

PASSWORD = "smtp-hunter2"


class FakeSMTP:
    sent: list[tuple[str, str, str]] = []
    html: list[str] = []
    events: list[str] = []
    fail: Exception | None = None

    def __init__(self, host: str, port: int, timeout: float = 0, context: object = None) -> None:
        FakeSMTP.events.append(f"{type(self).__name__}:{host}:{port}")

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def starttls(self, context: object = None) -> None:
        FakeSMTP.events.append("starttls")

    def login(self, username: str, password: str) -> None:
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        FakeSMTP.events.append(f"login:{username}")

    def send_message(self, message) -> None:  # noqa: ANN001
        FakeSMTP.sent.append((message["To"], message["Subject"], message.get_body(("plain",)).get_content()))
        html = message.get_body(("html",))
        FakeSMTP.html.append(html.get_content() if html else "")


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def factory(monkeypatch):  # noqa: ANN201
    FakeSMTP.sent, FakeSMTP.html, FakeSMTP.events, FakeSMTP.fail = [], [], [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    monkeypatch.setattr(tmdb, "client_for", lambda record: FakeTmdb())
    factory = file_backed_session_factory("alice", "bob")
    with factory() as session:
        session.add(make_user("admin", role="admin"))
        seed_app_settings(session, requests_enabled=True, smtp_host="mail.local", smtp_port=587, smtp_security="starttls",
                          smtp_username="lumina", smtp_password=PASSWORD, smtp_from="lumina@example.com")
        for user_id, enabled in (("admin", True), ("alice", True), ("bob", True)):
            session.add(UserSettings(id=f"s-{user_id}", user_id=user_id, notify_email=f"{user_id}@example.com", notify_requests=enabled))
        session.commit()
    return factory


def wait_for(count: int) -> list[tuple[str, str, str]]:
    deadline = time.monotonic() + 5
    while len(FakeSMTP.sent) < count and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.05)
    return sorted(FakeSMTP.sent)


def test_pending_emails_admins_and_decisions_email_requester_and_followers(factory) -> None:  # noqa: ANN001
    with factory() as session:
        req, _ = engine.create(session, session.get(User, "alice"), {"kind": "movie", "tmdb_id": 603})
        session.add(MediaRequestFollower(request_id=req.id, user_id="bob"))
        session.commit()
        [(to, subject, text)] = wait_for(1)
        assert (to, subject) == ("admin@example.com", "New request: The Matrix (1999)")
        assert "Alice would like this in the vault" in text and "/requests/manage" in text and "Settings → Requests" in text
        assert "Alice would like this in the vault" in FakeSMTP.html[0] and 'alt="The Matrix"' in FakeSMTP.html[0]
        assert FakeSMTP.events[:3] == ["FakeSMTP:mail.local:587", "starttls", "login:lumina"]
        FakeSMTP.sent.clear()
        session.get(UserSettings, "s-bob").notify_requests = False  # bob opted out
        session.commit()
        engine.decline(session, session.get(User, "admin"), req, "Already on Plex")
        sent = wait_for(1)
    [(to, subject, text)] = sent
    assert (to, subject) == ("alice@example.com", "Declined: The Matrix (1999)") and "Reason: Already on Plex" in text
    assert "Already on Plex" in FakeSMTP.html[-1] and "/requests/title/movie/603" in FakeSMTP.html[-1]
    assert all(PASSWORD not in part for message in sent for part in message)


def test_ssl_mode_uses_smtp_ssl(factory) -> None:  # noqa: ANN001
    notify.send(notify.Smtp("mail.local", 465, "ssl", None, None, "lumina@example.com"), "a@example.com", "s", "b")
    assert FakeSMTP.events == ["FakeSMTPSSL:mail.local:465"]


def test_a_failing_mail_server_never_breaks_requests_or_logs_the_password(factory, caplog) -> None:  # noqa: ANN001
    FakeSMTP.fail = smtplib.SMTPAuthenticationError(535, f"bad password {PASSWORD}".encode())
    caplog.set_level(logging.WARNING)
    with factory() as session:
        req, created = engine.create(session, session.get(User, "alice"), {"kind": "movie", "tmdb_id": 603})
    assert created and req.status == "pending"
    deadline = time.monotonic() + 5
    while "Request email failed" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "Request email failed: SMTPAuthenticationError" in caplog.text and PASSWORD not in caplog.text
    assert notify.failure_message(FakeSMTP.fail) == "The mail server refused the username or password."


def test_smtp_test_endpoint(factory, api_client) -> None:  # noqa: ANN001
    with factory() as session:
        admin = session.get(User, "admin")
    from app.db import get_db
    from app.main import app

    def real_db():  # the file-backed database the factory seeded
        with factory() as session:
            yield session
            session.commit()

    client = api_client(user=admin, base_url="http://localhost")
    app.dependency_overrides[get_db] = real_db
    assert client.post("/api/admin/requests/smtp/test", json={"to": "owner@example.com"}).json() == {"ok": True}
    [(to, subject, text)] = FakeSMTP.sent
    assert (to, subject) == ("owner@example.com", "Lumina test email") and "this message made it through" in text
    assert "<!doctype html>" in FakeSMTP.html[0] and "Open Lumina" in FakeSMTP.html[0]
    FakeSMTP.fail = smtplib.SMTPAuthenticationError(535, PASSWORD.encode())
    failed = client.post("/api/admin/requests/smtp/test", json={"to": "owner@example.com"})
    assert failed.json() == {"ok": False, "error": "The mail server refused the username or password."}
    assert PASSWORD not in failed.text
