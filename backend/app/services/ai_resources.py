"""Member admission and fair dispatch shared by the existing AI job registries."""
from __future__ import annotations

from collections import Counter, OrderedDict
from contextlib import contextmanager
from threading import Condition

from sqlalchemy import func, select

from app.db import session_scope
from app.models import AppSettings, AsrJob, IdeaGraph, SeriesRecap, Summary
from app.services.resource_policy import effective

MODELS = (AsrJob, Summary, IdeaGraph, SeriesRecap)


class AiCapacityError(RuntimeError):
    pass


def duplicate(db, row, registry):
    """Recheck idempotency while holding the writer, before reserving any queue slot."""
    model = type(row)
    keys = {
        AsrJob: ("library_item_id", "kind", "model_id", "params"),
        Summary: ("transcript_id", "transcript_revision", "model_id"),
        IdeaGraph: ("summary_id", "model_id"),
        SeriesRecap: ("episode_title_id", "model_id", "inputs_digest"),
    }.get(model)
    if keys is None:
        return None
    candidates = db.query(model).filter(model.state.in_(("pending", "queued", "running", "succeeded")))
    # Compare params in Python: JSON null and SQL NULL represent the same empty parameters here.
    for key in keys:
        if key != "params":
            candidates = candidates.filter(getattr(model, key) == getattr(row, key))
    for candidate in candidates.order_by(model.created_at.desc()):
        if "params" in keys and (candidate.params or None) != (row.params or None):
            continue
        if model is AsrJob:
            # Enrichment's durable completion is only reusable while its
            # derived output still exists (for example file analysis can be
            # invalidated after a media replacement).  The initial request
            # performs this check too; retain it through JobRegistry's
            # cross-kind duplicate path.
            from app.services.enrichment import _still_done
            if not _still_done(db, candidate):
                continue
        if candidate.state in {"pending", "succeeded"} or registry.is_active(candidate.id):
            return candidate
    return None


def admit(db, owner: str | None, additional: int = 1) -> None:
    """Called inside the durable writer after duplicate/cache checks. Maintenance has its own bounded lane."""
    if owner is None:
        return
    limit = effective(db, owner).ai_queued
    if not limit:
        return
    count = sum(db.scalar(select(func.count()).select_from(model).where(
        model.requested_by == owner, model.state.in_(("pending", "queued")),
    )) or 0 for model in MODELS)
    if count + additional > limit:
        raise AiCapacityError("Your AI queue is full. Cancel waiting work or try again after a job finishes.")


_condition = Condition()
_waiting: OrderedDict[str, tuple[str, str]] = OrderedDict()
_pool_active: Counter[str] = Counter()
_active: Counter[str] = Counter()
_last_member: str | None = None


@contextmanager
def execution(model, job_id: str, registry):
    """Keep one charge across all stages of a job; cancellation can leave the wait immediately."""
    global _last_member
    with session_scope() as db:
        row = db.get(model, job_id)
        owner = row.requested_by if row else None
        live = row is not None and row.state in {"queued", "running", "pending"}
    if not live:
        yield False
        return
    if owner is None:
        yield True
        return
    acquired = False
    pool = "asr" if model is AsrJob else "ai"
    try:
        with _condition:
            _waiting[job_id] = (owner, pool)
            _condition.notify_all()
        while True:
            if registry.is_canceled(job_id):
                yield False
                return
            # No database session, writer slot or model lease remains open while waiting.
            with session_scope() as db:
                with _condition:
                    members = list(dict.fromkeys(value[0] for value in _waiting.values()))
                limits = {member: effective(db, member).ai_running for member in members}
                from app.services.local_ai import effective_config
                record = db.get(AppSettings, 1)
                pool_limit = max(1, effective_config(record).ai_max_concurrency) if pool == "ai" else registry.limit
            with _condition:
                members = list(dict.fromkeys(value[0] for value in _waiting.values()))
                if _last_member in members:
                    at = members.index(_last_member) + 1
                    members = members[at:] + members[:at]
                eligible = {member for member, waiting_pool in _waiting.values() if waiting_pool == pool}
                chosen = next((member for member in members if member in eligible and member in limits and
                               (not limits[member] or _active[member] < limits[member])), None)
                first = next((key for key, value in _waiting.items() if value == (chosen, pool)), None)
                if first == job_id and _pool_active[pool] < pool_limit:
                    del _waiting[job_id]
                    _active[owner] += 1
                    _pool_active[pool] += 1
                    _last_member = owner
                    acquired = True
                    _condition.notify_all()
                    break
                _condition.wait(timeout=0.1)
        yield True
    finally:
        with _condition:
            _waiting.pop(job_id, None)
            if acquired:
                _active[owner] -= 1
                _pool_active[pool] -= 1
                if not _active[owner]:
                    del _active[owner]
            _condition.notify_all()


def recover(db) -> None:
    """Summary workers are ephemeral; persist interruption so their queue charges cannot survive restart."""
    from app.models import utcnow
    from app.persistence import write_transaction
    with write_transaction(db, name="ai_resources_recover"):
        for model in (Summary, IdeaGraph, SeriesRecap):
            db.query(model).filter(model.state.in_(("queued", "running"))).update(
                {"state": "interrupted", "completed_at": utcnow()}, synchronize_session=False,
            )


@contextmanager
def interactive_execution(owner: str, member_limit: int, pool_limit: int):
    """Fail-fast admission for synchronous member AI work.

    Smart-collection drafting has no durable job row, so waiting would create
    an uncharged, uncancellable HTTP queue outside the member's durable queue.
    It therefore shares the running counters with summaries, recaps, graphs
    and ASR, but returns a capacity error when the member or AI pool is full,
    or when durable work is already waiting for that member.
    """
    acquired = False
    with _condition:
        durable_waiting = any(waiting_owner == owner for waiting_owner, _pool in _waiting.values())
        if durable_waiting or (member_limit and _active[owner] >= member_limit) or _pool_active["ai"] >= pool_limit:
            raise AiCapacityError("Your AI capacity is busy. Wait for queued work to finish or try again shortly.")
        _active[owner] += 1
        _pool_active["ai"] += 1
        acquired = True
    try:
        yield
    finally:
        if acquired:
            with _condition:
                _active[owner] -= 1
                _pool_active["ai"] -= 1
                if not _active[owner]:
                    del _active[owner]
                _condition.notify_all()
