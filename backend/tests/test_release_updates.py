"""Stable release checks: provider trust, durable cache/email dedupe, and the admin API."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import httpx
import pytest

from app.models import AppSettings, UserSettings
from app.services import release_updates
from support import file_backed_session_factory, make_user, seed_app_settings


@pytest.fixture(autouse=True)
def installed_version(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(release_updates, "APP_VERSION", "0.3.4")


def latest(version: str = "0.4.0") -> release_updates.LatestRelease:
    return release_updates.LatestRelease(
        version=version,
        url=f"https://github.com/shiftedx/lumina/releases/tag/v{version}",
        published_at="2026-10-18T12:00:00Z",
    )


def test_fixed_github_fetch_accepts_one_bounded_canonical_stable_release(monkeypatch) -> None:  # noqa: ANN001
    original = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == release_updates.LATEST_RELEASE_URL
        assert request.headers["accept"] == "application/vnd.github+json"
        assert request.headers["user-agent"].startswith("Lumina/")
        return httpx.Response(200, json={
            "draft": False,
            "prerelease": False,
            "tag_name": "v0.4.0",
            "html_url": "https://github.com/shiftedx/lumina/releases/tag/v0.4.0",
            "published_at": "2026-10-18T12:00:00Z",
        })

    monkeypatch.setattr(release_updates.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    assert release_updates.fetch_latest_release() == latest()


@pytest.mark.parametrize("payload", [
    {"draft": True, "prerelease": False, "tag_name": "v0.4.0", "html_url": "https://github.com/shiftedx/lumina/releases/tag/v0.4.0"},
    {"draft": False, "prerelease": True, "tag_name": "v0.4.0", "html_url": "https://github.com/shiftedx/lumina/releases/tag/v0.4.0"},
    {"draft": False, "prerelease": False, "tag_name": "v00.4.0", "html_url": "https://github.com/shiftedx/lumina/releases/tag/v00.4.0"},
    {"draft": False, "prerelease": False, "tag_name": "v0.4.0", "html_url": "https://example.com/v0.4.0"},
    {"draft": False, "prerelease": False, "tag_name": "v9999999999.4.0", "html_url": "https://github.com/shiftedx/lumina/releases/tag/v9999999999.4.0"},
    {"draft": False, "prerelease": False, "tag_name": "v0.4.0", "html_url": "https://github.com/shiftedx/lumina/releases/tag/v0.4.0", "published_at": None},
])
def test_provider_rejects_nonstable_noncanonical_release_data(payload) -> None:  # noqa: ANN001
    with pytest.raises(release_updates.ReleaseCheckError):
        release_updates._release(payload)


def test_provider_404_is_not_reported_as_up_to_date(monkeypatch) -> None:  # noqa: ANN001
    original = httpx.Client
    monkeypatch.setattr(
        release_updates.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(lambda _request: httpx.Response(404)), **kwargs),
    )
    with pytest.raises(release_updates.ReleaseCheckError, match="No public"):
        release_updates.fetch_latest_release()


def test_success_and_failure_keep_a_durable_cached_release(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        seed_app_settings(db)
    checked = datetime(2026, 10, 18, 12, 0, 0)
    result = release_updates.perform_check(fetcher=latest, session_factory=db_factory, now=lambda: checked)
    assert result.status == "update_available" and result.checked_at == checked.isoformat()

    def unavailable():
        raise release_updates.ReleaseCheckError("Unable to check GitHub for updates.")

    failed = release_updates.perform_check(fetcher=unavailable, session_factory=db_factory, now=lambda: checked + timedelta(hours=2))
    assert failed.status == "update_available"
    assert failed.latest_release == latest()
    assert failed.checked_at == checked.isoformat()
    assert failed.error == "Unable to check GitHub for updates."
    with db_factory() as db:
        stored = db.get(AppSettings, 1).update_settings
        assert stored["latest_release"]["version"] == "0.4.0"
        assert stored["last_attempt_at"] == (checked + timedelta(hours=2)).isoformat()


def test_update_email_is_opt_in_active_owner_only_and_deduped_across_checks(db_factory, monkeypatch) -> None:  # noqa: ANN001
    owner = make_user("owner", role="admin")
    duplicate = make_user("duplicate", role="admin")
    inactive = make_user("inactive", role="admin", is_active=False)
    member = make_user("member")
    with db_factory() as db:
        db.add_all([owner, duplicate, inactive, member])
        seed_app_settings(
            db,
            smtp_host="mail.local",
            smtp_from="lumina@example.com",
            update_settings={"automatic_checks": True, "email_notifications": True},
        )
        db.add_all([
            UserSettings(id="s1", user_id=owner.id, notify_email="owner@example.com", notify_requests=False),
            UserSettings(id="s2", user_id=duplicate.id, notify_email="owner@example.com", notify_requests=False),
            UserSettings(id="s3", user_id=inactive.id, notify_email="inactive@example.com"),
            UserSettings(id="s4", user_id=member.id, notify_email="member@example.com"),
        ])
        db.commit()

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(release_updates, "send", lambda _smtp, to, subject, *_args: sent.append((to, subject)))

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):  # noqa: ANN001
            self.target, self.args = target, args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(release_updates.threading, "Thread", ImmediateThread)
    release_updates.perform_check(fetcher=latest, session_factory=db_factory)
    release_updates.perform_check(fetcher=latest, session_factory=db_factory)  # restart/recheck sees the durable marker
    release_updates.perform_check(fetcher=lambda: latest("0.5.0"), session_factory=db_factory)
    release_updates.perform_check(fetcher=latest, session_factory=db_factory)  # a yanked 0.5 must not re-send 0.4
    assert sent == [
        ("owner@example.com", "Lumina 0.4.0 is available"),
        ("owner@example.com", "Lumina 0.5.0 is available"),
    ]
    with db_factory() as db:
        assert db.get(AppSettings, 1).update_settings["email_queued_version"] == "0.5.0"


def test_automatic_retry_intervals_and_malformed_cache_are_safe(db_factory) -> None:  # noqa: ANN001
    now = datetime(2026, 10, 18, 12, 0, 0)
    with db_factory() as db:
        record = seed_app_settings(db, update_settings={
            "last_attempt_at": now.isoformat(),
            "checked_at": now.isoformat(),
            "latest_release": {"version": "bad", "url": "javascript:x", "published_at": None},
            "error": "Unable to check GitHub for updates.",
        })
        assert release_updates.status_for(record).status == "not_checked"
        assert not release_updates.automatic_check_due(record, now + timedelta(minutes=59))
        assert release_updates.automatic_check_due(record, now + timedelta(hours=1))
        record.update_settings = {**record.update_settings, "error": None}
        assert not release_updates.automatic_check_due(record, now + timedelta(hours=23))
        assert release_updates.automatic_check_due(record, now + timedelta(days=1))
        record.update_settings = {"automatic_checks": False}
        assert not release_updates.automatic_check_due(record, now + timedelta(days=2))


def test_coordinator_is_single_flight_and_manual_checks_have_a_cooldown() -> None:
    coordinator = release_updates.ReleaseUpdateCoordinator()
    entered, finish = threading.Event(), threading.Event()

    def blocked(_cancel) -> None:  # noqa: ANN001
        entered.set()
        finish.wait(timeout=2)
        with coordinator._lock:
            coordinator._checking = False

    coordinator._run = blocked  # type: ignore[method-assign]
    assert coordinator.request_manual() == "started"
    assert entered.wait(timeout=1)
    assert coordinator.request_manual() == "running"
    finish.set()
    for _ in range(100):
        if not coordinator.checking:
            break
        time.sleep(0.005)
    assert coordinator.request_manual() == "cooldown"
    coordinator.close()
    assert coordinator.request_manual() == "closed"


def test_close_waits_for_the_checker_and_prevents_a_post_close_database_write() -> None:
    factory = file_backed_session_factory()
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def blocked_fetch() -> release_updates.LatestRelease:
        entered.set()
        release.wait(timeout=2)
        return latest()

    coordinator = release_updates.ReleaseUpdateCoordinator(session_factory=factory, fetcher=blocked_fetch)
    assert coordinator.request_manual() == "started" and entered.wait(timeout=1)
    closer = threading.Thread(target=lambda: (coordinator.close(), closed.set()))
    closer.start()
    assert not closed.wait(timeout=0.05)
    release.set()
    assert closed.wait(timeout=1)
    closer.join()
    with factory() as db:
        assert db.get(AppSettings, 1) is None


def test_admin_updates_api_defaults_preferences_auth_and_manual_outcomes(api_client, db_factory, monkeypatch) -> None:  # noqa: ANN001
    owner, member = make_user("owner", role="admin"), make_user("member")
    with db_factory.begin() as db:
        db.add_all([owner, member])
    current = {"user": owner}
    client = api_client(user=lambda: current["user"], base_url="http://localhost")

    initial = client.get("/api/admin/updates")
    assert initial.status_code == 200
    assert initial.json() == {
        "current_version": release_updates.APP_VERSION,
        "latest_release": None,
        "status": "not_checked",
        "checked_at": None,
        "error": None,
        "checking": False,
        "automatic_checks": True,
        "email_notifications": False,
        "email_configured": False,
    }
    saved = client.patch("/api/admin/updates", json={"automatic_checks": False, "email_notifications": True})
    assert saved.status_code == 200
    assert saved.json()["automatic_checks"] is False and saved.json()["email_notifications"] is True

    monkeypatch.setattr(release_updates.coordinator, "request_manual", lambda: "running")
    monkeypatch.setattr(release_updates.coordinator, "_checking", True)
    assert client.post("/api/admin/updates/check").status_code == 202
    monkeypatch.setattr(release_updates.coordinator, "request_manual", lambda: "cooldown")
    cooled = client.post("/api/admin/updates/check")
    assert cooled.status_code == 429 and cooled.headers["retry-after"] == "60"
    monkeypatch.setattr(release_updates.coordinator, "request_manual", lambda: "closed")
    assert client.post("/api/admin/updates/check").status_code == 503

    current["user"] = member
    assert client.get("/api/admin/updates").status_code == 403
    assert client.patch("/api/admin/updates", json={"automatic_checks": True}).status_code == 403
    assert client.post("/api/admin/updates/check").status_code == 403
