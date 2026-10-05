"""Route modules (each exposes ``register(app)``) and the route helpers they share."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import LibraryItem, User
from app.services.library import LibraryService


def visible_item_or_404(db: Session, item_id: str, user: User) -> LibraryItem:
    """The Library item ``user`` may see, else the same 404 whether it is missing or private."""
    item = LibraryService(db).get_item(item_id, user)
    if item is None:
        raise HTTPException(status_code=404, detail="Library item not found")
    return item
