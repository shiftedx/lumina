"""Bounded import history retention: keep the newest runs per root, delete the rest in small batches."""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import ImportEntry, ImportRun, StorageRoot
from app.persistence import write_transaction
from app.services.library_import import ACTIVE_STATES

RETAIN_RUNS = 100
PRUNE_ROWS_PER_HOUR = 2000
_KEEP_STATES = (*ACTIVE_STATES, "needs_confirmation")


def prune_history(db: Session, *, retain: int = RETAIN_RUNS, row_limit: int = PRUNE_ROWS_PER_HOUR) -> int:
    """Delete terminal runs beyond the newest `retain` per root (never the newest full run), entries first.

    Returns the rows deleted (runs + entries), at most `row_limit`; one write transaction per run.
    """
    # One transaction per run (<= row_limit rows); a root with huge entry counts takes several hourly calls.
    deleted = 0
    for root_id in db.scalars(select(StorageRoot.id)).all():
        runs = ImportRun.root_id == root_id
        order = (ImportRun.created_at.desc(), ImportRun.id.desc())
        keep_full = db.scalar(select(ImportRun.id).where(runs, ImportRun.scope.is_(None)).order_by(*order).limit(1))
        old = db.scalars(
            select(ImportRun.id).where(runs, ImportRun.state.not_in(_KEEP_STATES)).order_by(*order).offset(retain)
        ).all()
        for run_id in reversed(old):  # oldest first
            if run_id == keep_full:
                continue
            budget = row_limit - deleted
            if budget <= 0:
                return deleted
            with write_transaction(db, name="library_retention"):
                ids = db.scalars(select(ImportEntry.id).where(ImportEntry.run_id == run_id).limit(budget)).all()
                if ids:
                    db.execute(delete(ImportEntry).where(ImportEntry.id.in_(ids)))
                    deleted += len(ids)
                remaining = db.scalar(select(func.count()).select_from(ImportEntry).where(ImportEntry.run_id == run_id))
                if remaining:
                    return deleted  # entries left: the run stays until the next call
                if deleted < row_limit:
                    db.execute(delete(ImportRun).where(ImportRun.id == run_id))
                    deleted += 1
                else:
                    return deleted
    return deleted
