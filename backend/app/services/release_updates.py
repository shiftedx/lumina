"""Bounded stable-release checks and durable server-owner update state.

The provider URL is fixed. Network and SMTP work happen outside database
transactions, and failures retain the last successful release result.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import db as db_module
from app.config import APP_VERSION
from app.models import AppSettings, User, UserSettings, utcnow
from app.persistence import write_transaction
from app.services import email_layout
from app.services.requests.notify import Smtp, send
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)

LATEST_RELEASE_URL = "https://api.github.com/repos/shiftedx/lumina/releases/latest"
RELEASE_PAGE_PREFIX = "https://github.com/shiftedx/lumina/releases/tag/"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10.0
AUTOMATIC_SUCCESS_INTERVAL = timedelta(days=1)
AUTOMATIC_FAILURE_INTERVAL = timedelta(hours=1)
MANUAL_COOLDOWN_SECONDS = 60.0
SEMVER_TAG = re.compile(r"^v(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")
RequestResult = Literal["started", "running", "cooldown", "closed"]


class LatestRelease(BaseModel):
    version: str
    url: str
    published_at: str | None


class UpdateStatus(BaseModel):
    current_version: str
    latest_release: LatestRelease | None
    status: Literal["not_checked", "up_to_date", "update_available"]
    checked_at: str | None
    error: str | None
    checking: bool
    automatic_checks: bool
    email_notifications: bool
    email_configured: bool


class ReleaseCheckError(Exception):
    """A fixed, safe provider failure suitable for the admin response."""


def _version(value: str) -> tuple[int, int, int]:
    match = SEMVER_TAG.fullmatch("v" + value)
    if match is None:
        raise ValueError("Invalid installed version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _stored_version(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    try:
        return _version(value)
    except ValueError:
        return None


def _timestamp(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReleaseCheckError("GitHub sent an invalid release.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseCheckError("GitHub sent an invalid release.") from exc
    if parsed.tzinfo is None:
        raise ReleaseCheckError("GitHub sent an invalid release.")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _release(payload: object) -> LatestRelease:
    if not isinstance(payload, dict) or payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ReleaseCheckError("GitHub sent an invalid stable release.")
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or (match := SEMVER_TAG.fullmatch(tag)) is None:
        raise ReleaseCheckError("GitHub sent an invalid release tag.")
    expected_url = RELEASE_PAGE_PREFIX + tag
    if payload.get("html_url") != expected_url:
        raise ReleaseCheckError("GitHub sent an invalid release URL.")
    if payload.get("published_at") is None:
        raise ReleaseCheckError("GitHub sent an invalid release timestamp.")
    return LatestRelease(
        version=".".join(match.groups()),
        url=expected_url,
        published_at=_timestamp(payload.get("published_at")),
    )


def fetch_latest_release() -> LatestRelease:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Lumina/{APP_VERSION} (+https://github.com/shiftedx/lumina)",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=REQUEST_TIMEOUT_SECONDS)
    deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
    try:
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            with client.stream("GET", LATEST_RELEASE_URL, headers=headers) as response:
                if response.status_code == 404:
                    raise ReleaseCheckError("No public Lumina releases were found.")
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise ReleaseCheckError("The GitHub release check timed out.")
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ReleaseCheckError("GitHub sent an oversized release response.")
    except ReleaseCheckError:
        raise
    except httpx.HTTPError as exc:
        raise ReleaseCheckError("Unable to check GitHub for updates.") from exc
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseCheckError("GitHub sent an invalid release response.") from exc
    return _release(payload)


def _state(record: AppSettings) -> dict:
    raw = record.update_settings if isinstance(record.update_settings, dict) else {}
    latest = raw.get("latest_release")
    if not isinstance(latest, dict):
        latest = None
    return {
        "automatic_checks": raw.get("automatic_checks") is not False,
        "email_notifications": raw.get("email_notifications") is True,
        "last_attempt_at": raw.get("last_attempt_at") if isinstance(raw.get("last_attempt_at"), str) else None,
        "checked_at": raw.get("checked_at") if isinstance(raw.get("checked_at"), str) else None,
        "latest_release": latest,
        "error": raw.get("error") if isinstance(raw.get("error"), str) else None,
        "email_queued_version": raw.get("email_queued_version") if isinstance(raw.get("email_queued_version"), str) else None,
    }


def _cached_release(value: object) -> LatestRelease | None:
    try:
        latest = LatestRelease.model_validate(value)
        tag = "v" + latest.version
        if SEMVER_TAG.fullmatch(tag) is None or latest.url != RELEASE_PAGE_PREFIX + tag or latest.published_at is None:
            return None
        _timestamp(latest.published_at)
        return latest
    except (ValueError, ReleaseCheckError):
        return None


def status_for(record: AppSettings, *, checking: bool = False) -> UpdateStatus:
    state = _state(record)
    latest = _cached_release(state["latest_release"]) if state["latest_release"] is not None else None
    status = "not_checked"
    if latest is not None:
        status = "update_available" if _version(latest.version) > _version(APP_VERSION) else "up_to_date"
    return UpdateStatus(
        current_version=APP_VERSION,
        latest_release=latest,
        status=status,
        checked_at=state["checked_at"],
        error=state["error"],
        checking=checking,
        automatic_checks=state["automatic_checks"],
        email_notifications=state["email_notifications"],
        email_configured=Smtp.of(record) is not None,
    )


def get_status(db: Session, *, checking: bool = False) -> UpdateStatus:
    return status_for(YtDlpService(db).get_app_settings(), checking=checking)


def update_preferences(db: Session, *, automatic_checks: bool | None, email_notifications: bool | None) -> UpdateStatus:
    with write_transaction(db, name="release_update_settings"):
        record = YtDlpService(db).ensure_app_settings()
        state = _state(record)
        if automatic_checks is not None:
            state["automatic_checks"] = automatic_checks
        if email_notifications is not None:
            state["email_notifications"] = email_notifications
        record.update_settings = state
        db.flush()
    return status_for(record, checking=coordinator.checking)


def _parse_at(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed


def automatic_check_due(record: AppSettings, now: datetime | None = None) -> bool:
    state = _state(record)
    if not state["automatic_checks"]:
        return False
    attempted = _parse_at(state["last_attempt_at"])
    if attempted is None:
        return True
    interval = AUTOMATIC_FAILURE_INTERVAL if state["error"] else AUTOMATIC_SUCCESS_INTERVAL
    return (now or utcnow()) >= attempted + interval


def _owner_mail(db: Session, record: AppSettings) -> tuple[Smtp | None, list[str]]:
    smtp = Smtp.of(record)
    if smtp is None:
        return None, []
    addresses = db.scalars(
        select(UserSettings.notify_email)
        .join(User, User.id == UserSettings.user_id)
        .where(User.role == "admin", User.is_active.is_(True), UserSettings.notify_email.is_not(None))
    )
    return smtp, sorted({address for address in addresses if address})


def _email(release: LatestRelease) -> tuple[str, str, str]:
    subject = f"Lumina {release.version} is available"
    text, html = email_layout.compose(
        subject=subject,
        preheader=f"A newer stable Lumina release, {release.version}, is available.",
        eyebrow="Update available",
        heading=f"Lumina {release.version}",
        status="connected",
        paragraphs=("A newer stable release is available. Lumina will never install it automatically.",),
        facts=(("Installed", APP_VERSION), ("Available", release.version)),
        button=("View release", release.url),
        why="You're getting this because update emails are enabled and you're an active Lumina owner.",
    )
    return subject, text, html


def _deliver(smtp: Smtp, addresses: list[str], release: LatestRelease) -> None:
    subject, text, html = _email(release)
    for address in addresses:
        try:
            send(smtp, address, subject, text, html)
        except Exception as exc:  # noqa: BLE001 - never log provider text, address or credentials
            logger.warning("Release update email failed: %s", type(exc).__name__)


def perform_check(
    *,
    fetcher: Callable[[], LatestRelease] = fetch_latest_release,
    session_factory: sessionmaker | None = None,
    now: Callable[[], datetime] = utcnow,
    cancelled: Callable[[], bool] = lambda: False,
) -> UpdateStatus:
    factory = session_factory or db_module.SessionLocal
    attempted_at = now()
    try:
        latest = fetcher()
    except ReleaseCheckError as exc:
        if cancelled():
            raise RuntimeError("Release update check cancelled") from exc
        with db_module.session_scope(factory) as db, write_transaction(db, name="release_update_check"):
            record = YtDlpService(db).ensure_app_settings()
            state = _state(record)
            state["last_attempt_at"] = attempted_at.isoformat()
            state["error"] = str(exc)
            record.update_settings = state
            db.flush()
        return status_for(record)

    if cancelled():
        raise RuntimeError("Release update check cancelled")
    smtp: Smtp | None = None
    addresses: list[str] = []
    send_release: LatestRelease | None = None
    with db_module.session_scope(factory) as db, write_transaction(db, name="release_update_check"):
        record = YtDlpService(db).ensure_app_settings()
        state = _state(record)
        latest_version = _version(latest.version)
        queued_version = _stored_version(state["email_queued_version"])
        newer = latest_version > _version(APP_VERSION)
        if newer and state["email_notifications"] and (queued_version is None or latest_version > queued_version):
            smtp, addresses = _owner_mail(db, record)
            if smtp is not None and addresses:
                state["email_queued_version"] = latest.version
                send_release = latest
        state.update({
            "last_attempt_at": attempted_at.isoformat(),
            "checked_at": attempted_at.isoformat(),
            "latest_release": latest.model_dump(mode="json"),
            "error": None,
        })
        record.update_settings = state
        db.flush()
    if not cancelled() and smtp is not None and addresses and send_release is not None:
        threading.Thread(
            target=_deliver,
            args=(smtp, addresses, send_release),
            name="release-update-email",
            daemon=True,
        ).start()
    return status_for(record)


class ReleaseUpdateCoordinator:
    """One daemon check worker; callers only enqueue, while shutdown joins its bounded work."""

    def __init__(self, *, session_factory: sessionmaker | None = None, fetcher: Callable[[], LatestRelease] = fetch_latest_release) -> None:
        self._session_factory = session_factory
        self._fetcher = fetcher
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._checking = False
        self._closed = False
        self._last_manual_started = float("-inf")
        self._cancel = threading.Event()

    @property
    def checking(self) -> bool:
        with self._lock:
            return self._checking

    def start(self) -> None:
        with self._lock:
            self._closed = False

    def _request(self, *, manual: bool) -> RequestResult:
        with self._lock:
            moment = time.monotonic()
            if self._closed:
                return "closed"
            if self._checking:
                return "running"
            if manual and moment - self._last_manual_started < MANUAL_COOLDOWN_SECONDS:
                return "cooldown"
            if manual:
                self._last_manual_started = moment
            self._checking = True
            self._cancel = threading.Event()
            cancel = self._cancel
            self._thread = threading.Thread(target=self._run, args=(cancel,), name="release-update-check", daemon=True)
            self._thread.start()
            return "started"

    def request_manual(self) -> RequestResult:
        return self._request(manual=True)

    def request_automatic(self) -> bool:
        factory = self._session_factory or db_module.SessionLocal
        with factory() as db:
            if not automatic_check_due(YtDlpService(db).get_app_settings()):
                return False
        return self._request(manual=False) == "started"

    def _run(self, cancel: threading.Event) -> None:
        try:
            perform_check(fetcher=self._fetcher, session_factory=self._session_factory, cancelled=cancel.is_set)
        except Exception as exc:  # noqa: BLE001 - keep the scheduler alive; log type only
            logger.warning("Release update check failed: %s", type(exc).__name__)
        finally:
            with self._lock:
                self._checking = False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            worker = self._thread
        if worker is not None:
            # Provider reads and writer admission are bounded. Waiting here
            # guarantees no old worker can
            # write after the server lock is released for an offline restore.
            worker.join()


coordinator = ReleaseUpdateCoordinator()
