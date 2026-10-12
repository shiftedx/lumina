"""Bulk metadata edits, the vocabulary behind their pickers and people suggestions."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.models import MediaTitle, User, utcnow
from app.persistence import write_transaction
from app.services import cast_photos
from app.services.library import LibraryService
from app.services.metadata_editor import (
    CATALOGUE, EDITABLE_TYPES, Batch, FieldError, load_titles, pin_field, read_field, revert_field, set_item_lock, validate,
    write_user_field,
)

LIST_FIELDS = ("genres", "tags", "studios")
SET_FIELDS = ("official_rating", "custom_rating", "status")
FIELD_OPS = ("add", "remove", "set", "lock", "unlock")
OPS = (*FIELD_OPS, "lock_item", "unlock_item")


def _check(ops: list[dict[str, Any]]) -> None:
    """Shape errors up front so nothing is applied."""
    for op in ops:
        name, field = op.get("op"), op.get("field")
        if name not in OPS:
            raise FieldError("unknown_op")
        if name in ("add", "remove") and (field not in LIST_FIELDS or not op.get("values")):
            raise FieldError("invalid_op", field=field or "")
        if name == "set" and field not in SET_FIELDS:
            raise FieldError("invalid_op", field=field or "")
        if name in ("lock", "unlock") and (not op.get("fields") or any(f not in CATALOGUE for f in op["fields"])):
            raise FieldError("unknown_field")


def _op_fields(op: dict[str, Any]) -> list[str]:
    return list(op.get("fields") or []) if op["op"] in ("lock", "unlock") else [op["field"]] if op["op"] in ("add", "remove", "set") else []


def _next(title: MediaTitle, op: dict[str, Any], current: Any) -> list[Any] | Any:
    """The value an add/remove/set leaves in place over ``current``, validated; ``current`` when nothing changes."""
    field = op["field"]
    if op["op"] == "set":
        return validate(title.type, field, op.get("value"))
    have = list(current or [])
    if op["op"] == "add":
        seen = {v.casefold() for v in have}
        for value in op["values"]:
            if value.casefold() not in seen:
                seen.add(value.casefold())
                have.append(value)
    else:
        drop = {v.casefold() for v in op["values"]}
        have = [v for v in have if v.casefold() not in drop]
    return validate(title.type, field, have) if have != list(current or []) else current


def bulk_edit(db: Session, user: User, title_ids: list[str], ops: list[dict[str, Any]], *, owner: bool) -> dict[str, Any]:
    """One batch for every title: {"batch_id", "applied", "skipped": [{"title_id", "reason"}]}."""
    ids = list(dict.fromkeys(title_ids))
    if len(ids) > 500:
        raise FieldError("too_many")
    _check(ops)
    titles = load_titles(db, user, ids)
    skipped, todo = [], []
    for title_id in ids:
        title = titles.get(title_id)
        if title is None:
            skipped.append({"title_id": title_id, "reason": "not_found"})
            continue
        locked, reason, sim = bool(title.locked), None, {}  # sim: values earlier ops in this request leave behind
        for op in ops:
            fields = _op_fields(op)
            if any(f not in CATALOGUE or title.type not in CATALOGUE[f].types for f in fields):
                reason = "wrong_type"
                break
            if op["op"] in FIELD_OPS and locked:
                reason = "locked_item"
                break
            if op["op"] in ("lock_item", "unlock_item"):
                locked = op["op"] == "lock_item"
            elif op["op"] in ("add", "remove", "set"):
                field = op["field"]  # validation (and the list caps) over the earlier ops' result, before any write
                try:
                    sim[field] = _next(title, op, sim[field] if field in sim else read_field(title, field)[0])
                except FieldError as error:
                    raise FieldError(error.reason, field=field, title_id=title_id) from error
        if reason:
            skipped.append({"title_id": title_id, "reason": reason})
        else:
            todo.append(title_id)
    batch_id, applied, now = str(uuid.uuid4()), 0, utcnow()
    for start in range(0, len(todo), 100):
        with write_transaction(db, name="metadata_bulk"):
            fresh = load_titles(db, user, todo[start:start + 100])
            batch = Batch(db, "bulk", user.id, id=batch_id)
            for title in fresh.values():
                changed = False
                for op in ops:
                    name = op["op"]
                    if name in ("add", "remove", "set"):
                        current = read_field(title, op["field"])[0]
                        value = _next(title, op, current)
                        changed |= write_user_field(title, op["field"], value, batch) if value != current else False
                    elif name == "lock":
                        changed |= any([pin_field(title, f, batch) for f in op["fields"]])
                    elif name == "unlock":
                        changed |= any([revert_field(title, f, batch, now, owner=owner) for f in op["fields"]])
                    else:
                        changed |= set_item_lock(title, name == "lock_item", batch, now)
                applied += changed
            batch.finish(db)
    return {"batch_id": batch_id if applied else None, "applied": applied, "skipped": skipped}


def vocabulary(db: Session, user: User, field: str) -> list[dict[str, Any]]:
    """Distinct values over titles the caller can see, most used first (<= 200)."""
    if field not in (*LIST_FIELDS, "official_rating"):
        raise FieldError("unknown_field")
    visible = (MediaTitle.type.in_(EDITABLE_TYPES), LibraryService.visible_title_predicate(user))
    if field == "official_rating":
        value = func.json_extract(MediaTitle.metadata_json, "$.official_rating")
        query = select(value, func.count()).where(*visible, value.is_not(None)).group_by(value)
    else:
        refs = func.json_each(MediaTitle.metadata_json, f"$.{field}").table_valued("value")
        value = refs.c.value
        query = select(value, func.count()).select_from(MediaTitle).join(refs, true()).where(*visible).group_by(value)
    # Counts are per exact string; fold case if the household's vocabulary fragments.
    rows = db.execute(query.order_by(func.count().desc(), value).limit(200))
    return [{"value": v, "count": n} for v, n in rows]


def people_suggestions(db: Session, user: User, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Credits of visible titles whose name contains ``q``, prefix matches first (<= 50)."""
    q = q.strip().lower()
    if not q:
        return []
    refs = func.json_each(MediaTitle.metadata_json, "$.people").table_valued("value")
    name = func.json_extract(refs.c.value, "$.name")
    pid = func.json_extract(refs.c.value, "$.person_id")
    needle = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.execute(
        select(pid, name).select_from(MediaTitle).join(refs, true())
        .where(MediaTitle.type.in_(EDITABLE_TYPES), LibraryService.visible_title_predicate(user), pid.is_not(None),
               func.lower(name).like(f"%{needle}%", escape="\\"))
        .group_by(pid, name)
        .order_by(func.lower(name).like(f"{needle}%", escape="\\").desc(), func.count().desc(), name)
        .limit(max(1, min(limit, 50)))
    ).all()
    images, names = cast_photos.photos_and_names(db, {p for p, _ in rows})
    # Matches the credits' own names; a renamed person is found by the old name only (search their new name via FTS).
    return list({p: {"person_id": p, "name": names.get(p, n), "image_url": images.get(p)} for p, n in rows}.values())
