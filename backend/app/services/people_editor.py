"""Household people editor (#164): rename a person and set their photo across every title that credits them.

One ``person_overrides`` row per edited person, laid over the titles' people refs at read time (people_for_titles, the
FTS people text, cast_photos), so scans and TMDB refreshes never undo it. Each change is a ``title_edits`` row with
title_id = person id and field ``person.name`` / ``person.photo``, so the shared undo restores it.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.models import MediaTitle, NfoPerson, Person, PersonOverride, User
from app.persistence import write_transaction
from app.services import cast_photos, library_search
from app.services.library import LibraryService
from app.services.media_titles import parse_item_id
from app.services.metadata_editor import Batch, FieldError
from app.services.title_metadata import person_visible

FIELDS = {"person.name": "name", "person.photo": "photo"}


class PersonNotFound(Exception): ...


def _crediting(db: Session, person_id: str, user: User | None = None) -> list[MediaTitle]:
    """Titles whose people refs carry ``person_id`` (visible to ``user`` when given)."""
    refs = func.json_each(MediaTitle.metadata_json, "$.people").table_valued("value")
    query = select(MediaTitle).join(refs, true()).where(func.json_extract(refs.c.value, "$.person_id") == person_id)
    if user is not None:
        query = query.where(LibraryService.visible_title_predicate(user))
    return list(dict.fromkeys(db.scalars(query)))


def visible_person(db: Session, user: User, person_id: str) -> str:
    """The parsed id of a person some title ``user`` can see credits; else PersonNotFound (no enumeration)."""
    parsed = parse_item_id(person_id)
    if parsed is None or not person_visible(db, parsed, user):
        raise PersonNotFound
    return parsed


def source_name(db: Session, person_id: str) -> str:
    """What TMDB or the NFO files call the person; else the first credit's name."""
    for model in (Person, NfoPerson):
        if (row := db.get(model, person_id)) is not None:
            return row.name
    refs = func.json_each(MediaTitle.metadata_json, "$.people").table_valued("value")
    return db.scalar(select(func.json_extract(refs.c.value, "$.name")).select_from(MediaTitle).join(refs, true())
                     .where(func.json_extract(refs.c.value, "$.person_id") == person_id).limit(1)) or ""


def person_doc(db: Session, user: User, person_id: str) -> dict[str, Any]:
    override = db.get(PersonOverride, person_id)
    name = source_name(db, person_id)
    return {
        "person_id": person_id, "name": (override.name if override and override.name else name), "source_name": name,
        "name_edited": bool(override and override.name), "photo_edited": bool(override and override.photo),
        "image_url": cast_photos.image_urls(db, {person_id}).get(person_id),
        "title_count": len(_crediting(db, person_id, user)),
    }


def _value(override: PersonOverride | None, field: str) -> Any:
    return getattr(override, FIELDS[field]) if override is not None else None


def set_value(db: Session, override_id: str, field: str, value: Any) -> PersonOverride | None:
    """Write one override column; a row left empty is deleted. Returns the row (None once deleted)."""
    row = db.get(PersonOverride, override_id) or PersonOverride(id=override_id)
    setattr(row, FIELDS[field], value)
    if row.name is None and row.photo is None:
        if row in db:
            db.delete(row)
        return None
    db.add(row)
    return row


def reindex(db: Session, person_id: str) -> None:
    """Search finds the new name: re-index every title that credits the person (episodes alone, not their series)."""
    db.flush()
    for title in _crediting(db, person_id):
        library_search.index_title(db, title)


def edit(db: Session, user: User, person_id: str, field: str, value: Any) -> str | None:
    """One history batch for one person field; None clears (back to the source). Returns the batch id, or None if unchanged."""
    if field == "person.name" and value is not None:
        if not isinstance(value, str) or not 1 <= len(value := value.strip()) <= 200:
            raise FieldError("bad_entry", field="name")
    with write_transaction(db, name="person_edit"):
        before = _value(db.get(PersonOverride, person_id), field)
        if before == value:
            return None
        batch = Batch(db, "person", user.id)
        set_value(db, person_id, field, value)
        batch.record(SimpleNamespace(id=person_id), field, before, "user" if before is not None else None, value, "user" if value is not None else None)
        if field == "person.name":
            reindex(db, person_id)
        return batch.id


def undo_row(db: Session, user: User, row: Any) -> str | None:
    """Undo one person.* history row inside the caller's transaction: None when restored, else the skip reason."""
    if not person_visible(db, row.title_id, user):
        return "not_visible"
    if _value(db.get(PersonOverride, row.title_id), row.field) != row.after:
        return "changed_since"
    set_value(db, row.title_id, row.field, row.before)
    if row.field == "person.name":
        reindex(db, row.title_id)
    return None
