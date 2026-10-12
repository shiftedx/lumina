"""Metadata editor routes. Thin: services own validation, transactions and history."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import (
    BulkRequest, BulkResult, EditRequest, EpisodeGroup, PersonDoc, PersonEditResult, PersonNameEdit, EditResult, EpisodeTable, HistoryPage, PersonSuggestion, RefreshPreview,
    RevertRequest, RevertResult, TitleMetadataDoc, UndoResult, VocabularyEntry,
)
from app.models import AppSettings, MediaTitle, User, utcnow
from app.persistence import write_transaction
from app.security import get_admin_user
from app.services import two_factor, metadata_bulk, metadata_editor as me, metadata_history, metadata_preview, people_editor, title_metadata, tmdb
from app.services.media_titles import parse_item_id
from app.services.rate_limit import enforce_rate_limit

get_detail_editor = me.get_detail_editor
TITLE_NOT_FOUND = "Title not found"


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": code, **extra})


def _fail(error: Exception) -> JSONResponse:
    """The one exception-to-response map for every route."""
    if isinstance(error, me.FieldError):
        extra = {k: v for k, v in (("title_id", error.title_id), ("field", error.field)) if v}
        return _error(422, "invalid_field", **extra, reason=error.reason)
    if isinstance(error, me.TitleNotFound):
        return _error(404, TITLE_NOT_FOUND)
    if isinstance(error, me.IdentifyRequiresOwner):
        return _error(403, "identify_requires_owner")
    if isinstance(error, title_metadata.TitleLocked):
        return _error(409, "title_locked")
    if isinstance(error, metadata_preview.PreviewError):
        return _error(502 if error.code == "tmdb_unavailable" else 409, error.code, **({"match": error.match} if error.code == "no_match" else {}))
    if isinstance(error, metadata_history.BatchNotFound):
        return _error(404, "batch_not_found")
    if isinstance(error, metadata_history.AlreadyUndone):
        return _error(409, "already_undone")
    if isinstance(error, people_editor.PersonNotFound):
        return _error(404, "person_not_found")
    raise error


_HANDLED = (me.FieldError, me.TitleNotFound, me.IdentifyRequiresOwner, title_metadata.TitleLocked, metadata_preview.PreviewError,
            metadata_history.BatchNotFound, metadata_history.AlreadyUndone, people_editor.PersonNotFound)


def _load(db: Session, user: User, title_id: str) -> MediaTitle:
    parsed = parse_item_id(title_id)
    title = me.load_titles(db, user, [parsed] if parsed else []).get(parsed or "")
    if title is None:
        raise me.TitleNotFound
    return title


def _docs(db: Session, user: User, ids: list[str]) -> list[TitleMetadataDoc]:
    titles = me.load_titles(db, user, ids)
    return me.build_docs(db, user, [titles[i] for i in ids if i in titles])


def get_doc(title_id: str, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    try:
        return me.build_docs(db, user, [_load(db, user, title_id)])[0]
    except _HANDLED as error:
        return _fail(error)


def save(body: EditRequest, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    entries = [me.EditEntry(e.title_id, {k: me.Change(c.value, c.base, "base" in c.model_fields_set) for k, c in e.changes.items()},
                            list(e.pin), e.locked) for e in body.edits]
    try:
        batch_id, applied, conflicts = me.save_edits(db, user, entries, owner=two_factor.owner_capable(db, user))
        result = EditResult(batch_id=batch_id if applied else None, titles=_docs(db, user, applied), conflicts=conflicts)
    except _HANDLED as error:
        return _fail(error)
    return JSONResponse(status_code=409 if conflicts and not applied else 200, content=result.model_dump(mode="json"))


def revert(title_id: str, body: RevertRequest, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    try:
        title = _load(db, user, title_id)
        allowed = {*me.fields_for(title.type), *(f"images.{k}" for k in me.IMAGE_KEYS)}
        for field in body.fields:
            if field not in allowed:
                raise me.FieldError("unknown_field", field=field, title_id=title.id)
        batch, now, changed = me.Batch(db, "revert", user.id), utcnow(), False
        with write_transaction(db, name="metadata_revert"):
            title = _load(db, user, title_id)
            for field in body.fields:
                changed = me.revert_field(title, field, batch, now, owner=two_factor.owner_capable(db, user)) or changed
            batch.finish(db)
        return RevertResult(batch_id=batch.id if changed else None, title=_docs(db, user, [title.id])[0])
    except _HANDLED as error:
        return _fail(error)


def get_history(title_id: str, cursor: str | None = None, limit: int = Query(20, ge=1, le=100),
                user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    try:
        return HistoryPage(**metadata_history.history(db, _load(db, user, title_id), cursor, limit, user))
    except (*_HANDLED, ValueError) as error:
        return _fail(error) if isinstance(error, _HANDLED) else _error(422, "invalid_cursor")


def undo(batch_id: str, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    try:
        return UndoResult(**metadata_history.undo(db, user, batch_id, owner=two_factor.owner_capable(db, user)))
    except _HANDLED as error:
        return _fail(error)


def bulk(body: BulkRequest, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    try:
        return BulkResult(**metadata_bulk.bulk_edit(db, user, body.title_ids, [op.model_dump(exclude_none=True) for op in body.ops],
                                                    owner=two_factor.owner_capable(db, user)))
    except _HANDLED as error:
        return _fail(error)


def get_vocabulary(field: str, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    try:
        return [VocabularyEntry(**row) for row in metadata_bulk.vocabulary(db, user, field)]
    except _HANDLED as error:
        return _fail(error)


def get_people(request: Request, q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=50),
               user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")) -> list[PersonSuggestion]:
    enforce_rate_limit("metadata_people", request, user_id=user.id)
    return [PersonSuggestion(**row) for row in metadata_bulk.people_suggestions(db, user, q, limit)]


def refresh_preview(title_id: str, request: Request, user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    enforce_rate_limit("metadata_preview", request, user_id=user.id)
    try:
        return RefreshPreview(**metadata_preview.refresh_preview(db, _load(db, user, title_id)))
    except _HANDLED as error:
        return _fail(error)


def get_episodes(title_id: str, season_id: str | None = None, user: User = Depends(get_detail_editor),
                 db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    try:
        return EpisodeTable(**metadata_preview.episode_table(db, user, _load(db, user, title_id), season_id))
    except _HANDLED as error:
        return _fail(error)


def get_episode_groups(title_id: str, request: Request, user: User = Depends(get_detail_editor),
                       db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    """A series' TMDB episode groups, for picking the one its display order reads through (#164)."""
    enforce_rate_limit("metadata_preview", request, user_id=user.id)
    try:
        title = _load(db, user, title_id)
    except _HANDLED as error:
        return _fail(error)
    tmdb_id = (title.provider_ids or {}).get("Tmdb")
    client = tmdb.client_for(db.get(AppSettings, 1))
    if title.type != "series" or client is None or not str(tmdb_id or "").isdigit():
        return _error(409, "no_tmdb_id" if client is not None else "tmdb_not_configured")
    try:
        return [EpisodeGroup(**row) for row in title_metadata.episode_groups(client, int(tmdb_id))]
    except tmdb.TmdbError:
        return _error(502, "tmdb_unavailable")


def get_person(person_id: str, user: User = Depends(get_detail_editor), db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    try:
        return PersonDoc(**people_editor.person_doc(db, user, people_editor.visible_person(db, user, person_id)))
    except _HANDLED as error:
        return _fail(error)


def rename_person(person_id: str, body: PersonNameEdit, user: User = Depends(get_detail_editor),
                  db: Session = Depends(get_db, scope="function")):  # noqa: ANN201
    """Rename a person on every title that credits them (#164); a null name goes back to the source's."""
    try:
        parsed = people_editor.visible_person(db, user, person_id)
        batch_id = people_editor.edit(db, user, parsed, "person.name", body.name)
        return PersonEditResult(batch_id=batch_id, person=PersonDoc(**people_editor.person_doc(db, user, parsed)))
    except _HANDLED as error:
        return _fail(error)


def register(app: FastAPI) -> None:
    app.get("/api/titles/{title_id}/metadata", response_model=TitleMetadataDoc)(get_doc)
    app.post("/api/metadata/edits", response_model=EditResult)(save)
    app.post("/api/titles/{title_id}/metadata/revert", response_model=RevertResult)(revert)
    app.get("/api/titles/{title_id}/metadata/history", response_model=HistoryPage)(get_history)
    app.post("/api/metadata/batches/{batch_id}/undo", response_model=UndoResult)(undo)
    app.post("/api/metadata/bulk", response_model=BulkResult)(bulk)
    app.get("/api/metadata/vocabulary", response_model=list[VocabularyEntry])(get_vocabulary)
    app.get("/api/metadata/people", response_model=list[PersonSuggestion])(get_people)
    app.get("/api/metadata/people/{person_id}", response_model=PersonDoc)(get_person)
    app.put("/api/metadata/people/{person_id}", response_model=PersonEditResult)(rename_person)
    app.post("/api/titles/{title_id}/metadata/refresh-preview", response_model=RefreshPreview)(refresh_preview)
    app.get("/api/titles/{title_id}/metadata/episodes", response_model=EpisodeTable)(get_episodes)
    app.get("/api/titles/{title_id}/metadata/episode-groups", response_model=list[EpisodeGroup])(get_episode_groups)
