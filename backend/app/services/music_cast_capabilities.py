"""Database lifecycle for member-scoped Sonos queue capabilities."""
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import MusicCastTicket


def revoke_for_user(db: Session, user_id: str) -> int:
    """Revoke every Sonos queue capability owned by one member."""
    result = db.execute(delete(MusicCastTicket).where(MusicCastTicket.user_id == user_id))
    return int(result.rowcount or 0)


__all__ = ["revoke_for_user"]
