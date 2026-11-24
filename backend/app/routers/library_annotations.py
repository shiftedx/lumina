"""Library notes and member-managed tags routes."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.routers import visible_item_or_404
from app.schemas import LibraryNoteCreateRequest, LibraryNoteResponse, LibraryNoteUpdateRequest, LibraryTagCreateRequest, LibraryTagResponse
from app.security import get_current_user
from app.services.library import LibraryService
from app.services.media_notes import MediaNotesService


def register(app: FastAPI) -> None:
    def _note_error(exc: Exception) -> HTTPException:
        if isinstance(exc, PermissionError):
            return HTTPException(status_code=403, detail=str(exc))
        return HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc))


    @app.get("/api/library/{item_id}/notes", response_model=list[LibraryNoteResponse])
    def list_library_notes(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[LibraryNoteResponse]:
        service = MediaNotesService(db)
        try:
            notes = service.list_notes(item_id, current_user)
        except ValueError as exc:
            raise _note_error(exc) from exc
        return [service.serialize(note, current_user) for note in notes]


    @app.post("/api/library/{item_id}/notes", response_model=LibraryNoteResponse, status_code=201)
    def add_library_note(
        item_id: str,
        payload: LibraryNoteCreateRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> LibraryNoteResponse:
        service = MediaNotesService(db)
        try:
            note = service.add_note(item_id, payload.body, payload.visibility, current_user, payload.timestamp_ms)
        except ValueError as exc:
            raise _note_error(exc) from exc
        return service.serialize(note, current_user)


    @app.put("/api/library/notes/{note_id}", response_model=LibraryNoteResponse)
    def update_library_note(
        note_id: str,
        payload: LibraryNoteUpdateRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> LibraryNoteResponse:
        service = MediaNotesService(db)
        try:
            note = service.update_note(note_id, payload.body, payload.visibility, current_user)
        except (PermissionError, ValueError) as exc:
            raise _note_error(exc) from exc
        return service.serialize(note, current_user)


    @app.delete("/api/library/notes/{note_id}", status_code=204)
    def delete_library_note(
        note_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> None:
        try:
            MediaNotesService(db).delete_note(note_id, current_user)
        except (PermissionError, ValueError) as exc:
            raise _note_error(exc) from exc


    @app.get("/api/library/{item_id}/tags", response_model=list[LibraryTagResponse])
    def list_library_tags(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[LibraryTagResponse]:
        service = LibraryService(db)
        visible_item_or_404(db, item_id, current_user)
        return [service.serialize_tag(tag) for tag in service.list_tags(item_id, current_user)]


    @app.post("/api/library/{item_id}/tags", response_model=LibraryTagResponse, status_code=201)
    def add_library_tag(
        item_id: str,
        payload: LibraryTagCreateRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> LibraryTagResponse:
        service = LibraryService(db)
        visible_item_or_404(db, item_id, current_user)
        try:
            tag = service.add_tag(item_id, payload.tag, current_user)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return service.serialize_tag(tag)


    @app.delete("/api/library/{item_id}/tags/{tag_value}", status_code=204)
    def delete_library_tag(item_id: str, tag_value: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> None:
        service = LibraryService(db)
        visible_item_or_404(db, item_id, current_user)
        try:
            service.delete_tag(item_id, tag_value, current_user)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
