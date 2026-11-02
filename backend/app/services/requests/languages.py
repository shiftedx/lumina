"""Stored, member-visible language facts; never runs a probe or reads files."""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.models import ArrServer, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, StorageRoot
from app.services.library import LibraryService
from app.services.transcripts import _LANGUAGE_ROWS, normalize_language, to_iso639_2

_SHORT = {row.split()[0]: row.split()[1] for row in _LANGUAGE_ROWS.strip().splitlines()}
LIMIT = 500


def code(value):  # noqa: ANN001, ANN201
    if value is None:
        return None
    value = normalize_language(value).lower().replace("_", "-")
    base, separator, region = value.partition("-")
    return _SHORT.get(to_iso639_2(base), base) + (separator + region if separator else "")


def preference(req):  # noqa: ANN001, ANN201
    if req.audio_language is not None or req.subtitle_language is not None:
        return code(req.audio_language), code(req.subtitle_language)
    return {"dub": ("en", None), "sub": ("ja", "en")}.get(req.language, (None, None))


def mode(req, db):  # noqa: ANN001, ANN201
    audio, subtitles = preference(req)
    if (audio, subtitles) == (None, None):
        return "automatic"
    if req.kind == "anime" and req.media_type == "tv" and req.language in ("dub", "sub") and (
        audio, subtitles
    ) == {"dub": ("en", None), "sub": ("ja", "en")}[req.language]:
        server = db.scalars(select(ArrServer).where(
            ArrServer.kind == "sonarr", ArrServer.enabled.is_(True),
        ).order_by(ArrServer.created_at)).first()
        profile = getattr(server, f"{req.language}_profile_id", None)
        return "automatic" if profile else "manual"
    return "manual"


def availability(db, user, title_id):  # noqa: ANN001, ANN201
    if not title_id:
        return {"state": "unknown", "files": [], "truncated": False, "total_files": 0}
    leaf, season = aliased(MediaTitle), aliased(MediaTitle)
    base = select(LibraryItem, MediaArtifact, leaf.index_number, season.index_number).join(
        leaf, leaf.id == LibraryItem.title_id,
    ).outerjoin(season, season.id == leaf.parent_id).outerjoin(
        LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id,
    ).outerjoin(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id).outerjoin(
        StorageRoot, StorageRoot.id == MediaArtifact.root_id,
    ).where(
        LibraryItem.status != "missing", LibraryItem.extra_type.is_(None), LibraryService.visible_predicate(user),
        or_(LibraryItemArtifact.artifact_id.is_(None), (MediaArtifact.lifecycle == "available") & StorageRoot.enabled.is_(True)),
        or_(leaf.id == title_id, season.parent_id == title_id),
    )
    rows = db.execute(base.order_by(LibraryItem.id).limit(LIMIT + 1)).all()
    total = db.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    files = []
    for item, artifact, episode_number, season_number in rows[:LIMIT]:
        probe = artifact.probe if artifact else None
        if isinstance(probe, dict) and artifact.size is not None and artifact.mtime_ns is not None and probe.get("fingerprint") != f"{artifact.size}:{artifact.mtime_ns}":
            probe = None
        streams = probe.get("streams") if isinstance(probe, dict) and not probe.get("error") else None
        streams = streams if isinstance(streams, list) else None
        audios, subtitles = set(), set()
        unknown_audio = unknown_subtitles = streams is None
        for stream in streams or []:
            if not isinstance(stream, dict) or stream.get("type") not in ("audio", "subtitle"):
                continue
            language = code(stream.get("language"))
            if not language or language == "und":
                if stream["type"] == "audio":
                    unknown_audio = True
                else:
                    unknown_subtitles = True
            else:
                (audios if stream["type"] == "audio" else subtitles).add(language)
        if item.metadata_json is not None and not isinstance(item.metadata_json, dict):
            unknown_subtitles = True
        metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
        sidecars = metadata.get("lumina_subtitles", [])
        if not isinstance(sidecars, list):
            unknown_subtitles = True
            sidecars = []
        for sidecar in sidecars:
            if isinstance(sidecar, dict) and (language := code(sidecar.get("language"))) and language != "und":
                subtitles.add(language)
            else:
                unknown_subtitles = True
        if not any(isinstance(stream, dict) and stream.get("type") == "audio" for stream in streams or []):
            unknown_audio = True
        files.append({"audio_languages": sorted(audios), "subtitle_languages": sorted(subtitles),
                      "audio_known": not unknown_audio, "subtitles_known": not unknown_subtitles,
                      "season": season_number if season_number is not None and item.kind == "episode" else None,
                      "episode": episode_number if item.kind == "episode" else None})
    known = sum(f["audio_known"] and f["subtitles_known"] for f in files)
    state = "known" if files and known == len(files) and total <= LIMIT else "partial" if known or any(f["audio_languages"] or f["subtitle_languages"] for f in files) else "unknown"
    return {"state": state, "files": files, "truncated": total > LIMIT, "total_files": total}


def matches(file, req):  # noqa: ANN001, ANN201
    audio, subtitles = preference(req)
    return (not audio or audio in file["audio_languages"]) and (not subtitles or subtitles in file["subtitle_languages"])


def english_coverage(db, user, title_id):  # noqa: ANN001, ANN201
    facts = availability(db, user, title_id)

    def coverage(kind, known):  # noqa: ANN001, ANN202
        files = facts["files"]
        if not files:
            return "unknown"
        present = sum("en" in file[kind] for file in files)
        if present == len(files) and not facts["truncated"]:
            return "available"
        if present:
            return "partial"
        if facts["truncated"] or any(not file[known] for file in files):
            return "unknown"
        return "missing"

    return {"audio": coverage("audio_languages", "audio_known"),
            "subtitles": coverage("subtitle_languages", "subtitles_known")}
