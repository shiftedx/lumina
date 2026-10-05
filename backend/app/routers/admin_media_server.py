"""Admin Media server settings: Jellyfin apps, TMDB key (write-only), metadata language, transcoding.

Sibling actions (tmdb-test, transcode-diagnostics) register under URL in their own modules.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.media_schemas import MediaServerSettings, MediaServerSettingsUpdate
from app.models import AppSettings
from app.persistence import queue_after_commit, write_transaction
from app.security import get_admin_user
from app.services import categories, public_address
from app.services.yt_dlp_service import YtDlpService

URL = "/api/admin/media-server"


def serialize_media_server(record: AppSettings) -> MediaServerSettings:
    # A fresh database serves a transient AppSettings whose column defaults are unset, hence the fallbacks.
    public_url = (settings.app_public_url or "").strip().rstrip("/")
    return MediaServerSettings(
        jellyfin_enabled=bool(record.jellyfin_enabled),
        jellyfin_url=public_address.local_origin() or public_url or None,
        jellyfin_public_url=public_address.origin(),
        has_tmdb_key=bool(record.tmdb_api_key or settings.tmdb_api_key),
        metadata_language=record.metadata_language or "en-US",
        introdb_enabled=bool(record.introdb_enabled),
        hwaccel=record.hwaccel or "auto",
        max_playback_sessions=record.max_playback_sessions or 2,
        transcode_cache_gb=record.transcode_cache_gb or 10,
        jellyfin_import_url=record.jellyfin_import_url or None,
        anime_folders=list(record.anime_folders) if record.anime_folders is not None else ["Anime"],
        recategorising=categories.recategorising(),
        members_edit_metadata=bool(record.members_edit_metadata),
    )


def get_media_server_settings(db: Session = Depends(get_db, scope="function")) -> MediaServerSettings:
    return serialize_media_server(YtDlpService(db).get_app_settings())


def update_media_server_settings(payload: MediaServerSettingsUpdate, db: Session = Depends(get_db, scope="function")) -> MediaServerSettings:
    with write_transaction(db, name="media_server_settings_update"):
        record = YtDlpService(db).ensure_app_settings()
        folders = list(record.anime_folders) if record.anime_folders is not None else list(categories.DEFAULT_FOLDERS)
        for field, value in payload.model_dump(exclude_unset=True).items():
            if field == "tmdb_api_key" and value is not None:
                value = value.strip() or None  # "" removes the saved key; the env default applies again
            elif value is None:
                continue
            elif field == "jellyfin_import_url":
                value = value or None  # "" (already validated) turns importing off
            setattr(record, field, value)
        if payload.anime_folders is not None and not categories.same_folders(payload.anime_folders, folders):
            queue_after_commit(db, categories.recategorise)  # Re-sort once the new list is durable
        db.flush()
    return serialize_media_server(record)


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get(URL, response_model=MediaServerSettings, dependencies=admin)(get_media_server_settings)
    app.put(URL, response_model=MediaServerSettings, dependencies=admin)(update_media_server_settings)
