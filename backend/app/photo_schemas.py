"""Public request and response shapes for Photos."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Visibility = Literal["private", "shared"]
PhotoSourceState = Literal["queued", "scanning", "idle", "canceling", "offline", "failed"]


class PhotoResponse(BaseModel):
    id: str
    title: str | None
    filename: str
    content_type: str
    width: int
    height: int
    captured_at: datetime | None
    added_at: datetime
    camera: str | None
    camera_make: str | None
    camera_model: str | None
    bytes: int
    favorite: bool
    can_edit: bool
    visibility: Visibility
    thumbnail_url: str
    preview_url: str
    original_url: str | None
    deleted_at: datetime | None
    album_ids: list[str] = Field(default_factory=list)
    person_ids: list[str] = Field(default_factory=list)


class PhotosPage(BaseModel):
    items: list[PhotoResponse]
    total: int
    next_cursor: str | None


class PhotoPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=500)
    visibility: Visibility | None = None
    favorite: bool | None = None
    deleted: bool | None = None


class PhotoAlbumCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Visibility = "private"


class PhotoAlbumPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Visibility | None = None
    deleted: bool | None = None


class PhotoAlbumResponse(BaseModel):
    id: str
    name: str
    description: str | None
    visibility: Visibility
    can_edit: bool
    photo_count: int
    cover_url: str | None
    created_at: datetime
    deleted_at: datetime | None


class PhotoAlbumsPage(BaseModel):
    items: list[PhotoAlbumResponse]


class PhotoSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_id: str = Field(min_length=1, max_length=36)
    relative_folder: str = Field(default="", max_length=4096)
    visibility: Visibility = "private"


class PhotoSourceResponse(BaseModel):
    id: str
    root_id: str
    root_label: str
    relative_folder: str
    owner_user_id: str
    visibility: Visibility
    state: PhotoSourceState
    enabled: bool
    indexed_count: int
    error_code: str | None
    error_count: int
    last_scan_at: datetime | None
    last_completed_at: datetime | None


class PhotoSourceRoot(BaseModel):
    id: str
    label: str
    online: bool


class PhotoSourcesPage(BaseModel):
    items: list[PhotoSourceResponse]
    roots: list[PhotoSourceRoot]
