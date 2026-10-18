"""Database lifecycle for member-scoped Sonos queue capabilities."""
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import MediaCastTicket, MusicCastTicket


def revoke_for_user(db: Session, user_id: str) -> int:
    """Revoke every Sonos queue capability owned by one member."""
    music = db.execute(delete(MusicCastTicket).where(MusicCastTicket.user_id == user_id))
    video = db.execute(delete(MediaCastTicket).where(MediaCastTicket.user_id == user_id))
    return int(music.rowcount or 0) + int(video.rowcount or 0)


__all__ = ["revoke_for_user"]
