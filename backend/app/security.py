from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.device_ring import ring_cookie_name
from app.db import get_db
from app.persistence import write_transaction
from app.models import AppSession, DeviceToken, User, utcnow
from app.services import public_address, two_factor
from app.services.public_address import arrived_over_https
from app.services.rate_limit import rate_limiter, resolve_client_key


basic_auth = HTTPBasic(auto_error=False)
audit_log = logging.getLogger("lumina.audit")
# OWASP 2023 for PBKDF2-HMAC-SHA256. Older hashes keep verifying at their stored count and are upgraded on sign-in.
PBKDF2_ITERATIONS = 600_000
PASSWORD_SCHEME = "pbkdf2_sha256"
SESSION_TOUCH_INTERVAL_SECONDS = 300
UNSAFE_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
ACCOUNT_LOCK_BUCKET = "session_login_username"
# Verified against when no real hash applies, so unknown usernames cost the same PBKDF2 work.
_DUMMY_PASSWORD_HASH = f"{PASSWORD_SCHEME}${PBKDF2_ITERATIONS}${'0' * 32}${'0' * 64}"
# Connected apps (ADR 0010): a device token idle this long is deleted on its next use.
DEVICE_TOKEN_IDLE_DAYS = 90
MAX_BEARER_LENGTH = 512


class PasswordPolicyError(ValueError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"{PASSWORD_SCHEME}${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def validate_password_policy(password: str, *, username: str | None = None) -> None:
    """Enforce the single account-password policy without retaining or reflecting input."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be no more than {MAX_PASSWORD_LENGTH} characters long.")
    categories = (
        any(character.islower() for character in password),
        any(character.isupper() for character in password),
        any(character.isdigit() for character in password),
        any(not character.isalnum() for character in password),
    )
    if sum(categories) < 3:
        raise PasswordPolicyError("Password must include at least three of: lowercase, uppercase, number, or symbol.")
    normalized_username = (username or "").strip().casefold()
    if normalized_username and normalized_username in password.casefold():
        raise PasswordPolicyError("Password must not contain the account username.")


def _stale_hash(password_hash: str) -> bool:
    try:
        return int(password_hash.split("$")[1]) < PBKDF2_ITERATIONS
    except (IndexError, ValueError):
        return False


def has_local_password(password_hash: str | None) -> bool:
    return bool(password_hash and password_hash.startswith(f"{PASSWORD_SCHEME}$"))


def verify_password(password: str, password_hash: str | None) -> bool:
    if not has_local_password(password_hash):
        return False
    assert password_hash is not None
    try:
        algorithm, iterations_text, salt, digest = password_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != PASSWORD_SCHEME:
        return False
    try:
        iterations = int(iterations_text)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
    return hmac.compare_digest(candidate, digest)


def normalize_origin(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def resolve_request_origin(request: Request) -> str | None:
    origin = normalize_origin(request.headers.get("origin"))
    if origin:
        return origin
    return normalize_origin(request.headers.get("referer"))


def validate_state_changing_request(request: Request) -> str | None:
    if request.method.upper() not in UNSAFE_HTTP_METHODS:
        return None

    # An untrusted Origin/Referer is rejected even when an Authorization header
    # is present: a browser that has cached HTTP Basic credentials attaches them
    # to cross-site requests just like a cookie, so header auth must not bypass
    # the origin allowlist (issue #114).
    request_origin = resolve_request_origin(request)
    if request_origin:
        if request_origin in settings.allowed_origins_list or request_origin in {public_address.origin(), public_address.local_origin()}:
            return None
        return f"Untrusted request origin: {request_origin}"

    # No Origin/Referer at all: only callers without a session cookie (scripts
    # using validated Basic credentials) may omit it. Header *presence* never
    # exempts a cookie-bearing request; authentication then enforces the
    # session-bound CSRF token on cookie-authenticated mutations. The device ring
    # holds session bearers, so it counts as a session cookie here (app polish 6.2).
    if request.cookies.get(settings.session_cookie_name) or request.cookies.get(ring_cookie_name()):
        return "Missing Origin or Referer header for a state-changing session request."
    return None


CSRF_HEADER = "X-CSRF-Token"
CSRF_REJECTED_DETAIL = "CSRF token missing or invalid."


def csrf_token_for(session_id: str) -> str:
    """Session-bound CSRF token: keyed by the secret, httponly session id."""
    return hmac.new(session_id.encode("utf-8"), b"lumina-csrf", hashlib.sha256).hexdigest()


class AppPasswordRequired(Exception):
    """The account password was right, but the account uses two-step verification and the caller cannot ask for a code
    (HTTP Basic, Jellyfin apps): it must sign in with an app password instead."""


APP_PASSWORD_REQUIRED_DETAIL = (
    "This account uses two-step verification. Use an app password from Lumina → Settings → Connected apps."
)


def _app_password(db: Session, user: User, password: str) -> DeviceToken | None:
    if not two_factor.is_app_password_shape(password):
        return None
    return (
        db.query(DeviceToken)
        .filter(DeviceToken.token_digest == session_digest(two_factor.normalize_code(password)), DeviceToken.kind == "app_password",
                DeviceToken.user_id == user.id)
        .first()
    )


def authenticate_user(db: Session, username: str, password: str, *, app_passwords: bool = False, request: Request | None = None) -> User | None:
    """The member a username and password name. ``app_passwords`` (HTTP Basic, Jellyfin apps only, never the web form) also
    accepts an app password, and refuses the account password of a two-step account with AppPasswordRequired."""
    user = db.query(User).filter(User.username == username.strip().lower()).first()
    if user is None or not user.is_active or not has_local_password(user.password_hash):
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None
    if app_passwords and (record := _app_password(db, user, password)) is not None:
        if request is not None:
            request.state.app_password_id = record.id
        if (utcnow() - record.last_seen_at).total_seconds() >= SESSION_TOUCH_INTERVAL_SECONDS:
            record.last_seen_at = utcnow()
            record.last_ip = client_ip(request) if request is not None else None
            try:
                with write_transaction(db, name="app_password_touch"):
                    db.flush()
            except OperationalError:
                pass
        return user
    if not verify_password(password, user.password_hash):
        return None
    if _stale_hash(user.password_hash):
        try:
            with write_transaction(db, name="password_rehash"):
                user.password_hash = hash_password(password)
                db.flush()
        except OperationalError:
            pass  # upgraded on a later sign-in
    if app_passwords and two_factor.enabled(user):
        raise AppPasswordRequired()
    return user


def account_lock_key(username: str) -> str:
    return f"username:{username.strip().lower()[:128]}"


def check_sign_in_limits(request: Request, username: str) -> None:
    """Peek the per-IP and (on the public address) per-account buckets without charging them."""
    rate_limiter.check("session_login", resolve_client_key(request), record=False)
    if arrived_over_https(request.scope):
        try:
            rate_limiter.check(ACCOUNT_LOCK_BUCKET, account_lock_key(username), record=False)
        except HTTPException:
            audit_log.warning("login.locked client=%s", resolve_client_key(request))
            raise


def charge_sign_in_failure(request: Request, username: str) -> None:
    """A wrong password or code: one more toward the account lock, and one toward the IP block (raises 429 when full)."""
    try:
        rate_limiter.check(ACCOUNT_LOCK_BUCKET, account_lock_key(username))
    except HTTPException:
        pass
    rate_limiter.check("session_login", resolve_client_key(request))


def authenticate_rate_limited(db: Session, request: Request, username: str, password: str, *, app_passwords: bool = False) -> User | None:
    """Password check shared by web login, HTTP Basic and Jellyfin AuthenticateByName.

    Failures hard-block per client IP. Failures per typed username (any IP, known or not) lock that account on the
    public address only: the LAN and loopback always reach the password check, so an internet attacker can never
    lock the household out, and a successful sign-in or an owner's unlock clears the lock. A two-step account's
    password alone is not a successful sign-in: the web form clears the lock only once the code is accepted.
    """
    ip_key = resolve_client_key(request)
    check_sign_in_limits(request, username)
    user = authenticate_user(db, username, password, app_passwords=app_passwords, request=request)
    if user is None:
        known = db.query(User.id).filter(User.username == username.strip().lower()).scalar()
        audit_log.warning("login.fail user=%s client=%s", known or "unknown", ip_key)
        charge_sign_in_failure(request, username)
        return None
    if app_passwords or not two_factor.enabled(user):
        rate_limiter.reset(ACCOUNT_LOCK_BUCKET, account_lock_key(username))
    return user


def session_digest(token: str) -> str:
    """Only this digest is persisted; the bearer itself lives solely in the cookie."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_app_session(db: Session, user: User, request: Request | None = None, *, expires_at: datetime | None = None) -> tuple[AppSession, str]:
    """A new bearer. ``expires_at`` lets a device-ring switch rotate a session without extending its deadline."""
    token = secrets.token_urlsafe(48)
    session = AppSession(
        id=session_digest(token),
        user_id=user.id,
        # Absolute deadline: fixed at creation, never extended by activity.
        expires_at=expires_at or utcnow() + timedelta(hours=settings.session_duration_hours),
        client_meta={
            "user_agent": request.headers.get("user-agent") if request else None,
            "ip": client_ip(request) if request else None,
        },
    )
    with write_transaction(db, name="app_session_create"):
        db.add(session)
        db.flush()
    return session, token


def apply_session_cookie(response: Response, session: AppSession, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        max_age=settings.session_duration_hours * 3600,
        expires=session.expires_at.replace(tzinfo=UTC),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )


def revoke_token(db: Session, token: str) -> None:
    """Deletes the session a bearer belongs to (sign-out, forget, a replaced or unreachable session)."""
    session_record = db.get(AppSession, session_digest(token))
    if session_record is not None:
        with write_transaction(db, name="app_session_revoke"):
            db.delete(session_record)
            db.flush()


def claim_token(db: Session, token: str) -> bool:
    """Atomically deletes a bearer's session; True only for the one caller whose delete removed the row."""
    with write_transaction(db, name="app_session_claim"):
        claimed = db.query(AppSession).filter(AppSession.id == session_digest(token)).delete(synchronize_session=False)
        db.flush()
    return claimed == 1


def revoke_session(db: Session, request: Request) -> None:
    session_token = request.cookies.get(settings.session_cookie_name)
    if session_token:
        revoke_token(db, session_token)


def _expire(db: Session, session_record: AppSession) -> None:
    try:
        with write_transaction(db, name="app_session_expire"):
            db.delete(session_record)
            db.flush()
    except OperationalError:
        pass


def _live_session(db: Session, session_token: str | None) -> tuple[AppSession, User] | None:
    """The session and its member while both are valid. Expired, idle-expired and orphaned sessions are deleted."""
    if not session_token:
        return None
    session_record = db.get(AppSession, session_digest(session_token))
    if session_record is None:
        return None
    now = utcnow()
    idle_deadline = session_record.last_seen_at + timedelta(hours=settings.session_idle_hours)
    if session_record.expires_at <= now or idle_deadline <= now:
        _expire(db, session_record)
        return None
    user = db.get(User, session_record.user_id)
    if user is None or not user.is_active:
        _expire(db, session_record)
        return None
    return session_record, user


def peek_session_user(db: Session, session_token: str | None) -> User | None:
    """resolve_session_user without the activity touch: the device ring validates its entries with
    every session rule, but looking at a remembered session must never extend its idle deadline."""
    live = _live_session(db, session_token)
    return live[1] if live else None


def resolve_session_user(db: Session, session_token: str | None) -> User | None:
    live = _live_session(db, session_token)
    if live is None:
        return None
    session_record, user = live
    now = utcnow()
    if (now - session_record.last_seen_at).total_seconds() >= SESSION_TOUCH_INTERVAL_SECONDS:
        # Activity only refreshes the idle deadline; expires_at stays absolute.
        session_record.last_seen_at = now
        try:
            # Authentication runs before the route handler. Releasing the
            # writer lock after flush but before request teardown left this
            # transaction holding SQLite's write lock while another request
            # could acquire our process lock, producing a lock inversion.
            with write_transaction(db, name="app_session_touch"):
                db.flush()
        except OperationalError:
            return db.get(User, user.id)
    return user


def client_ip(request: Request) -> str | None:
    """Best-effort caller address for Connected-app bookkeeping; never rejects the request."""
    try:
        return resolve_client_key(request)[:64]
    except HTTPException:
        return None


def resolve_device_token(db: Session, request: Request, token: str | None, *, kind: str) -> tuple[DeviceToken, User] | None:
    """Resolve a Connected-app bearer of one kind (``jellyfin`` on /jellyfin, ``agent`` on /api).

    Idle and orphaned tokens are deleted; activity touches last_seen_at at most every
    SESSION_TOUCH_INTERVAL_SECONDS, like cookie sessions.
    """
    if not token or len(token) > MAX_BEARER_LENGTH:
        return None
    found = (db.query(DeviceToken, User).outerjoin(User, User.id == DeviceToken.user_id)
        .filter(DeviceToken.token_digest == session_digest(token), DeviceToken.kind == kind).first())
    return validate_device_token_record(db, request, found)


def validate_device_token_record(
    db: Session, request: Request, found: tuple[DeviceToken, User | None] | None,
) -> tuple[DeviceToken, User] | None:
    """Validate an already-loaded connected-app credential and keep its existing lifecycle rules.

    Some hot paths load the app setting, token, and member in one query.  Keeping the
    expiry and touch behavior here makes those paths use precisely the same token
    lifecycle as ``resolve_device_token``.
    """
    if found is None:
        return None
    record, user = found
    now = utcnow()
    if user is None or not user.is_active or record.last_seen_at + timedelta(days=DEVICE_TOKEN_IDLE_DAYS) <= now:
        try:
            with write_transaction(db, name="device_token_expire"):
                db.delete(record)
                db.flush()
        except OperationalError:
            pass
        return None
    if (now - record.last_seen_at).total_seconds() >= SESSION_TOUCH_INTERVAL_SECONDS:
        record.last_seen_at, record.last_ip = now, client_ip(request)
        try:
            with write_transaction(db, name="device_token_touch"):
                db.flush()
        except OperationalError:
            pass
    return record, user


def _unauthorized(detail: str = "Authentication required", *, advertise_basic: bool = False) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Basic"} if advertise_basic else None,
    )


def resolve_user_from_request(
    db: Session,
    request: Request,
    credentials: HTTPBasicCredentials | None = None,
) -> User:
    session_token = request.cookies.get(settings.session_cookie_name)
    session_user = resolve_session_user(db, session_token)
    if session_user is not None:
        # Verified provenance: cookie session. Browser mutations must prove the
        # session-bound CSRF token; the trusted Origin is already required for
        # any cookie-bearing mutation by validate_state_changing_request.
        if request.method.upper() in UNSAFE_HTTP_METHODS and not hmac.compare_digest(
            request.headers.get(CSRF_HEADER, "").encode("utf-8"),
            csrf_token_for(session_token).encode("utf-8"),
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=CSRF_REJECTED_DETAIL)
        request.state.via_session = True
        return session_user

    scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        # Agent Connected app: no cookie, so CSRF does not apply; the token's scope does.
        resolved = resolve_device_token(db, request, bearer.strip(), kind="agent")
        if resolved is None:
            raise _unauthorized("Invalid or revoked token")
        record, user = resolved
        if record.scope != "write" and request.method.upper() in UNSAFE_HTTP_METHODS:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This connected app has read-only access.")
        request.state.connected_app_id = record.id
        return user

    username: str | None = None
    password: str | None = None

    if credentials is not None:
        username = credentials.username
        password = credentials.password

    if not username or password is None:
        raise _unauthorized()

    try:
        user = authenticate_rate_limited(db, request, username, password, app_passwords=True)
    except AppPasswordRequired:
        raise _unauthorized(APP_PASSWORD_REQUIRED_DETAIL, advertise_basic=True) from None
    if user is None:
        raise _unauthorized("Invalid username or password", advertise_basic=True)
    if getattr(request.state, "app_password_id", None) and not app_password_allows(request.method, request.url.path):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=APP_PASSWORD_SCOPE_DETAIL)
    return user


# App passwords are for media apps: browse and play, never manage an account (2.9.0 security review).
APP_PASSWORD_SCOPE_DETAIL = "An app password only plays and browses media. Do this from a signed-in browser session."
_APP_PASSWORD_NO_READ = ("/api/admin", "/api/connected-apps", "/api/me/two-factor", "/api/me/jellyfin-import")
_APP_PASSWORD_WRITES = re.compile(
    r"/api/(library/[^/]+/playback|library/[^/]+/playback-sessions|playback-sessions/[^/]+|playback/remote/.+|titles/[^/]+/watched)"
)


def app_password_allows(method: str, path: str) -> bool:
    if method.upper() in UNSAFE_HTTP_METHODS:
        return _APP_PASSWORD_WRITES.fullmatch(path) is not None
    return not path.startswith(_APP_PASSWORD_NO_READ)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)] = None,
) -> User:
    return resolve_user_from_request(db, request=request, credentials=credentials)


def _charge_member_job(current_user: User = Depends(get_current_user)) -> None:
    if current_user.role != "admin":  # the vault owner's own work is never metered
        rate_limiter.check("member_job", f"user:{current_user.id}", user_id=current_user.id)


MEMBER_JOB = [Depends(_charge_member_job)]


TWO_FACTOR_SETUP_REQUIRED = "two_factor_setup_required"


def get_admin_user(
    request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> User:
    if current_user.role != "admin" or getattr(request.state, "app_password_id", None):  # an app password is never an owner
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    if two_factor.setup_required(db, current_user):  # the household requires owners to turn it on first
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=TWO_FACTOR_SETUP_REQUIRED)
    return current_user


def require_browser_session(request: Request) -> None:
    """Credential management (two-step settings, app passwords) needs a signed-in browser: never HTTP Basic or a token."""
    if not getattr(request.state, "via_session", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Do this from a signed-in browser session.")


def get_owner_session_user(request: Request, current_user: User = Depends(get_admin_user)) -> User:
    """Owner actions that could take over an account (reset links, two-step reset, roles, invitations, the public address):
    a signed-in browser only, never an agent token or HTTP Basic."""
    require_browser_session(request)
    return current_user
