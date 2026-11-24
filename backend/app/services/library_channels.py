"""Channel identity and query expressions for saved remote videos."""
from __future__ import annotations

from sqlalchemy import Integer, cast, func

from app.models import LibraryItem
from app.services.media_titles import synthetic_id


CHANNEL_KINDS = ("video", "recording")
# Managed videos with no Media title: yt-dlp downloads and live recordings.
CHANNEL_ITEM = (
    LibraryItem.title_id.is_(None), LibraryItem.extractor.is_not(None),
    LibraryItem.kind.in_(CHANNEL_KINDS), LibraryItem.status != "missing",
)
UPLOADER = func.coalesce(LibraryItem.uploader, "")
UPLOAD_DAY = func.coalesce(func.json_extract(LibraryItem.metadata_summary, "$.upload_date"), func.strftime("%Y%m%d", LibraryItem.created_at))
UPLOAD_YEAR = cast(func.substr(UPLOAD_DAY, 1, 4), Integer)


def channel_id(extractor: str, uploader: str, year: int | None = None) -> str:
    key = f"channel:{extractor}:{uploader}"
    return synthetic_id(key if year is None else f"{key}:{year}")


def channel_year(item: LibraryItem) -> int:
    day = str((item.metadata_summary or {}).get("upload_date") or "")
    return int(day[:4]) if day[:4].isdigit() else item.created_at.year
