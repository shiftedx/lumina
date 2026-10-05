"""Just-ahead AI work: summaries and the next recap, made when a member finishes something.

There is never a library-wide backfill and never a transcript made for this. PlaybackProgressService.update queues
``on_completed`` after the commit that flips a titled Library item to completed; web and Jellyfin clients both go
through it. Each chain runs on its own daemon thread and requests one summary at a time through the summaries
JobRegistry (starting only below MAX_ACTIVE jobs, so member-requested work keeps room), then the member's next
recap, and none of it starts while a video transcode runs. It also keeps one of the assistant's concurrent inference slots (ai_max_concurrency) free of background
jobs, so a member's own request never queues behind it. At most one chain runs per (member, series or movie). SummaryBusyError drops the chain: the next completion
retries it.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import LibraryItem, MediaTitle, Summary, Transcript, User
from app.services import recaps, summaries
from app.services.ai_extras import EPISODE_SUMMARIES, KEY_SCENES, switched_off
from app.services.library import LibraryService
from app.services.local_playback_sessions import sessions
from app.services.local_ai import effective_config
from app.services.titles import TitleService
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)

MAX_ACTIVE = 8  # just-ahead work starts only below this many summary/recap jobs (the registry allows 16)
EARLIER_EPISODES = 8
POLL_SECONDS = 1.0
JOB_WAIT_SECONDS = 30 * 60  # stop waiting on one summary after this and move on

_claims: set[tuple[str, str]] = set()
_claims_lock = threading.Lock()


@dataclass(frozen=True)
class Chain:
    user_id: str
    key: str  # the series id (episodes) or the movie id: one chain per (member, key)
    items: tuple[str, ...]  # Library items to summarize, in this order
    recap_series_id: str | None  # request the member's next recap of this series afterwards
    model_id: str


def plan_chain(db: Session, user: User, item_id: str) -> Chain | None:
    """What finishing ``item_id`` should prepare, or None (assistant not set up, switched off, not a movie or episode)."""
    config = effective_config(YtDlpService(db).get_app_settings())
    item = LibraryService(db).get_item(item_id, user)
    title = db.get(MediaTitle, item.title_id) if item is not None and item.title_id else None
    if not config.enabled or title is None:
        return None
    if title.type == "movie":
        return Chain(user.id, title.id, (item.id,), None, config.ai_model) if not switched_off(db, KEY_SCENES) else None
    if title.type != "episode" or (switched_off(db, recaps.FEATURE_KEY) and switched_off(db, EPISODE_SUMMARIES)):
        return None
    season = db.get(MediaTitle, title.parent_id) if title.parent_id else None
    if season is None or season.parent_id is None:
        return None
    earlier = (
        _earlier_items(db, user, season.parent_id, season.index_number, title.index_number)
        if season.index_number and title.index_number is not None else ()
    )
    recap = season.parent_id if not switched_off(db, recaps.FEATURE_KEY) else None
    return Chain(user.id, season.parent_id, (item.id, *earlier), recap, config.ai_model)


def _earlier_items(db: Session, user: User, series_id: str, season_no: int, episode_no: int) -> tuple[str, ...]:
    """One visible version with a transcript for each of up to 8 earlier regular episodes with no summary, nearest first."""
    prior = [title.id for title, _number in recaps._prior_episodes(db, user, series_id, season_no, episode_no)]  # noqa: SLF001 - the recap's own spoiler-safe window
    if not prior:
        return ()
    summarized = set(db.scalars(
        select(LibraryItem.title_id).join(Summary, Summary.library_item_id == LibraryItem.id)
        .where(LibraryItem.title_id.in_(prior), Summary.state == "succeeded")
    ))
    first: dict[str, str] = {}
    rows = db.execute(
        select(LibraryItem.title_id, LibraryItem.id).join(Transcript, Transcript.library_item_id == LibraryItem.id)
        .where(LibraryItem.title_id.in_(prior), LibraryItem.extra_type.is_(None), LibraryItem.status != "missing",
               LibraryService.visible_predicate(user))
        .order_by(LibraryItem.created_at, LibraryItem.id)
    )
    for title_id, item_id in rows:
        first.setdefault(title_id, item_id)
    return tuple(first[title_id] for title_id in prior if title_id in first and title_id not in summarized)[:EARLIER_EPISODES]


def on_completed(user_id: str, item_id: str) -> None:
    """After-commit hook (PlaybackProgressService.update): run the chain off the request thread."""
    threading.Thread(target=run, args=(user_id, item_id), daemon=True, name="just-ahead").start()


def run(user_id: str, item_id: str) -> None:
    try:
        with session_scope() as db:
            user = db.get(User, user_id)
            chain = plan_chain(db, user, item_id) if user is not None else None
        if chain is None or not _claim(chain):
            return
        try:
            _work(chain)
        finally:
            _release(chain)
    except Exception as exc:  # noqa: BLE001 - background work never surfaces; log the type only, never content
        logger.warning("Just-ahead chain failed: %s", type(exc).__name__)


def _claim(chain: Chain) -> bool:
    with _claims_lock:
        key = (chain.user_id, chain.key)
        if key in _claims:
            return False
        _claims.add(key)
        return True


def _release(chain: Chain) -> None:
    with _claims_lock:
        _claims.discard((chain.user_id, chain.key))


def _room() -> bool:
    """No video transcode running, below MAX_ACTIVE registry jobs and below the assistant's concurrency less one."""
    if sessions.video_encodes() > 0:  # the CPU belongs to playback
        return False
    with session_scope() as db:
        slots = effective_config(YtDlpService(db).get_app_settings()).ai_max_concurrency
    return len(summaries.jobs.running_ids()) < min(MAX_ACTIVE, slots - 1)


def _wait(job_id: str) -> None:
    deadline = time.monotonic() + JOB_WAIT_SECONDS
    while summaries.jobs.is_active(job_id) and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)


def _work(chain: Chain) -> None:
    """Summaries one at a time (each waited for), then the member's next recap. A fresh session per step: none is held while waiting."""
    for item_id in chain.items:
        if not _room():
            return
        with session_scope() as db:
            transcript = db.scalars(
                select(Transcript).where(Transcript.library_item_id == item_id)
                .order_by(Transcript.created_at.desc(), Transcript.revision.desc()).limit(1)
            ).first()
            if transcript is None:
                continue  # never transcribe for this
            try:
                summary, _created = summaries.request_summary(db, transcript, chain.model_id, chain.user_id)
            except summaries.SummaryBusyError:
                return
            job_id = summary.id
        _wait(job_id)
    if chain.recap_series_id is None or not _room():
        return
    with session_scope() as db:
        user, series = db.get(User, chain.user_id), db.get(MediaTitle, chain.recap_series_id)
        target = TitleService(db).play_next(user, series) if user is not None and series is not None else None
        context = recaps.load_context(db, user, target) if target else None
        if context is not None and context.inputs:
            try:
                recaps.request_recap(db, user, context, chain.model_id)
            except summaries.SummaryBusyError:
                return
