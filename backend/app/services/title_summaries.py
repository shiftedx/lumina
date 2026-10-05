"""Title summaries by id for other features (home rows, Similar, Continue Watching's ``title``)."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.media_schemas import TitleSummary
from app.models import MediaTitle, User


def title_summaries(db: Session, user: User, title_ids: Iterable[str]) -> list[TitleSummary]:
    """Visible titles only, in input order (duplicates collapse to the first), serialized set-based."""
    from app.services.titles import TitleService  # Lazy import — playback.py imports this module

    ids = list(dict.fromkeys(title_ids))
    service = TitleService(db)
    found = {title.id: title for title in db.scalars(select(MediaTitle).where(MediaTitle.id.in_(ids), service.visible(user)))}
    return service.summaries(user, [found[title_id] for title_id in ids if title_id in found])
