"""Member-scoped destinations for the browser."""
from fastapi import Depends, FastAPI, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import get_current_user
from app.services.navigation import navigation


def get_navigation(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return navigation(db, user)


def register(app: FastAPI) -> None:
    app.get("/api/navigation")(get_navigation)
