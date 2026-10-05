"""Read-only Twitch chat through Twitch's anonymous IRC login.

Twitch lets anyone read a channel's chat as ``justinfan<digits>`` without an
account (posting needs one). Chat is pushed, so one reader thread per channel
buffers normalized events and ``fetch_page`` hands over what arrived since the
last call, which fits the poll-shaped ``LiveChatFetcher`` seam. ``close`` (or a
dropped connection) ends the reader. The browser never sees the connection.
"""

from __future__ import annotations

import re
import secrets
import socket
import ssl
from collections import deque
from collections.abc import Callable, Mapping
from threading import Event, Lock, Thread
from time import monotonic

from app.services.live_chat_seam import LiveChatBootstrap, LiveChatPage
from app.services.timed_chat import EVENT_KIND_MESSAGE, EVENT_KIND_PAID_MESSAGE, EVENT_KIND_SYSTEM, TimedChatAuthor, TimedChatEvent

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
_CHANNEL = re.compile(r"^https?://(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{3,25})/?(?:[?#].*)?$")
_NOT_CHANNELS = {"videos", "directory", "settings", "p", "downloads", "jobs", "search", "subscriptions", "inventory", "wallet"}
_BADGES = {"broadcaster": "owner", "moderator": "moderator", "subscriber": "member", "founder": "member", "vip": "vip", "partner": "verified"}
_BUFFER = 500
_UNREAD_SECONDS = 120.0  # a reader nobody drains for this long disconnects itself (viewer gone, recording ended)


def twitch_channel(source_url: str) -> str | None:
    match = _CHANNEL.match(source_url or "")
    if match is None or match.group(1).lower() in _NOT_CHANNELS:
        return None
    return match.group(1).lower()


def _unescape_tag(value: str) -> str:
    return value.replace("\\s", " ").replace("\\:", ";").replace("\\r", "\r").replace("\\n", "\n").replace("\\\\", "\\")


def parse_irc_line(line: str) -> TimedChatEvent | None:
    """One IRC line to a normalized event: PRIVMSG (chat, Bits) and USERNOTICE (subs, raids); anything else is None."""

    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw_tags, _, line = line[1:].partition(" ")
        for pair in raw_tags.split(";"):
            key, _, value = pair.partition("=")
            tags[key] = _unescape_tag(value)
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    else:
        prefix = ""
    command, _, rest = line.partition(" ")
    if command not in {"PRIVMSG", "USERNOTICE"}:
        return None
    _, _, text = rest.partition(" :")
    message_id = tags.get("id")
    if not message_id:
        return None
    login = prefix.split("!", 1)[0]
    name = tags.get("display-name") or login or "Viewer"
    badges = tuple(dict.fromkeys(_BADGES[badge.split("/", 1)[0]] for badge in tags.get("badges", "").split(",") if badge.split("/", 1)[0] in _BADGES))
    author = TimedChatAuthor(name=name[:120], channel_id=tags.get("user-id") or None, badges=badges[:6])
    if command == "USERNOTICE":
        notice = tags.get("system-msg", "").strip()
        body = f"{notice} {text}".strip() if text else notice
        return TimedChatEvent(id=f"twitch:{message_id}", offset_ms=None, kind=EVENT_KIND_SYSTEM, text=body[:500], author=author)
    bits = tags.get("bits")
    kind = EVENT_KIND_PAID_MESSAGE if bits else EVENT_KIND_MESSAGE
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        text = text[8:-1]
    return TimedChatEvent(id=f"twitch:{message_id}", offset_ms=None, kind=kind, text=text[:500], author=author, amount=f"{bits} Bits" if bits else None)


class _Reader:
    def __init__(self, channel: str, connect: Callable[[], socket.socket]) -> None:
        self.channel = channel
        self.events: deque[TimedChatEvent] = deque(maxlen=_BUFFER)
        self.lock = Lock()
        self.stopped = Event()
        self.alive = True
        self.drained_at = monotonic()
        self._connect = connect
        self.thread = Thread(target=self._run, name=f"twitch-chat-{channel}", daemon=True)

    def _run(self) -> None:
        try:
            with self._connect() as conn:
                conn.sendall(f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nPASS SCHMOOPIIE\r\nNICK justinfan{secrets.randbelow(89999) + 10000}\r\nJOIN #{self.channel}\r\n".encode())
                pending = b""
                while not self.stopped.is_set() and monotonic() - self.drained_at < _UNREAD_SECONDS:
                    try:
                        chunk = conn.recv(65536)
                    except TimeoutError:
                        continue
                    if not chunk:
                        break
                    pending += chunk
                    *lines, pending = pending.split(b"\r\n")
                    for raw in lines:
                        line = raw.decode("utf-8", errors="replace")
                        if line.startswith("PING"):
                            conn.sendall(("PONG" + line[4:] + "\r\n").encode())
                        elif (event := parse_irc_line(line)) is not None:
                            with self.lock:
                                self.events.append(event)
        except OSError:
            pass
        finally:
            self.alive = False

    def drain(self) -> list[TimedChatEvent]:
        self.drained_at = monotonic()
        with self.lock:
            drained = list(self.events)
            self.events.clear()
        return drained


def _tls_connect() -> socket.socket:
    raw = socket.create_connection((IRC_HOST, IRC_PORT), timeout=15)
    conn = ssl.create_default_context().wrap_socket(raw, server_hostname=IRC_HOST)
    conn.settimeout(5)  # recv wakes every 5 s so close() is noticed promptly
    return conn


class TwitchChatFetcher:
    def __init__(self, connect: Callable[[], socket.socket] = _tls_connect) -> None:
        self._connect = connect
        self._readers: dict[str, _Reader] = {}
        self._lock = Lock()

    def bootstrap(self, source_url: str, owner_user_id: str) -> LiveChatBootstrap:
        channel = twitch_channel(source_url)
        if channel is None:
            return LiveChatBootstrap(continuation=None, status="unavailable")
        key = f"twitch-irc:{channel}:{secrets.token_hex(4)}"
        reader = _Reader(channel, self._connect)
        with self._lock:
            self._readers[key] = reader
        reader.thread.start()
        return LiveChatBootstrap(continuation=key, headers={}, status="available")

    def fetch_page(self, continuation: str, *, headers: Mapping[str, str]) -> LiveChatPage:
        with self._lock:
            reader = self._readers.get(continuation)
        if reader is None:
            return LiveChatPage(actions=[], continuation=None, status="ended")
        events = reader.drain()
        if not reader.alive:
            self.close(continuation)
            return LiveChatPage(actions=list(events), continuation=None, status="ended")
        return LiveChatPage(actions=list(events), continuation=continuation, status="active")

    def close(self, continuation: str) -> None:
        with self._lock:
            reader = self._readers.pop(continuation, None)
        if reader is not None:
            reader.stopped.set()


class ProviderLiveChatFetcher:
    """Routes a source to its provider's chat reader: Twitch IRC for twitch.tv, YouTube otherwise."""

    def __init__(self, youtube, twitch: TwitchChatFetcher) -> None:  # noqa: ANN001
        self._youtube = youtube
        self._twitch = twitch

    def _for(self, continuation_or_url: str):  # noqa: ANN202
        return self._twitch if continuation_or_url.startswith("twitch-irc:") or twitch_channel(continuation_or_url) else self._youtube

    def bootstrap(self, source_url: str, owner_user_id: str) -> LiveChatBootstrap:
        return self._for(source_url).bootstrap(source_url, owner_user_id)

    def fetch_page(self, continuation: str, *, headers: Mapping[str, str]) -> LiveChatPage:
        return self._for(continuation).fetch_page(continuation, headers=headers)

    def close(self, continuation: str) -> None:
        if continuation.startswith("twitch-irc:"):
            self._twitch.close(continuation)
