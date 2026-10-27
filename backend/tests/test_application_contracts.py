from __future__ import annotations

from fastapi.dependencies.models import Dependant

from app.db import get_db
from app.main import app


def test_request_database_transactions_finish_before_responses_are_sent() -> None:
    database_dependencies: list[tuple[str, str | None]] = []

    def visit(path: str, dependant: Dependant) -> None:
        for dependency in dependant.dependencies:
            if dependency.call is get_db:
                database_dependencies.append((path, dependency.scope))
            visit(path, dependency)

    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            visit(route.path, dependant)

    assert database_dependencies
    assert [
        path for path, scope in database_dependencies if scope != "function"
    ] == []
