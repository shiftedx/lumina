"""GET /api/public/showcase: the sign-in page's public release slides, and their images. No session (reviewed in
test_unauthenticated_surface): public catalog data only, rate limited per client, and the image route serves only
URLs the current showcase put in a slide (services/showcase.py)."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import showcase
from app.services.artwork import ArtworkError
from app.services.network_policy import PublicSourcePolicyError
from app.services.rate_limit import enforce_rate_limit, rate_limit_dependency
from app.services.yt_dlp_service import YtDlpService


def register(app: FastAPI) -> None:
    @app.get("/api/public/showcase", dependencies=[Depends(rate_limit_dependency("showcase"))])
    def public_showcase(response: Response, db: Session = Depends(get_db, scope="function")) -> dict:
        response.headers["Cache-Control"] = "public, max-age=900"
        return {"slides": showcase.slides(YtDlpService(db).get_app_settings())}

    @app.get("/api/public/showcase/art/{token}")
    def public_showcase_art(token: str, request: Request) -> Response:
        enforce_rate_limit("showcase_art", request)
        url = showcase.source(token)
        if url is None:
            raise HTTPException(status_code=404, detail="Image not found")
        artwork = request.app.state.artwork
        try:
            resolved = artwork.load_remote_artwork(artwork.register_remote_artwork(url))
        except (ArtworkError, PublicSourcePolicyError):
            raise HTTPException(status_code=404, detail="Image not found") from None
        return Response(content=resolved.content, media_type=resolved.content_type, headers={
            "Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
        })
