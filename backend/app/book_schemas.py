from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class BookProgress(BaseModel):
    unit_index: int = 0
    offset: float = 0
    percent: float = 0
    completed: bool = False
    updated_at: datetime | None = None


class BookFileResponse(BaseModel):
    id: str
    filename: str
    format: Literal["epub", "cbz", "cbr", "pdf"]
    chapter_count: int = 0
    page_count: int = 0
    available: bool


class BookResponse(BaseModel):
    id: str
    filename: str
    canonical_book_id: str
    reading_file_id: str
    files: list[BookFileResponse] = Field(default_factory=list)
    kind: Literal["ebook", "manga"]
    format: Literal["epub", "cbz", "cbr", "pdf"]
    title: str
    author: str = ""
    description: str = ""
    language: str = ""
    genres: list[str] = Field(default_factory=list)
    series_id: str | None = None
    series_name: str | None = None
    volume_number: float | None = None
    reading_direction: Literal["ltr", "rtl"] = "ltr"
    cover_url: str | None = None
    chapter_count: int = 0
    page_count: int = 0
    created_at: datetime
    visibility: Literal["private", "shared"]
    can_edit: bool
    progress: BookProgress
    favorite: bool = False
    reading_list: bool = False
    shelf_ids: list[str] = Field(default_factory=list)
    available: bool


class BookSeriesResponse(BaseModel):
    id: str
    title: str
    author: str = ""
    description: str = ""
    cover_url: str | None = None
    volume_count: int
    read_volume_count: int
    next_book_id: str | None = None


class BookShelfResponse(BaseModel):
    id: str
    name: str
    description: str = ""
    visibility: Literal["private", "shared"]
    can_edit: bool
    book_ids: list[str] = Field(default_factory=list)
    count: int = 0
    covers: list[str] = Field(default_factory=list)


class BooksPage(BaseModel):
    items: list[BookResponse]
    total: int
    next_cursor: str | None = None


class SeriesPage(BaseModel):
    items: list[BookSeriesResponse]
    total: int
    next_cursor: str | None = None


class BookOverview(BaseModel):
    totals: dict[str, int]
    continue_reading: list[BookResponse]
    recently_added: list[BookResponse]
    shelves: list[BookShelfResponse]
    discover: list[BookResponse]


class BookStatePatch(BaseModel):
    favorite: bool | None = None
    reading_list: bool | None = None
    completed: bool | None = None
    unit_index: int | None = Field(default=None, ge=0)
    offset: float | None = Field(default=None, ge=0, le=1)


class BookPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    filename: str | None = Field(default=None, max_length=255)
    series_name: str | None = Field(default=None, max_length=500)
    volume_number: float | None = Field(default=None, ge=0, le=100000)
    reading_direction: Literal["ltr", "rtl"] | None = None
    visibility: Literal["private", "shared"] | None = None
    genres: list[str] | None = Field(default=None, max_length=30)


class ShelfCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["private", "shared"] = "private"


class ShelfPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["private", "shared"] | None = None


class ShelfEntries(BaseModel):
    book_ids: list[str] = Field(max_length=500)


class BookCombine(BaseModel):
    primary_book_id: str
    book_ids: list[str] = Field(min_length=2, max_length=100)


class BundleMatchesResponse(BaseModel):
    combined: int
    remaining: int
