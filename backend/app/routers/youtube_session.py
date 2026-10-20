"""A personal, explicit opt-in; no endpoint ever returns cookies."""
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, YouTubeSession
from app.persistence import write_transaction
from app.security import get_current_user
from app.services import youtube_session
from app.services.rate_limit import enforce_rate_limit


class ConnectBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cookies: str = Field(min_length=1, max_length=youtube_session.MAX_COOKIE_BYTES, repr=False)
    consent: bool = False


def status(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope='function')) -> dict:
    response.headers['Cache-Control'] = 'no-store'
    return youtube_session.snapshot(db, user.id)


def connect(body: ConnectBody, request: Request, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope='function')) -> dict:
    enforce_rate_limit('youtube_session', request, user_id=user.id)
    if not body.consent:
        raise HTTPException(400, 'Confirm that Lumina may use this YouTube session for your playback.')
    try:
        result = youtube_session.connect(db, user.id, body.cookies)
    except youtube_session.InvalidYouTubeSession as exc:
        raise HTTPException(400, str(exc)) from None
    _revoke_streams(user.id)
    response.headers['Cache-Control'] = 'no-store'
    return result


def disconnect(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope='function')) -> dict:
    with write_transaction(db, name='youtube_session_disconnect'):
        row = db.get(YouTubeSession, user.id)
        if row:
            db.delete(row)
    _revoke_streams(user.id)
    response.headers['Cache-Control'] = 'no-store'
    return {'status': 'not_connected', 'updated_at': None}


def _revoke_streams(user_id: str) -> None:
    from app.main import remote_streams
    remote_streams.revoke_personal_youtube(user_id)


def register(app: FastAPI) -> None:
    app.get('/api/me/youtube-session')(status)
    app.put('/api/me/youtube-session')(connect)
    app.delete('/api/me/youtube-session')(disconnect)
