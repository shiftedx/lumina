"""Edit history, exact undo and retention. Leaf of ``metadata_editor``."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.models import MediaTitle, TitleEdit, User, utcnow
from app.persistence import write_transaction
from app.services.metadata_editor import Batch, load_titles, read_field, set_item_lock
from app.services.library import LibraryService
from app.services.media_titles import TITLE_COLUMNS

CUT, KEEP_PER_TITLE, KEEP_DAYS = 300, 200, 365


class BatchNotFound(Exception): ...


class AlreadyUndone(Exception): ...


def _cut(value: Any) -> Any:
    if isinstance(value, str):
        return value[:CUT]
    if value is not None and not isinstance(value, (bool, int, float)) and len(text_ := json.dumps(value, default=str)) > CUT:
        return text_[:CUT]
    return value


def history(db: Session, title: MediaTitle, cursor: int | str | None, limit: int = 20, user: User | None = None) -> dict[str, Any]:
    last = func.max(TitleEdit.id).label("last")
    query = select(TitleEdit.batch_id, last).where(TitleEdit.title_id == title.id).group_by(TitleEdit.batch_id)
    if cursor is not None:
        query = query.having(last < int(cursor))
    found = db.execute(query.order_by(last.desc()).limit(limit + 1)).all()
    page = found[:limit]
    ids = [b for b, _ in page]
    rows = db.scalars(select(TitleEdit).where(TitleEdit.title_id == title.id, TitleEdit.batch_id.in_(ids)).order_by(TitleEdit.id)).all()
    count_query = select(TitleEdit.batch_id, func.count(func.distinct(TitleEdit.title_id))).where(TitleEdit.batch_id.in_(ids))
    if user is not None:  # a batch's count never includes titles the caller cannot see
        count_query = count_query.join(MediaTitle, MediaTitle.id == TitleEdit.title_id).where(LibraryService.visible_title_predicate(user))
    counts = dict(db.execute(count_query.group_by(TitleEdit.batch_id)).all())
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows if r.user_id})))}
    by_batch: dict[str, list[TitleEdit]] = {}
    for row in rows:
        by_batch.setdefault(row.batch_id, []).append(row)
    batches = []
    for batch_id in ids:
        group = by_batch[batch_id]
        user = users.get(group[0].user_id or "")
        batches.append({
            "batch_id": batch_id, "kind": group[0].kind, "created_at": group[-1].created_at, "undone": all(r.undone_by for r in group),
            "title_count": counts[batch_id],
            "user": {"id": user.id, "display_name": user.display_name} if user is not None and user.is_active else None,
            "changes": [{"field": r.field, "before": _cut(r.before), "after": _cut(r.after),
                         "before_source": r.before_source, "after_source": r.after_source} for r in group],
        })
    return {"batches": batches, "next_cursor": str(page[-1][1]) if len(found) > limit else None}


def _write_raw(title: MediaTitle, field: str, value: Any, source: str | None) -> None:
    """Put back an exact (value, source) state, bypassing ranks and locks; no source means the field was empty."""
    sources = dict(title.field_sources or {})
    if source is None:
        sources.pop(field, None)
    else:
        sources[field] = source
    title.field_sources = sources
    if field.startswith("images."):
        images = dict(title.images or {})
        kind = field.removeprefix("images.")
        images.pop(kind, None) if value is None and source is None else images.__setitem__(kind, value)
        title.images = images
    elif field == "added_at":
        title.added_at = datetime.fromisoformat(value) if value else None
    elif field in TITLE_COLUMNS:
        setattr(title, field, {} if field == "provider_ids" and value is None else value)
    else:
        meta = dict(title.metadata_json or {})
        meta.pop(field, None) if value is None and source is None else meta.__setitem__(field, value)
        title.metadata_json = meta


def undo(db: Session, user: User, batch_id: str, *, owner: bool) -> dict[str, Any]:
    rows = list(db.scalars(select(TitleEdit).where(TitleEdit.batch_id == batch_id).order_by(TitleEdit.id.desc())))
    if not rows:
        raise BatchNotFound
    if all(r.undone_by for r in rows):
        raise AlreadyUndone
    restored, skipped, now = 0, [], utcnow()
    hidden: dict[str, str] = {}  # opaque per-response names: a hidden title's real id is never returned
    with write_transaction(db, name="metadata_undo"):
        titles = load_titles(db, user, {r.title_id for r in rows if not r.field.startswith("person.")})
        if not titles and not any(r.field.startswith("person.") for r in rows):
            raise BatchNotFound
        batch = Batch(db, "undo", user.id)
        for row in rows:
            if row.undone_by:
                continue  # a member's earlier undo already restored it
            if row.field.startswith("person."):  # #164 people editor: the row's title_id is the person id
                from app.services import people_editor  # local: people_editor imports the editor this module imports

                if reason := people_editor.undo_row(db, user, row):
                    skipped.append({"title_id": row.title_id, "field": row.field, "reason": reason})
                    continue
                batch.record(SimpleNamespace(id=row.title_id), row.field, row.after, row.after_source, row.before, row.before_source)
                restored += 1
                continue
            title = titles.get(row.title_id)
            if title is None:
                skipped.append({"title_id": hidden.setdefault(row.title_id, f"hidden-{len(hidden) + 1}"), "field": row.field, "reason": "not_visible"})
                continue
            if row.field == "locked":
                if bool(title.locked) != row.after:
                    skipped.append({"title_id": title.id, "field": row.field, "reason": "changed_since"})
                    continue
                set_item_lock(title, bool(row.before), batch, now)
                restored += 1
                continue
            if row.field == "provider_ids" and not owner and (row.before or {}).get("Tmdb") != (row.after or {}).get("Tmdb"):
                skipped.append({"title_id": title.id, "field": row.field, "reason": "owner_only"})
                continue
            if read_field(title, row.field) != (row.after, row.after_source):
                skipped.append({"title_id": title.id, "field": row.field, "reason": "changed_since"})
                continue
            _write_raw(title, row.field, row.before, row.before_source)
            kept = dict(title.source_values or {})
            if row.before_source == "user" and row.after_source != "user":
                kept[row.field] = {"source": row.after_source, "value": row.after}  # undoing a revert re-creates the kept value
            elif row.before_source != "user":
                kept.pop(row.field, None)
            title.source_values = kept
            if row.field == "provider_ids" and (row.before or {}).get("Tmdb") != (row.after or {}).get("Tmdb"):
                title.metadata_due_at = now
            batch.record(title, row.field, row.after, row.after_source, row.before, row.before_source)
            batch.touched(title, row.field)
            restored += 1
        open_rows = {(s["title_id"], s["field"]) for s in skipped if s["reason"] != "changed_since"}
        for row in rows:  # consumed unless the caller could not act on it: not_visible / owner_only stay open for the owner
            if row.undone_by is None and (row.title_id, row.field) not in open_rows:
                row.undone_by = batch.id
        batch.finish(db)
    return {"batch_id": batch.id, "restored": restored, "skipped": skipped}


_last = 0.0


def _prune(db: Session, now: datetime) -> None:
    db.execute(delete(TitleEdit).where(TitleEdit.created_at < now - timedelta(days=KEEP_DAYS)))
    # The window scan reads every history row; fine to 100k rows, index a per-title counter if history grows past that.
    db.execute(text("DELETE FROM title_edits WHERE id IN (SELECT id FROM (SELECT id, row_number() OVER "
                    "(PARTITION BY title_id ORDER BY id DESC) AS rn FROM title_edits) WHERE rn > :keep)"), {"keep": KEEP_PER_TITLE})
    try:
        from app.services import title_images
    except ImportError:  # pragma: no cover
        return
    title_images.gc_uploads(db, now)


def maintenance(db: Session, now: datetime | None = None, *, force: bool = False) -> None:
    """History retention and upload GC, at most hourly (the maintenance cycle runs every minute)."""
    global _last
    if not force and time.monotonic() - _last < 3600:
        return
    _last = time.monotonic()
    with write_transaction(db, name="metadata_maintenance"):
        _prune(db, now or utcnow())
