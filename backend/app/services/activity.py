"""Admin Activity: who is playing what right now, stopping it, and what was played.

Transcodes and remuxes come from the in-memory PlaybackSession registry; remote streams from the HLS relays. Direct play has no
server-side state, so a small tracker is fed by Jellyfin Sessions/Playing reports, Lumina web progress checkpoints and library
stream requests. A tracked session is active while something arrived in the last 60 s. Every ended session becomes one
playback_history row (kept 90 days, pruned on write).
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import AppSettings, DeviceToken, DownloadJob, LibraryItem, LiveRecording, MediaTitle, PlaybackHistory, User
from app.services import job_manager, local_playback_sessions
from app.services.hwaccel import hwaccel
from app.services.local_playback_sessions import PlaybackSession, sessions
from app.services.media_probe import media_tool
from app.services.titles import image_url

log = logging.getLogger(__name__)

ACTIVE_SECONDS = 60
BLOCK_SECONDS = 120
RECENT_END_SECONDS = 30  # late reports from a player whose session just ended must not start a ghost direct session
RETENTION = timedelta(days=90)
MIN_HISTORY_SECONDS = 10  # a session shorter than this (a seek replacing its encode, a probe) is not a viewing
WEB = "web"
_clock = time.time  # tests replace it

_lock = threading.Lock()
_blocked: dict[tuple[str, str, str], float] = {}
_recent_end: dict[tuple[str, str, str], float] = {}


@dataclass
class _Direct:
    user_id: str
    item_id: str
    device: str
    started: float
    last_seen: float
    first_position: float | None = None
    max_position: float | None = None
    position: float | None = None
    duration: float | None = None

    @property
    def id(self) -> str:
        return "d:" + hashlib.sha1(f"{self.user_id}|{self.item_id}|{self.device}".encode()).hexdigest()[:24]


_direct: dict[tuple[str, str, str], _Direct] = {}


def _wall(utc_ts: float) -> datetime:
    return datetime.fromtimestamp(utc_ts, UTC).replace(tzinfo=None)


# ---- stop guard -----------------------------------------------------------------------------------------------------------

def block(user_id: str, item_id: str, device: str) -> None:
    with _lock:
        _blocked[(user_id, item_id, device)] = _clock() + BLOCK_SECONDS


def guard(user_id: str, item_id: str, device: str) -> None:
    """403 stopped_by_admin for 120 s after an admin stopped this (member, item, device); one guard for every media route."""
    with _lock:
        until = _blocked.get((user_id, item_id, device))
        if until is not None and until <= _clock():
            del _blocked[(user_id, item_id, device)]
            until = None
    if until is not None:
        raise HTTPException(status_code=403, detail={"code": "stopped_by_admin", "message": "Playback was stopped by the server owner."})


# ---- direct-play tracker --------------------------------------------------------------------------------------------------

def touch(user_id: str, item_id: str, device: str = WEB, *, position: float | None = None, duration: float | None = None) -> None:
    """A progress report or media request for (member, item, device) arrived."""
    key, now = (user_id, item_id, device), _clock()
    with _lock:
        if _blocked.get(key, 0) > now or now - _recent_end.get(key, 0) < RECENT_END_SECONDS:
            return
        entry = _direct.get(key)
        if entry is None or now - entry.last_seen > ACTIVE_SECONDS:
            entry = _direct[key] = _Direct(user_id, item_id, device, now, now)
        entry.last_seen = now
        if position is not None:
            entry.position = position
            entry.first_position = position if entry.first_position is None else entry.first_position
            entry.max_position = position if entry.max_position is None else max(entry.max_position, position)
        if duration:
            entry.duration = duration


def finish(user_id: str, item_id: str, device: str = WEB) -> None:
    """The player reported it stopped (Jellyfin /Sessions/Playing/Stopped)."""
    with _lock:
        entry = _direct.pop((user_id, item_id, device), None)
    if entry is not None and sessions.for_device(user_id, device, item_id) is None:
        _record_direct(entry, ended=entry.last_seen, by_admin=False)


def sweep() -> None:
    """Close direct sessions that went quiet; the 60 s maintenance tick and every listing call this."""
    now = _clock()
    with _lock:
        stale = [key for key, e in _direct.items() if now - e.last_seen > ACTIVE_SECONDS]
        gone = [_direct.pop(key) for key in stale]
        for key in [k for k, until in _blocked.items() if until <= now]:
            del _blocked[key]
        for key in [k for k, at in _recent_end.items() if now - at >= RECENT_END_SECONDS]:
            del _recent_end[key]
    for entry in gone:
        if sessions.for_device(entry.user_id, entry.device, entry.item_id) is None:  # an encode records its own row
            _record_direct(entry, ended=entry.last_seen, by_admin=False)


def _watched(started: float, last_seen: float, first: float | None, high: float | None) -> float:
    if first is not None and high is not None:
        return max(0.0, high - first)
    return max(0.0, last_seen - started)


# ---- labels ---------------------------------------------------------------------------------------------------------------

def _label(db: Session, item_id: str | None) -> dict[str, Any]:
    """Title, subtitle, artwork and duration of a Library item, as a series/movie reads in the library."""
    item = db.get(LibraryItem, item_id) if item_id else None
    if item is None:
        return {"title": "Unknown title", "subtitle": None, "artwork_url": None, "duration": None}
    title = db.get(MediaTitle, item.title_id) if item.title_id and item.extra_type is None else None
    if title is None:
        return {"title": item.title, "subtitle": item.uploader, "artwork_url": None, "duration": item.duration}
    if title.type != "episode":
        return {"title": title.name, "subtitle": str(title.year) if title.year else None, "artwork_url": image_url(title, "Primary"), "duration": item.duration}
    season = db.get(MediaTitle, title.parent_id) if title.parent_id else None
    series = db.get(MediaTitle, season.parent_id) if season is not None and season.parent_id else None
    parts = [f"S{season.index_number}" if season is not None and season.index_number is not None else None,
             f"E{title.index_number}" if title.index_number is not None else None, title.name]
    return {
        "title": series.name if series is not None else title.name, "subtitle": " · ".join(p for p in parts if p) or None,
        "artwork_url": image_url(series, "Primary") if series is not None else image_url(title, "Primary"), "duration": item.duration,
    }


def _client(db: Session, device: str, cache: dict[str, dict]) -> dict[str, Any]:
    if device not in cache:
        if device == WEB:
            cache[device] = {"kind": "web", "name": "Lumina web", "device": None}
        else:
            token = db.get(DeviceToken, device)
            cache[device] = {"kind": "app", "name": (token.client if token else None) or "App", "device": token.device_name if token else None}
    return cache[device]


def _user(db: Session, user_id: str | None, cache: dict[str, dict]) -> dict[str, str]:
    key = user_id or ""
    if key not in cache:
        user = db.get(User, user_id) if user_id else None
        cache[key] = {"id": key, "name": (user.display_name or user.username) if user else "Unknown member"}
    return cache[key]


def _relay_title(url: str) -> tuple[str, str | None]:
    parts = urlsplit(url)
    return parts.netloc or "Remote stream", (parts.netloc + parts.path)[:160] or None  # never the query: it can carry tokens


# ---- history --------------------------------------------------------------------------------------------------------------

def _write_history(*, user_id: str, source: str, title: str, subtitle: str | None, item_id: str | None, client: dict, method: str,
                   hardware: str | None, video: dict | None, started: float, ended: float, watched: float, by_admin: bool, user_name: str) -> None:
    if ended - started < MIN_HISTORY_SECONDS and not by_admin:
        return
    with session_scope() as db:
        db.add(PlaybackHistory(
            id=str(uuid.uuid4()), user_id=user_id, user_name=user_name, source=source, title=title, subtitle=subtitle, item_id=item_id,
            client=client, method=method, hardware=hardware, video=video, started_at=_wall(started), ended_at=_wall(ended),
            watched_seconds=round(watched, 1), stopped_by_admin=by_admin,
        ))
        db.query(PlaybackHistory).filter(PlaybackHistory.ended_at < _wall(_clock()) - RETENTION).delete()


def _record_direct(entry: _Direct, *, ended: float, by_admin: bool) -> None:
    try:
        with session_scope() as db:
            label, user = _label(db, entry.item_id), _user(db, entry.user_id, {})
            client = _client(db, entry.device, {})
        _write_history(
            user_id=entry.user_id, source="library", title=label["title"], subtitle=label["subtitle"], item_id=entry.item_id, client=client,
            method="direct", hardware=None, video=None, started=entry.started, ended=ended, by_admin=by_admin, user_name=user["name"],
            watched=_watched(entry.started, ended, entry.first_position, entry.max_position),
        )
    except Exception:
        log.exception("activity history write failed")


def hardware_of(session: PlaybackSession) -> str | None:
    if session.kind == "video_hw":
        return session.hw if session.hw != "none" else None
    return "software" if session.kind == "video_sw" else None


def _transcode_ended(session: PlaybackSession) -> None:
    """local_playback_sessions hook: one history row per ended encode or remux."""
    key, now = (session.user_id, session.item_id, session.device), _clock()
    replaced = sessions.for_device(session.user_id, session.device, session.item_id) not in (None, session)
    entry = None
    if not replaced:  # a seek replaces the encode: the player keeps reporting to the new one
        with _lock:
            entry = _direct.pop(key, None)
            _recent_end[key] = now
    last_seen = now - (time.monotonic() - session.last_access)
    with session_scope() as db:
        label, user, client = _label(db, session.item_id), _user(db, session.user_id, {}), _client(db, session.device, {})
    _write_history(
        user_id=session.user_id, source="library", title=label["title"], subtitle=label["subtitle"], item_id=session.item_id, client=client,
        method="remux" if session.mode == "remux" else "transcode", hardware=hardware_of(session), video=_video(session), started=session.started_wall,
        ended=now, by_admin=session.stopped_by_admin, user_name=user["name"],
        watched=_watched(session.started_wall, last_seen, entry.first_position if entry else None, entry.max_position if entry else None),
    )


def _relay_ended(info: dict[str, Any]) -> None:
    title, subtitle = _relay_title(info["source_url"])
    try:
        with session_scope() as db:
            user = _user(db, info["user_id"], {})
        _write_history(
            user_id=info["user_id"], source="remote", title=title, subtitle=subtitle, item_id=None, client={"kind": "web", "name": "Lumina web", "device": None},
            method="relay", hardware=None, video=None, started=info["started"], ended=_clock(), by_admin=info["by_admin"], user_name=user["name"],
            watched=max(0.0, info["last_seen"] - info["started"]),
        )
    except Exception:
        log.exception("activity history write failed")


def install(relays: tuple) -> None:
    local_playback_sessions.on_session_end = _transcode_ended
    for relay in relays:
        relay.on_end = _relay_ended


def _video(session: PlaybackSession) -> dict | None:
    return session.info.get("video")


# ---- listing --------------------------------------------------------------------------------------------------------------

def _session_dict(*, id: str, source: str, user: dict, label: dict, client: dict, method: str, video: dict | None, audio: dict | None,
                  hardware: str | None, speed: float | None, throttled: bool, position: float | None, started: float, last_seen: float,
                  item_id: str | None, duration: float | None = None) -> dict[str, Any]:
    return {
        "id": id, "source": source, "user": user, "title": label["title"], "subtitle": label["subtitle"], "item_id": item_id,
        "artwork_url": label["artwork_url"], "client": client, "method": method, "video": video, "audio": audio, "hardware": hardware,
        "speed": speed, "throttled": throttled, "position_seconds": position,
        "duration_seconds": duration if duration is not None else label["duration"],
        "started_at": _wall(started), "last_seen_at": _wall(last_seen), "stoppable": True,
    }


def list_sessions(db: Session, relays: tuple) -> list[dict[str, Any]]:
    sweep()
    users: dict[str, dict] = {}
    clients: dict[str, dict] = {}
    now, mono = _clock(), time.monotonic()
    out: list[dict[str, Any]] = []
    with _lock:
        tracked = dict(_direct)
    for s in sessions.live():
        entry = tracked.get((s.user_id, s.item_id, s.device))
        out.append(_session_dict(
            id="t:" + s.id, source="library", user=_user(db, s.user_id, users), label=_label(db, s.item_id), client=_client(db, s.device, clients),
            method="remux" if s.mode == "remux" else "transcode", video=_video(s), audio=s.info.get("audio"), hardware=hardware_of(s),
            speed=local_playback_sessions._speed(s.directory), throttled=s.throttled, position=entry.position if entry else None,
            duration=entry.duration if entry else None, started=s.started_wall, last_seen=now - (mono - s.last_access), item_id=s.item_id,
        ))
    encoded = {(s.user_id, s.item_id, s.device) for s in sessions.live()}
    for key, e in tracked.items():
        if key not in encoded and now - e.last_seen <= ACTIVE_SECONDS:
            out.append(_session_dict(
                id=e.id, source="library", user=_user(db, e.user_id, users), label=_label(db, e.item_id), client=_client(db, e.device, clients),
                method="direct", video=None, audio=None, hardware=None, speed=None, throttled=False, position=e.position, duration=e.duration,
                started=e.started, last_seen=e.last_seen, item_id=e.item_id,
            ))
    for relay in relays:
        for r in relay.list_streams():
            title, subtitle = _relay_title(r["source_url"])
            out.append(_session_dict(
                id="r:" + r["stream_id"], source="remote", user=_user(db, r["user_id"], users),
                label={"title": title, "subtitle": subtitle, "artwork_url": None, "duration": None},
                client={"kind": "web", "name": "Lumina web", "device": None}, method="relay", video=None, audio=None, hardware=None,
                speed=None, throttled=False, position=None, started=r["started"], last_seen=r["last_seen"], item_id=None,
            ))
    return sorted(out, key=lambda s: s["started_at"])


def list_downloads(db: Session) -> list[dict[str, Any]]:
    users: dict[str, dict] = {}
    jobs = db.query(DownloadJob).filter(DownloadJob.status == "running").order_by(DownloadJob.created_at).all()
    live = job_manager.LIVE_PROGRESS
    for stale in set(live) - {j.id for j in jobs}:
        live.pop(stale, None)
    return [{
        "id": j.id, "title": (j.preview_snapshot or {}).get("title"), "user": _user(db, j.user_id, users),
        "progress": (live.get(j.id) or (None, None))[0], "speed_bytes": (live.get(j.id) or (None, None))[1], "started_at": j.started_at,
    } for j in jobs]


def list_recordings(db: Session) -> list[dict[str, Any]]:
    users: dict[str, dict] = {}
    rows = db.query(LiveRecording).filter(LiveRecording.status.in_(("queued", "live", "stopping", "finalizing"))).order_by(LiveRecording.created_at).all()
    return [{"id": r.id, "title": r.title, "user": _user(db, r.user_id, users), "status": r.status, "started_at": r.created_at} for r in rows]


# ---- server stats (Linux /proc; null elsewhere) --------------------------------------------------------------------------

_cpu_prev: tuple[float, float] | None = None  # (busy jiffies, total jiffies)
_proc_prev: dict[int, float] = {}  # pid -> utime + stime jiffies at the last sample
_stats_lock = threading.Lock()


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def parse_cpu(text: str | None) -> tuple[float, float] | None:
    """(busy, total) jiffies from the aggregate ``cpu`` line of /proc/stat (idle and iowait are not busy)."""
    line = next((l for l in (text or "").splitlines() if l.startswith("cpu ")), None)
    if line is None:
        return None
    try:
        values = [float(v) for v in line.split()[1:]]
    except ValueError:
        return None
    if len(values) < 5:
        return None
    total = sum(values[:8])
    return total - values[3] - values[4], total


def parse_meminfo(text: str | None) -> dict[str, int] | None:
    fields = {}
    for line in (text or "").splitlines():
        name, _, rest = line.partition(":")
        if name in ("MemTotal", "MemAvailable") and rest.split():
            fields[name] = int(rest.split()[0]) * 1024
    if len(fields) != 2:
        return None
    return {"used_bytes": fields["MemTotal"] - fields["MemAvailable"], "total_bytes": fields["MemTotal"]}


def parse_loadavg(text: str | None) -> list[float] | None:
    try:
        return [float(v) for v in (text or "").split()[:3]] if len((text or "").split()) >= 3 else None
    except ValueError:
        return None


def parse_uptime(text: str | None) -> float | None:
    try:
        return float((text or "").split()[0])
    except (IndexError, ValueError):
        return None


def parse_process_jiffies(text: str | None) -> float | None:
    """utime + stime from /proc/<pid>/stat; the command name may contain spaces and parentheses, so split after the last ')'."""
    if not text or ")" not in text:
        return None
    fields = text.rsplit(")", 1)[1].split()
    try:
        return float(fields[11]) + float(fields[12])
    except (IndexError, ValueError):
        return None


def server_stats(db: Session) -> dict[str, Any]:
    global _cpu_prev
    live = [s for s in sessions.live() if s.process.poll() is None]
    cpu = transcoder = None
    with _stats_lock:
        now_cpu = parse_cpu(_read("/proc/stat"))
        procs = {s.process.pid: parse_process_jiffies(_read(f"/proc/{s.process.pid}/stat")) for s in live}
        if now_cpu is not None and _cpu_prev is not None and now_cpu[1] > _cpu_prev[1]:
            span = now_cpu[1] - _cpu_prev[1]
            cpu = round(100 * (now_cpu[0] - _cpu_prev[0]) / span, 1)
            used = [(j - _proc_prev[pid]) for pid, j in procs.items() if j is not None and pid in _proc_prev]
            transcoder = round(100 * sum(used) / span, 1) if used or not live else None
        _cpu_prev = now_cpu or _cpu_prev
        _proc_prev.clear()
        _proc_prev.update({pid: j for pid, j in procs.items() if j is not None})
    return {
        "cpu_percent": cpu, "memory": parse_meminfo(_read("/proc/meminfo")), "load_average": parse_loadavg(_read("/proc/loadavg")),
        "uptime_seconds": parse_uptime(_read("/proc/uptime")), "transcoder_cpu_percent": transcoder, "ffmpeg_processes": len(live),
        "hardware": hardware_status(db),
    }


def hardware_status(db: Session) -> dict[str, Any]:
    record = db.get(AppSettings, 1)
    mode = (record.hwaccel if record else None) or "auto"
    status = hwaccel.peek(media_tool(db, "ffmpeg"), mode)
    active = status.active if status is not None and status.active != "none" and not hwaccel.disabled else None
    return {"mode": mode, "active": active, "disabled": hwaccel.disabled, "failures": hwaccel.failures, "fallbacks": hwaccel.fallbacks}


def snapshot(db: Session, relays: tuple) -> dict[str, Any]:
    return {
        "generated_at": _wall(_clock()), "sessions": list_sessions(db, relays), "downloads": list_downloads(db),
        "recordings": list_recordings(db), "server": server_stats(db),
    }


# ---- stop -----------------------------------------------------------------------------------------------------------------

def stop(session_id: str, relays: tuple) -> bool:
    kind, _, ident = session_id.partition(":")
    if kind == "t":
        session = next((s for s in sessions.live() if s.id == ident), None)
        if session is None:
            return False
        session.stopped_by_admin = True
        block(session.user_id, session.item_id, session.device)
        return sessions.stop(ident)
    if kind == "d":
        with _lock:
            entry = next((e for e in _direct.values() if e.id == session_id), None)
            if entry is not None:
                del _direct[(entry.user_id, entry.item_id, entry.device)]
        if entry is None:
            return False
        block(entry.user_id, entry.item_id, entry.device)
        _record_direct(entry, ended=_clock(), by_admin=True)
        return True
    if kind == "r":
        return any(relay.stop_stream(ident, by_admin=True) for relay in relays)
    return False


# ---- history query --------------------------------------------------------------------------------------------------------

def history(db: Session, *, user_id: str | None, q: str | None, before: datetime | None, limit: int) -> dict[str, Any]:
    query = db.query(PlaybackHistory)
    if user_id:
        query = query.filter(PlaybackHistory.user_id == user_id)
    if q:
        needle = q.lower()
        query = query.filter(func.lower(PlaybackHistory.title).contains(needle, autoescape=True) | func.lower(func.coalesce(PlaybackHistory.subtitle, "")).contains(needle, autoescape=True))
    if before is not None:
        query = query.filter(PlaybackHistory.ended_at < before)
    # The cursor is ended_at alone; two sessions ending in the same microsecond could straddle a page.
    rows = query.order_by(PlaybackHistory.ended_at.desc(), PlaybackHistory.id.desc()).limit(limit + 1).all()
    page = rows[:limit]
    return {"items": [{
        "id": r.id, "source": r.source, "user": {"id": r.user_id, "name": r.user_name}, "title": r.title, "subtitle": r.subtitle,
        "item_id": r.item_id, "client": r.client, "method": r.method, "hardware": r.hardware, "video": r.video, "started_at": r.started_at,
        "ended_at": r.ended_at, "watched_seconds": r.watched_seconds, "stopped_by_admin": r.stopped_by_admin,
    } for r in page], "next_before": page[-1].ended_at if len(rows) > limit else None}
