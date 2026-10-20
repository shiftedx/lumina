"""Member-owned YouTube browser sessions; credentials never enter extractor options or files."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from http.cookiejar import Cookie
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import YouTubeSession, utcnow
from app.persistence import write_transaction
from app.services import two_factor

MAX_COOKIE_BYTES = 256 * 1024
AUTH_COOKIES = frozenset({"SID", "SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"})
DOMAINS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})
FALLBACK = "YouTube needs a signed-in session or permission to play this video. Connect or reconnect YouTube in your personal settings, or watch it on YouTube."


class InvalidYouTubeSession(ValueError):
    pass


@dataclass(frozen=True)
class PersonalSession:
    generation: str
    cookies: tuple[Cookie, ...] = field(repr=False)


def parse_cookies(text: str) -> list[dict]:
    if len(text.encode()) > MAX_COOKIE_BYTES or not text.lstrip('\ufeff').startswith(('# Netscape HTTP Cookie File', '# HTTP Cookie File')):
        raise InvalidYouTubeSession('Choose a Netscape-format YouTube cookies.txt file (up to 256 KB).')
    cookies = []
    for line in text.lstrip('\ufeff').splitlines():
        if line.startswith('#HttpOnly_'):
            line = line[len('#HttpOnly_'):]
        elif not line or line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) != 7:
            raise InvalidYouTubeSession('The cookie file is malformed. Export a fresh YouTube cookies.txt file.')
        domain, subdomains, path, secure, expiry, name, value = fields
        if domain.lstrip('.').lower() not in DOMAINS:
            continue  # Whole-browser exports must never grant access to other sites.
        if (subdomains not in ('TRUE', 'FALSE') or secure not in ('TRUE', 'FALSE') or not expiry.isdigit() or len(expiry) > 12
                or not re.fullmatch(r'[A-Za-z0-9_\-]{1,128}', name) or not 1 <= len(value) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 or c == ';' for c in value)
                or any(ord(c) < 32 or ord(c) > 126 for c in path) or not path.startswith('/')):
            raise InvalidYouTubeSession('The YouTube cookies are invalid. Export a fresh cookie file.')
        expires = int(expiry) or None
        if expires is not None and expires <= time.time():
            continue
        cookies.append(dict(domain=domain.lower(), subdomains=subdomains == 'TRUE', path=path, expires=expires, name=name, value=value))
        if len(cookies) > 256:
            raise InvalidYouTubeSession('The cookie file contains too many YouTube cookies.')
    if not any(cookie['name'] in AUTH_COOKIES for cookie in cookies):
        raise InvalidYouTubeSession('No current YouTube sign-in was found. Sign in on YouTube and export a fresh cookie file.')
    return cookies


def load(db: Session, user_id: str) -> PersonalSession | None:
    row = db.get(YouTubeSession, user_id, populate_existing=True)
    if row is None or row.needs_reconnect:
        return None
    raw = two_factor.unseal(row.encrypted_cookies, f'youtube-session:{user_id}')
    if raw is None:
        return None
    data = json.loads(raw)
    current = [item for item in data if item['expires'] is None or item['expires'] > time.time()]
    if not any(item['name'] in AUTH_COOKIES for item in current):
        return None
    cookies = tuple(Cookie(0, item['name'], item['value'], None, False, item['domain'], item['subdomains'],
                           item['domain'].startswith('.'), item['path'], True, True, item['expires'],
                           item['expires'] is None, None, None, {}, False) for item in current)
    return PersonalSession(row.generation, cookies)


def snapshot(db: Session, user_id: str) -> dict:
    row = db.get(YouTubeSession, user_id)
    status = 'not_connected' if row is None else 'connected' if load(db, user_id) else 'needs_reconnect'
    return {'status': status, 'updated_at': row.updated_at if row else None}


def connect(db: Session, user_id: str, text: str) -> dict:
    cookies = parse_cookies(text)
    sealed = two_factor.seal(json.dumps(cookies).encode(), f'youtube-session:{user_id}')
    with write_transaction(db, name='youtube_session_connect'):
        row = db.get(YouTubeSession, user_id)
        if row is None:
            row = YouTubeSession(user_id=user_id)
            db.add(row)
        row.encrypted_cookies, row.generation = sealed, str(uuid4())
        row.needs_reconnect, row.updated_at = False, utcnow()
    return snapshot(db, user_id)


def mark_reconnect(db: Session, user_id: str, generation: str) -> None:
    with write_transaction(db, name='youtube_session_reconnect'):
        row = db.get(YouTubeSession, user_id, populate_existing=True)
        if row and row.generation == generation:
            row.needs_reconnect = True


def generation_valid(user_id: str, generation: str) -> bool:
    with SessionLocal() as db:
        session = load(db, user_id)
        return session is not None and session.generation == generation


def video_url(url: str) -> str | None:
    from urllib.parse import parse_qs, urlsplit
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    if parsed.hostname == 'youtu.be':
        identity = parsed.path.removeprefix('/')
    elif parsed.hostname in DOMAINS:
        identity = (parse_qs(parsed.query).get('v') or [''])[0] if parsed.path == '/watch' else parsed.path.split('/')[-1] if parsed.path.startswith(('/shorts/', '/live/', '/embed/')) else ''
    else:
        return None
    return f'https://www.youtube.com/watch?v={identity}' if re.fullmatch(r'[A-Za-z0-9_-]{11}', identity) else None
