"""Optional two-step verification (2.9.0): RFC 6238 TOTP, recovery codes, sign-in challenges and trusted devices.

TOTP is SHA-1, 6 digits, 30-second steps, accepting one step either side; a step is accepted at most once per account
(``totp_last_step``), so a code seen over someone's shoulder cannot be replayed. The shared secret is encrypted at rest
with AES-GCM under its own key (app-data/totp-key, 0600, made on first enrollment) and bound to the user id: deleting
that file makes every enrolled authenticator unreadable, and members then sign in with a recovery code or are reset by
an owner. 2.9.0 sealed with a key derived from app-data/art-secret ("v1:"); startup reseals those (reseal_legacy).

Owner recovery without a working second factor (documented in docker/README.md)::

    docker compose exec yt-dlp-ui python -m app.services.two_factor reset <username>
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import struct
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import quote

from Cryptodome.Cipher import AES  # pycryptodomex: already installed with yt-dlp[default]
from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.config import settings
from app.models import AppSettings, User, UserSettings, utcnow
from app.persistence import queue_after_commit, write_transaction
from app.services import art_urls, email_layout, public_address

audit_log = logging.getLogger("lumina.audit")
logger = logging.getLogger(__name__)

STEP_SECONDS = 30
DIGITS = 6
ISSUER = "Lumina"
RECOVERY_CODES = 10
CHALLENGE_SECONDS = 300
CHALLENGE_ATTEMPTS = 5
TRUST_DAYS = 30
TRUST_COOKIE_SUFFIX = "_trust"
TRUST_PATH = "/api/session"
TOTP_LOCK_AFTER = 10  # consecutive wrong codes, from anywhere, before codes pause for the account (recovery codes still work)
TOTP_LOCK_SECONDS = 15 * 60  # doubled for each further miss after a pause, up to a day
TOTP_LOCK_MAX_SECONDS = 24 * 3600
# Recovery codes and app passwords: 31 symbols without look-alikes (no 0/o, 1/l/i), ~4.95 bits each.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


# ---- RFC 6238 -------------------------------------------------------------------------------------------------------

def totp(secret: bytes, step: int, *, digits: int = DIGITS, digest: str = "sha1") -> str:
    mac = hmac.new(secret, struct.pack(">Q", step), digest).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def matching_step(secret: bytes, code: str, *, last_step: int, now: float | None = None) -> int | None:
    """The step a code belongs to (current ±1), or None. Steps at or before ``last_step`` were already used."""
    current = int(time.time() if now is None else now) // STEP_SECONDS
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    for step in (current - 1, current, current + 1):
        if step > last_step and hmac.compare_digest(totp(secret, step), code):
            return step
    return None


def b32(secret: bytes) -> str:
    return base64.b32encode(secret).decode().rstrip("=")


def otpauth_uri(username: str, secret: bytes) -> str:
    label = quote(f"{ISSUER}:{username}", safe=":")
    return f"otpauth://totp/{label}?secret={b32(secret)}&issuer={ISSUER}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


# ---- Secret at rest -------------------------------------------------------------------------------------------------

KEY_BYTES = 32
_key_lock = Lock()


def key_path() -> Path:
    return settings.data_dir / "totp-key"


def _key(*, create: bool = False) -> bytes | None:
    """The sealing key in app-data/totp-key, or None when it is missing and ``create`` is false. Only a missing file is
    ever created (never replaced: a wrong-sized file raises), and it is on disk before anything is sealed with it."""
    path = key_path()
    with _key_lock:
        try:
            value = path.read_bytes()
        except FileNotFoundError:
            if not create:
                return None
            temporary = path.with_name(f".totp-key.{secrets.token_hex(4)}.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(secrets.token_bytes(KEY_BYTES))
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary, path)  # unlike a rename, never replaces a key that appeared meanwhile
                except FileExistsError:
                    pass
            finally:
                temporary.unlink()
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            value = path.read_bytes()
    if len(value) != KEY_BYTES:
        raise RuntimeError(f"{path} is not a {KEY_BYTES}-byte key; restore it from your backup of app-data")
    return value


def _legacy_key() -> bytes | None:
    """2.9.0's key, derived from app-data/art-secret; None without that file (art_urls.secret would mint a new one)."""
    if not art_urls.secret_path().exists():
        return None
    return hmac.new(art_urls.secret(), b"lumina-totp-secret-v1", hashlib.sha256).digest()


def _aad(user_id: str) -> bytes:
    return f"lumina-totp:{user_id}".encode()


def seal(secret: bytes, user_id: str) -> str:
    nonce = secrets.token_bytes(12)
    cipher = AES.new(_key(create=True), AES.MODE_GCM, nonce=nonce)
    cipher.update(_aad(user_id))
    ciphertext, tag = cipher.encrypt_and_digest(secret)
    return "v2:" + base64.urlsafe_b64encode(nonce + ciphertext + tag).decode()


def unseal(value: str | None, user_id: str) -> bytes | None:
    """The secret, or None when missing or unreadable (the key is gone, the value was tampered with or belongs to another
    account). "v1:" values (2.9.0, art-secret key, no user binding) still open until startup reseals them."""
    if not value or value[:3] not in ("v1:", "v2:"):
        return None
    key = _key() if value.startswith("v2:") else _legacy_key()
    if key is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(value[3:])
        cipher = AES.new(key, AES.MODE_GCM, nonce=raw[:12])
        if value.startswith("v2:"):
            cipher.update(_aad(user_id))
        return cipher.decrypt_and_verify(raw[12:-16], raw[-16:])
    except (ValueError, KeyError):
        return None


def reseal_legacy(db: Session) -> None:
    """Startup: move "v1:" secrets to "v2:" (totp-key, bound to the user id). One transaction, so a crash part-way leaves
    every row as it was and the next start retries; "v2:" rows are never touched, so it is a no-op once done. A "v1:"
    row the old key cannot open (art-secret deleted) is left as it is: restoring art-secret and restarting moves it."""
    try:
        with write_transaction(db, name="two_factor_reseal"):
            moved = stuck = 0
            for record in db.scalars(select(User).where(User.totp_secret.like("v1:%"))).all():
                secret = unseal(record.totp_secret, record.id)
                if secret is None:
                    stuck += 1
                    continue
                record.totp_secret = seal(secret, record.id)
                moved += 1
            db.flush()
        if moved:
            audit_log.info("two_factor.resealed count=%s", moved)
        if stuck:
            logger.error("Two-step verification: %s authenticator secret(s) could not be opened with app-data/art-secret; "
                         "restore that file and restart, or reset those members' two-step verification", stuck)
        if _key() is None and db.scalar(select(User.id).where(User.totp_secret.like("v2:%")).limit(1)) is not None:
            logger.error("Two-step verification: app-data/totp-key is missing, so every authenticator is unreadable; "
                         "restore it from your backup of app-data and restart")
    except RuntimeError as exc:  # a damaged totp-key: say so, keep serving (recovery codes still sign in)
        logger.error("Two-step verification: %s", exc)


# ---- Codes ----------------------------------------------------------------------------------------------------------

def random_code(groups: int) -> str:
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(groups))


def normalize_code(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def code_digest(value: str) -> str:
    return hashlib.sha256(normalize_code(value).encode()).hexdigest()


def is_app_password_shape(value: str) -> bool:
    """App passwords are 20 alphabet symbols; anything else never costs an app-password lookup."""
    normalized = normalize_code(value)
    return len(normalized) == 20 and all(ch in _ALPHABET for ch in normalized) and len(value) <= 40


def new_recovery_codes(user: User) -> list[str]:
    """Replace the account's recovery codes; the caller owns the write transaction. The plain codes are shown once."""
    codes = [random_code(4) for _ in range(RECOVERY_CODES)]
    user.totp_recovery = [code_digest(code) for code in codes]
    return codes


# ---- Account state --------------------------------------------------------------------------------------------------

def enabled(user: User) -> bool:
    return getattr(user, "totp_enabled_at", None) is not None


def owners_required(db: Session) -> bool:
    record = db.get(AppSettings, 1)
    return bool(record is not None and record.require_owner_two_factor)


def setup_required(db: Session, user: User) -> bool:
    return user.role == "admin" and not enabled(user) and owners_required(db)


def owner_capable(db: Session | None, user: User) -> bool:
    """Owner powers: an admin, and not one the household still requires to turn two-step verification on."""
    if user.role != "admin" or enabled(user):
        return user.role == "admin"
    db = db or object_session(user)
    # A detached snapshot with no session to ask (tests, async snapshots) keeps its role; request users are attached.
    return db is None or not owners_required(db)


def lock_seconds(failures: int) -> int:
    return min(TOTP_LOCK_SECONDS * 2 ** min(max(failures - TOTP_LOCK_AFTER, 0), 7), TOTP_LOCK_MAX_SECONDS)


def begin(db: Session, user: User) -> bytes:
    """Start (or restart) enrollment with a fresh secret; refused while two-step verification is on."""
    secret = secrets.token_bytes(20)
    with write_transaction(db, name="two_factor_begin"):
        record = db.get(User, user.id, populate_existing=True)
        if enabled(record):
            raise ValueError("Two-step verification is already on.")
        record.totp_secret, record.totp_last_step, record.totp_recovery = seal(secret, record.id), 0, []
        db.flush()
    return secret


def confirm(db: Session, user: User, code: str) -> list[str] | None:
    """Turn it on with a first code from the pending secret; the recovery codes, or None for a wrong code."""
    with write_transaction(db, name="two_factor_confirm"):
        record = db.get(User, user.id, populate_existing=True)
        secret = unseal(record.totp_secret, record.id)
        if enabled(record) or secret is None:
            return None
        step = matching_step(secret, code, last_step=0)
        if step is None:
            return None
        record.totp_enabled_at, record.totp_last_step = utcnow(), step
        codes = new_recovery_codes(record)
        db.flush()
    audit_log.info("two_factor.enable user=%s", user.id)
    return codes


def verify(db: Session, user: User, *, code: str | None = None, recovery_code: str | None = None) -> bool:
    """Check a TOTP code (each step accepted once) or spend one recovery code. Re-read under the writer lock, so two
    concurrent requests can never both use the same step or the same recovery code."""
    with write_transaction(db, name="two_factor_verify"):
        record = db.get(User, user.id, populate_existing=True)
        if record is None or not enabled(record):
            return False
        if recovery_code:
            digest = code_digest(recovery_code)
            remaining = list(record.totp_recovery or [])
            if not any(hmac.compare_digest(digest, stored) for stored in remaining):
                return False
            remaining.remove(digest)
            record.totp_recovery, record.totp_failures, record.totp_locked_until = remaining, 0, None
            db.flush()
            audit_log.info("two_factor.recovery_used user=%s left=%s", user.id, len(remaining))
            return True
        now = utcnow()
        if record.totp_locked_until is not None and record.totp_locked_until > now:
            return False  # paused: even the right code waits (a recovery code or an owner reset still works)
        secret = unseal(record.totp_secret, record.id)
        step = matching_step(secret, code or "", last_step=record.totp_last_step) if secret else None
        if step is None:
            record.totp_failures += 1
            if record.totp_failures >= TOTP_LOCK_AFTER:
                record.totp_locked_until = now + timedelta(seconds=lock_seconds(record.totp_failures))
                audit_log.warning("two_factor.locked user=%s failures=%s", user.id, record.totp_failures)
                if record.totp_failures == TOTP_LOCK_AFTER:  # the first pause only: a guesser cannot flood owners' mail
                    _email_owners_about_lock(db, record)
            db.flush()
            return False
        record.totp_last_step, record.totp_failures, record.totp_locked_until = step, 0, None
        db.flush()
    return True


def lock_email(record: User) -> tuple[str, str, str]:
    """(subject, text, html) telling the owners a member's authenticator codes are paused."""
    name = record.display_name or record.username
    minutes = TOTP_LOCK_SECONDS // 60
    subject = f"Two-step verification paused for {name}"
    text, html = email_layout.compose(
        subject=subject, preheader=f"{TOTP_LOCK_AFTER} wrong codes in a row. Codes are paused for {minutes} minutes.",
        eyebrow="Security notice", heading=subject,
        paragraphs=(f"{name} ({record.username}) entered {TOTP_LOCK_AFTER} wrong two-step verification codes in a row, so "
                    f"Lumina paused authenticator codes for that account for {minutes} minutes.",
                    "If it wasn't them, someone may know their password. Change it, or reset their two-step verification, "
                    "under Settings → Members."),
        button=("Review member", f"{public_address.link_base()}/settings/members/{quote(record.id, safe='')}"),
        why="You're getting this because you're an owner of Lumina and security notices go to your notification address.")
    return subject, text, html


def _email_owners_about_lock(db: Session, record: User) -> None:
    """Email each active vault owner with a notification address, after commit, when SMTP is set up (Requests' mailer)."""
    from app.services.requests import notify

    smtp = notify.Smtp.of(db.get(AppSettings, 1))
    if smtp is None:
        return
    emails = sorted({to for to in db.scalars(
        select(UserSettings.notify_email).join(User, User.id == UserSettings.user_id)
        .where(User.role == "admin", User.is_active.is_(True), UserSettings.notify_email.is_not(None))) if to})
    subject, text, html = lock_email(record)

    def deliver() -> None:
        for to in emails:
            try:
                notify.send(smtp, to, subject, text, html)
            except Exception as exc:  # noqa: BLE001 - logged by type only; email never breaks sign-in
                logger.warning("Two-step lock email failed: %s", type(exc).__name__)

    if emails:
        queue_after_commit(db, lambda: Thread(target=deliver, name="two-factor-email", daemon=True).start())


def clear(db: Session, user_id: str) -> None:
    """Turn it off: the secret, its replay guard and every recovery code go. Trusted-device cookies stop matching."""
    with write_transaction(db, name="two_factor_clear"):
        record = db.get(User, user_id, populate_existing=True)
        if record is None:
            raise LookupError(user_id)
        record.totp_secret, record.totp_enabled_at, record.totp_last_step, record.totp_recovery = None, None, 0, []
        record.totp_failures, record.totp_locked_until = 0, None
        db.flush()


# ---- Sign-in challenges ---------------------------------------------------------------------------------------------

@dataclass
class Challenge:
    user_id: str
    client_key: str
    password_digest: str  # a password change while the challenge is open voids it
    remember_on_device: bool
    expires: float
    attempts: int = 0


# Per-process store, like the rate limiter (single-process server); a restart just asks for the password again.
_challenges: dict[str, Challenge] = {}
_challenges_lock = Lock()


def _password_digest(user: User) -> str:
    return hashlib.sha256((user.password_hash or "").encode()).hexdigest()


def create_challenge(user: User, client_key: str, *, remember_on_device: bool) -> str:
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _challenges_lock:
        for key in [key for key, value in _challenges.items() if value.expires <= now]:
            del _challenges[key]
        _challenges[hashlib.sha256(token.encode()).hexdigest()] = Challenge(
            user.id, client_key, _password_digest(user), remember_on_device, now + CHALLENGE_SECONDS,
        )
    return token


def open_challenge(token: str, client_key: str, user_lookup) -> tuple[Challenge, User] | None:  # noqa: ANN001
    """The live challenge and its member, only from the client that answered the password and while nothing changed."""
    key = hashlib.sha256(token.encode()).hexdigest()
    with _challenges_lock:
        challenge = _challenges.get(key)
        if challenge is None or challenge.expires <= time.monotonic():
            _challenges.pop(key, None)
            return None
    if not hmac.compare_digest(challenge.client_key, client_key):
        return None
    user = user_lookup(challenge.user_id)
    if user is None or not user.is_active or not enabled(user) or challenge.password_digest != _password_digest(user):
        return None
    return challenge, user


def fail_challenge(token: str) -> None:
    key = hashlib.sha256(token.encode()).hexdigest()
    with _challenges_lock:
        challenge = _challenges.get(key)
        if challenge is not None:
            challenge.attempts += 1
            if challenge.attempts >= CHALLENGE_ATTEMPTS:
                del _challenges[key]


def consume_challenge(token: str) -> bool:
    """Single use: True only for the one caller that removed it."""
    with _challenges_lock:
        return _challenges.pop(hashlib.sha256(token.encode()).hexdigest(), None) is not None


# ---- Trusted device ("Don't ask again on this device for 30 days") ---------------------------------------------------

def trust_cookie_name() -> str:
    return f"{settings.session_cookie_name}{TRUST_COOKIE_SUFFIX}"


def _trust_signature(user: User, expires: int) -> str:
    # Bound to the password hash and the sealed secret: a password change, a reset or re-enrollment voids it.
    message = f"tfa-trust:{user.id}:{expires}:{user.password_hash}:{user.totp_secret}".encode()
    return base64.urlsafe_b64encode(hmac.new(art_urls.secret(), message, hashlib.sha256).digest()[:18]).decode()


def apply_trust_cookie(response: Response, user: User) -> None:
    # One trusted member per browser (the newest); add entries per member if shared 2FA browsers appear.
    expires = int(time.time()) + TRUST_DAYS * 86400
    response.set_cookie(
        key=trust_cookie_name(), value=f"{user.id}.{expires}.{_trust_signature(user, expires)}", max_age=TRUST_DAYS * 86400,
        httponly=True, secure=settings.session_cookie_secure, samesite=settings.session_cookie_samesite, path=TRUST_PATH,
    )


def trusted(request: Request, user: User) -> bool:
    user_id, _, rest = (request.cookies.get(trust_cookie_name()) or "")[:200].partition(".")
    expires_text, _, signature = rest.partition(".")
    if user_id != user.id or not expires_text.isdigit() or int(expires_text) <= time.time():
        return False
    return hmac.compare_digest(signature, _trust_signature(user, int(expires_text)))


# ---- Owner recovery CLI ---------------------------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "reset":
        print("usage: python -m app.services.two_factor reset <username>", file=sys.stderr)
        return 2
    from app.db import session_scope

    with session_scope() as db:
        user = db.query(User).filter(User.username == argv[1].strip().lower()).first()
        if user is None:
            print("No such user.", file=sys.stderr)
            return 1
        clear(db, user.id)
        audit_log.info("two_factor.reset actor=cli user=%s", user.id)
    print("Two-step verification is off for that account; it signs in with its password and can turn it on again.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(_cli(sys.argv[1:]))
