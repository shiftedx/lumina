"""Database lifecycle for member-scoped Sonos queue capabilities."""
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import MediaCastTicket, MusicCastTicket, MusicReceiverTicket


def revoke_for_user(db: Session, user_id: str) -> int:
    """Revoke every music and video receiver capability owned by one member."""
    music = db.execute(delete(MusicCastTicket).where(MusicCastTicket.user_id == user_id))
    receiver = db.execute(delete(MusicReceiverTicket).where(MusicReceiverTicket.user_id == user_id))
    video = db.execute(delete(MediaCastTicket).where(MediaCastTicket.user_id == user_id))
    return int(music.rowcount or 0) + int(receiver.rowcount or 0) + int(video.rowcount or 0)


def revoke_for_devices(db: Session, device_ids: list[str]) -> int:
    """A removed paired app loses every receiver URL it issued."""
    if not device_ids:
        return 0
    result = db.execute(delete(MusicReceiverTicket).where(MusicReceiverTicket.issuer_device_id.in_(device_ids)))
    return int(result.rowcount or 0)


__all__ = ["revoke_for_devices", "revoke_for_user"]
