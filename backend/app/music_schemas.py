"""Music contracts: library files, recordings, releases and performances remain distinct."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MusicBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MusicQueueRequest(MusicBody):
    item_ids: list[str] = Field(default_factory=list, max_length=5000)
    title_id: str | None = None


class MusicTrack(BaseModel):
    id: str
    title: str
    artist: str
    album_artist: str
    album: str
    album_id: str | None = None
    artist_id: str | None = None
    number: int | None = None
    disc: int | None = None
    duration: float | None = None
    artwork_url: str | None = None
    audio_url: str
    codec: str | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    gain_db: float | None = None
    genres: list[str] = Field(default_factory=list)
    edited: bool = False
    peak: float | None = None
    album_gain_db: float | None = None
    album_peak: float | None = None
    gain_is_manual: bool = False


class MusicTrackEdit(MusicBody):
    item_ids: list[str] = Field(min_length=1, max_length=500)
    changes: dict[str, Any]
    base: dict[str, dict[str, Any]] = Field(default_factory=dict)


class MusicReleaseCreate(MusicBody):
    item_ids: list[str] = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=300)
    artist_id: str | None = None
    artist_name: str | None = Field(default=None, max_length=300)
    year: int | None = Field(default=None, ge=1870, le=2100)
    edition: str | None = Field(default=None, max_length=300)
    base: dict[str, dict[str, Any]] = Field(default_factory=dict)


class LyricsEdit(MusicBody):
    text: str | None = Field(default=None, max_length=100_000)


class CastRequest(MusicBody):
    speaker_id: str = Field(min_length=1, max_length=128)
    item_ids: list[str] = Field(min_length=1, max_length=100)
    compatibility: bool = False
    base_revision: int | None = Field(default=None, ge=0)
    current_index: int = Field(default=0, ge=0, le=5000)
    position_seconds: float = Field(default=0, ge=0, le=604800)


class CastControl(MusicBody):
    action: Literal["play", "pause", "stop", "next", "previous", "volume", "seek"]
    value: int | None = Field(default=None, ge=0, le=86_400)


class MusicIdentify(MusicBody):
    provider_id: str | None = None
    refresh: bool = False


class MusicWriteback(MusicBody):
    item_ids: list[str] = Field(min_length=1, max_length=100)
    include_artwork: bool = False


class MusicArtworkPlan(MusicBody):
    title_ids: list[str] = Field(min_length=1, max_length=500)
    base: dict[str, str | None]
    image_type: Literal["Primary", "Backdrop"] = "Primary"


class MusicPlanApply(MusicBody):
    plan_id: str = Field(min_length=64, max_length=64)


class MusicPlaylistRules(MusicBody):
    genres: list[str] = Field(default_factory=list, max_length=20)
    artist_id: str | None = None
    year_from: int | None = Field(default=None, ge=1000, le=2200)
    year_to: int | None = Field(default=None, ge=1000, le=2200)
    favorites: bool = False
    unplayed: bool = False


class MusicPlaylistSave(MusicBody):
    name: str = Field(min_length=1, max_length=120)
    shared: bool = False
    item_ids: list[str] = Field(default_factory=list, max_length=5000)
    rules: MusicPlaylistRules | None = None
    revision: int | None = Field(default=None, ge=0)


class MusicCue(BaseModel):
    start: float
    text: str


class MusicLyrics(BaseModel):
    plain: str | None = None
    cues: list[MusicCue] = Field(default_factory=list)
    source: str | None = None
    instrumental: bool = False


class MusicVideo(BaseModel):
    id: str
    title: str
    url: str
    thumbnail_url: str | None = None
    uploader: str | None = None
    duration: float | None = None
    performance: Literal["official", "live", "cover", "remix", "performance"]
    association: Literal["recording", "artist"] = "recording"
    item_id: str | None = None


class MusicVideos(BaseModel):
    items: list[MusicVideo] = Field(default_factory=list)
    refreshing: bool = False
    error: str | None = None


class MusicArtworkCredit(BaseModel):
    image_type: Literal["Primary", "Backdrop"]
    source_url: str
    author: str = ""
    license: str = ""


class MusicTitleInfo(BaseModel):
    release_type: str | None = None
    edition: str | None = None
    label: str | None = None
    catalogue_number: str | None = None
    country: str | None = None
    original_year: int | None = None
    origin: str | None = None
    active_since: str | None = None
    active_until: str | None = None
    styles: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    discography: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    candidates: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    identified: bool = False
    credits: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    release_group_id: str | None = None
    biography_url: str | None = None
    artwork_credits: list[MusicArtworkCredit] = Field(default_factory=list, max_length=3)
