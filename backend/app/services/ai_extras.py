"""Gallery AI extras: watched-episode summaries and key scenes, read from stored rows only.

Neither function calls a model, so both answer within the page budget. The spoiler rules are enforced here, never
in the client: summary text only for episodes the requester completed; key scenes only from a movie or a regular
episode the requester completed. Summary and transcript text is untrusted (ADR 0013) and leaves as plain strings
for the client to render as text. A switched-off extra answers ``available: false``.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.media_schemas import EpisodeSummaries, EpisodeSummaryText, KeyScene, KeyScenes
from app.models import MediaTitle, Summary, TranscriptCue, User
from app.services.titles import TitleService, resume_anchor
from app.services.yt_dlp_service import YtDlpService

EPISODE_SUMMARIES = "episode_summaries"  # AppSettings.ai_features_disabled kill switches (ADR 0013)
KEY_SCENES = "key_scenes"
MAX_OVERVIEW = 600
MAX_QUOTE = 160
MIN_QUOTE = 12
MAX_CAPTION = 200
MAX_SCENES = 3
_TAG = re.compile(r"<[^>]+>")


def switched_off(db: Session, feature: str) -> bool:
    return feature in (YtDlpService(db).get_app_settings().ai_features_disabled or [])


def clip(text: str, limit: int) -> str:
    """Whitespace collapsed, then cut at a word boundary to at most ``limit`` characters ending in "…"."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip() + "…"


def episode_summaries(db: Session, user: User, series: MediaTitle, season: int) -> EpisodeSummaries:
    """The newest succeeded summary overview of each episode in ``season`` the requester completed."""
    if switched_off(db, EPISODE_SUMMARIES):
        return EpisodeSummaries(available=False)
    service = TitleService(db)
    episodes = service.episodes(user, series, season)
    batch = service.load(user, episodes)
    finished = {  # visible version -> its episode, for episodes whose latest progress (any visible version) is completed
        item.id: episode.id
        for episode in episodes
        if (progress := batch.latest_progress(episode.id)) is not None and progress.completed
        for item in batch.versions.get(episode.id, [])
    }
    newest: dict[str, str] = {}
    if finished:
        rows = db.execute(
            select(Summary.library_item_id, Summary.overview)
            .where(Summary.library_item_id.in_(list(finished)), Summary.state == "succeeded", Summary.overview.is_not(None))
            .order_by(Summary.completed_at.desc(), Summary.id)
        )
        for item_id, overview in rows:
            if overview.strip():
                newest.setdefault(finished[item_id], overview)
    return EpisodeSummaries(available=True, items=[
        EpisodeSummaryText(episode_id=episode.id, overview=clip(newest[episode.id], MAX_OVERVIEW))
        for episode in episodes if episode.id in newest
    ])


def key_scenes(db: Session, user: User, title: MediaTitle) -> KeyScenes:
    """Up to three quoted moments of the requester's completed movie, or of a series' last completed regular episode."""
    if title.type not in ("movie", "series") or switched_off(db, KEY_SCENES):
        return KeyScenes(available=False)
    service = TitleService(db)
    source = title if title.type == "movie" else _last_completed_episode(service, user, title)
    if source is None:
        return KeyScenes(available=False)
    batch = service.load(user, [source])
    progress = batch.latest_progress(source.id)
    items = [item.id for item in batch.versions.get(source.id, [])]
    if progress is None or not progress.completed or not items:
        return KeyScenes(available=False)
    summary = db.scalars(
        select(Summary).where(Summary.library_item_id.in_(items), Summary.state == "succeeded")
        .order_by(Summary.completed_at.desc(), Summary.id).limit(1)
    ).first()
    scenes = _scenes(db, summary) if summary is not None else []
    if not scenes:
        return KeyScenes(available=False)
    return KeyScenes(available=True, title_id=source.id, item_id=summary.library_item_id, scenes=scenes)


def _last_completed_episode(service: TitleService, user: User, series: MediaTitle) -> MediaTitle | None:
    episodes = service.series_progress(user, [series.id]).get(series.id, [])
    anchor = resume_anchor([episode for episode in episodes if episode.completed])  # regular seasons only
    return service.get_visible(anchor.title_id, user) if anchor is not None else None


def _first_cue(point: object) -> int | None:
    ordinals = point.get("cue_ordinals") if isinstance(point, dict) else None
    return ordinals[0] if isinstance(ordinals, list) and ordinals and type(ordinals[0]) is int else None


def _scenes(db: Session, summary: Summary) -> list[KeyScene]:
    """Stored key points in order, each quoting its first cited cue: tags stripped, short quotes skipped, at most three."""
    points = [point for point in summary.key_points or [] if _first_cue(point) is not None and isinstance(point.get("text"), str)]
    if not points:
        return []
    cues = {
        cue.ordinal: cue for cue in db.scalars(select(TranscriptCue).where(
            TranscriptCue.transcript_id == summary.transcript_id, TranscriptCue.ordinal.in_({_first_cue(point) for point in points}),
        ))
    }
    scenes: list[KeyScene] = []
    for point in points:
        cue = cues.get(_first_cue(point))
        caption = clip(point["text"], MAX_CAPTION)
        quote = clip(_TAG.sub("", cue.text), MAX_QUOTE) if cue is not None else ""
        if len(quote) < MIN_QUOTE or not caption:
            continue
        scenes.append(KeyScene(start_ms=cue.start_ms, quote=quote, caption=caption))
        if len(scenes) == MAX_SCENES:
            break
    return scenes
