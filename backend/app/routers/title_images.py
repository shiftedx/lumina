"""Title artwork routes: TMDB candidates, choose, upload, remove, reorder backdrops.

Every route runs the session auth and ``get_detail_editor`` (403 ``editing_not_allowed``) before it looks at the
title or the body, then resolves the title with the visibility predicate (an invisible title is 404). Writes go
through ``metadata_editor.write_user_field`` in one history batch; this module never calls ``apply_field``.
"""
from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from app.db import get_db
from app.media_schemas import BackdropOrder, ImageCandidate, ImagePutTmdb, PersonDoc, PersonEditResult, TitleImageEntry
from app.models import AppSettings, MediaTitle, TitleUpload, User
from app.persistence import write_transaction
from app.routers.admin_metadata import _image
from app.services import metadata_editor, people_editor, title_image_candidates, title_images, tmdb
from app.services.artwork import ArtworkService
from app.services.media_probe import media_tool
from app.services.media_titles import parse_item_id
from app.services.rate_limit import enforce_rate_limit
from app.services.titles import title_image_source

JSON_BODY_LIMIT = 4096
CANDIDATE_IMAGE = "/api/metadata/candidate-image"


def _load(db: Session, user: User, title_id: str) -> MediaTitle:
    parsed = parse_item_id(title_id)
    title = metadata_editor.load_titles(db, user, [parsed]).get(parsed) if parsed else None
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return title


def editable_title(
    title_id: str, user: Annotated[User, Depends(metadata_editor.get_detail_editor)], db: Session = Depends(get_db, scope="function"),
) -> MediaTitle:
    return _load(db, user, title_id)


def _slots(db: Session, title: MediaTitle) -> list[dict]:
    shas = {str(e["upload"]) for e in (title.images or {}).values() if isinstance(e, dict) and e.get("upload")}
    uploads = {u.sha256: u for u in db.scalars(select(TitleUpload).where(TitleUpload.sha256.in_(shas)))} if shas else {}
    return metadata_editor.image_slots(title, uploads)


def _refuse(error: title_images.UploadError) -> HTTPException:
    return HTTPException(status_code=error.status, detail=error.detail, headers={"Retry-After": "5"} if error.detail == "encoder_busy" else None)


def _body_refused(request: Request) -> Exception:
    """The upload body ended early: 413 past the cap, 408 when it stalled, else the client went away."""
    state = request.scope.get("state", {})
    if state.get("body_too_large"):
        return HTTPException(status_code=413, detail="too_large")
    if state.get("body_timeout"):
        return HTTPException(status_code=408, detail="body_timeout")
    return ClientDisconnect()


def _write(db: Session, user: User, title_id: str, change) -> list[dict]:  # noqa: ANN001
    """One write transaction: reload the title under the writer lock, ``change(title, batch)``, finish the batch, list the slots."""
    try:
        with write_transaction(db, name="title_image"):
            title = _load(db, user, title_id)
            batch = metadata_editor.Batch(db, "image", user.id)
            change(title, batch)
            batch.finish(db)
    except title_images.UploadError as error:
        raise _refuse(error) from None
    return _slots(db, title)


def _upload(db: Session, user: User, title: MediaTitle, image_type: str, index: int, data: bytes, base_tag: str | None) -> list[dict]:
    try:
        title_images.check_base(title, title_images.check_slot(title, image_type, index), base_tag)
        encoded = title_images.encode(data, image_type, ffmpeg=media_tool(db, "ffmpeg"))
    except title_images.UploadError as error:
        raise _refuse(error) from None

    def change(fresh: MediaTitle, batch: metadata_editor.Batch) -> None:
        key = title_images.check_write(fresh, image_type, index, base_tag, encoded.sha256)
        title_images.save(db, encoded, user.id)
        title_images.write_image(fresh, key, {"upload": encoded.sha256, "tag": encoded.sha256}, batch)

    return _write(db, user, title.id, change)


def _person_id(db: Session, user: User, person_id: str) -> str:
    try:
        return people_editor.visible_person(db, user, person_id)
    except people_editor.PersonNotFound:
        raise HTTPException(status_code=404, detail="person_not_found") from None


def _person_photo(db: Session, user: User, person_id: str, sha: str | None, encoded=None) -> PersonEditResult:  # noqa: ANN001
    """#164: a household photo for a person on every title (None = back to the source's photo); one history batch."""
    if encoded is not None:
        try:
            with write_transaction(db, name="person_photo_upload"):
                title_images.save(db, encoded, user.id)
        except title_images.UploadError as error:
            raise _refuse(error) from None
    batch_id = people_editor.edit(db, user, person_id, "person.photo", sha)
    return PersonEditResult(batch_id=batch_id, person=PersonDoc(**people_editor.person_doc(db, user, person_id)))


def _person_upload(db: Session, user: User, person_id: str, data: bytes) -> PersonEditResult:
    try:
        encoded = title_images.encode(data, "Primary", ffmpeg=media_tool(db, "ffmpeg"))
    except title_images.UploadError as error:
        raise _refuse(error) from None
    return _person_photo(db, user, person_id, encoded.sha256, encoded)


def register(app: FastAPI, artwork: ArtworkService) -> None:
    person_photo = "/api/metadata/people/{person_id}/photo"

    @app.put(person_photo, response_model=PersonEditResult)
    async def put_person_photo(
        person_id: str, request: Request, user: User = Depends(metadata_editor.get_detail_editor),
        db: Session = Depends(get_db, scope="function"),
    ) -> PersonEditResult:
        parsed = await run_in_threadpool(_person_id, db, user, person_id)  # 404 before the body is read
        enforce_rate_limit("image_upload", request, user_id=user.id)
        try:
            data = await request.body()
        except ClientDisconnect:
            raise _body_refused(request) from None
        if request.scope.get("state", {}).get("body_too_large") or len(data) > title_images.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="too_large")
        return await run_in_threadpool(_person_upload, db, user, parsed, data)

    @app.delete(person_photo, response_model=PersonEditResult)
    def delete_person_photo(
        person_id: str, user: User = Depends(metadata_editor.get_detail_editor), db: Session = Depends(get_db, scope="function"),
    ) -> PersonEditResult:
        return _person_photo(db, user, _person_id(db, user, person_id), None)

    base = "/api/titles/{title_id}/images"

    @app.get(base + "/{image_type}/candidates", response_model=list[ImageCandidate])
    def candidates(
        image_type: str, request: Request, index: int = Query(0), user: User = Depends(metadata_editor.get_detail_editor),
        title: MediaTitle = Depends(editable_title), db: Session = Depends(get_db, scope="function"),
    ) -> list[ImageCandidate]:
        try:
            title_images.check_slot(title, image_type, index)
        except title_images.UploadError as error:
            raise _refuse(error) from None
        client = tmdb.client_for(db.get(AppSettings, 1))
        if client is None:
            raise HTTPException(status_code=409, detail="tmdb_not_configured")
        enforce_rate_limit("artwork", request, user_id=user.id)
        try:
            rows = title_image_candidates.candidates(client, db, title, image_type)
        except title_image_candidates.NoTmdbId:
            raise HTTPException(status_code=409, detail="no_tmdb_id") from None
        except tmdb.TmdbError:
            raise HTTPException(status_code=502, detail="tmdb_unavailable") from None
        return [ImageCandidate(**row, preview_url=f"{CANDIDATE_IMAGE}?path={quote(row['tmdb_path'])}&type={image_type}")
                for row in rows if row["width"] and row["height"]]

    @app.get(CANDIDATE_IMAGE)
    def candidate_image(
        request: Request, path: str = Query(max_length=100), type: str = Query(max_length=16),  # noqa: A002
        user: User = Depends(metadata_editor.get_detail_editor),
    ) -> Response:
        if type not in tmdb.CANDIDATE_SIZES:
            raise HTTPException(status_code=422, detail="type_not_allowed")
        # A cold fetch costs the per-user "artwork" bucket; previews live in their own size-capped LRU cache.
        return _image(request, user, lambda: tmdb.load_image(
            artwork, path, type, size=tmdb.CANDIDATE_SIZES[type], bucket="candidates", max_bytes=tmdb.CANDIDATE_CACHE_BYTES,
            before_upstream_fetch=lambda: enforce_rate_limit("artwork", request, user_id=user.id),
        ))

    @app.put(base + "/{image_type}/{index}", response_model=list[TitleImageEntry])
    async def put_image(
        image_type: str, index: int, request: Request, base_tag: str | None = Query(None, max_length=128),
        user: User = Depends(metadata_editor.get_detail_editor), title: MediaTitle = Depends(editable_title),
        db: Session = Depends(get_db, scope="function"),
    ) -> list[dict]:
        # Auth, edit permission and the title lookup are dependencies: nothing below runs for a caller who fails them,
        # and the body (scoped 15 MiB cap in http_boundaries) is read only after the upload rate limit.
        try:
            title_images.check_slot(title, image_type, index, adding=True)
        except title_images.UploadError as error:
            raise _refuse(error) from None
        if request.headers.get("content-type", "").split(";")[0].strip().lower() == "application/json":
            raw = b""
            try:
                async for chunk in request.stream():
                    raw += chunk
                    if len(raw) > JSON_BODY_LIMIT:
                        raise HTTPException(status_code=413, detail="too_large")
            except ClientDisconnect:
                raise _body_refused(request) from None
            try:
                body = ImagePutTmdb.model_validate_json(raw)
            except ValidationError:
                raise HTTPException(status_code=422, detail="invalid_body") from None
            if not tmdb._IMAGE_PATH.fullmatch(body.tmdb_path):  # noqa: SLF001
                raise HTTPException(status_code=422, detail="invalid_path")
            enforce_rate_limit("artwork", request, user_id=user.id)  # a choice pins into the TMDB cache: same bucket as the proxy

            def choose(fresh: MediaTitle, batch: metadata_editor.Batch) -> None:
                tag = f"tmdb:{body.tmdb_path}"
                title_images.write_image(fresh, title_images.check_write(fresh, image_type, index, body.base_tag, tag), {"tmdb": body.tmdb_path, "tag": tag}, batch)

            return await run_in_threadpool(_write, db, user, title.id, choose)
        enforce_rate_limit("image_upload", request, user_id=user.id)  # only an upload spends an ffmpeg run
        try:
            data = await request.body()
        except ClientDisconnect:
            raise _body_refused(request) from None
        if request.scope.get("state", {}).get("body_too_large") or len(data) > title_images.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="too_large")
        return await run_in_threadpool(_upload, db, user, title, image_type, index, data, base_tag)

    @app.delete(base + "/{image_type}/{index}", response_model=list[TitleImageEntry])
    def delete_image(
        image_type: str, index: int, base_tag: str | None = Query(None, max_length=128),
        user: User = Depends(metadata_editor.get_detail_editor), title: MediaTitle = Depends(editable_title),
        db: Session = Depends(get_db, scope="function"),
    ) -> list[dict]:
        try:
            key = title_images.check_slot(title, image_type, index)
        except title_images.UploadError as error:
            raise _refuse(error) from None

        def remove(fresh: MediaTitle, batch: metadata_editor.Batch) -> None:
            if title_image_source(fresh, key) is None:
                raise title_images.UploadError(404, "empty_slot")
            title_images.check_base(fresh, key, base_tag)
            title_images.delete_image(fresh, key, batch)

        return _write(db, user, title.id, remove)

    @app.post(base + "/Backdrop/order", response_model=list[TitleImageEntry])
    def order_backdrops(
        body: BackdropOrder, user: User = Depends(metadata_editor.get_detail_editor), title: MediaTitle = Depends(editable_title),
        db: Session = Depends(get_db, scope="function"),
    ) -> list[dict]:
        try:
            title_images.check_slot(title, "Backdrop", 0)
        except title_images.UploadError as error:
            raise _refuse(error) from None
        return _write(db, user, title.id, lambda fresh, batch: title_images.reorder_backdrops(fresh, body.tags, batch))
