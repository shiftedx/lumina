from __future__ import annotations

from pathlib import Path

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


def test_production_python_avoids_deprecated_naive_utc_constructors() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    deprecated_calls = {
        str(path.relative_to(app_root)): constructor
        for path in app_root.rglob("*.py")
        for constructor in ("datetime.utcnow(", "datetime.utcfromtimestamp(")
        if constructor in path.read_text(encoding="utf-8")
    }
    assert deprecated_calls == {}
