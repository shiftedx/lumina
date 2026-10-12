"""From-start provenance + partial-history honesty (issue #98).

#98 extends #97's multi-output partial-outcome honesty with a second dimension:
did media capture begin at the SOURCE BEGINNING or at the LIVE EDGE, and — the
settled invariant — from-start is best-effort/experimental and NEVER silently
claims complete history. When a from-start capture's beginning history is
incomplete (media gap OR missing earlier chat), the acquisition is an explicit
PARTIAL-HISTORY condition, never a false ``completed``.

These are pure functions so the honesty rule is exhaustively testable and reused
by restart recovery, exactly like ``compute_overall_status`` in #97.
"""

from __future__ import annotations

from app.services.live_recording import (
    MediaRunProvenance,
    compute_overall_status,
    resolve_capture_provenance,
    resolve_history,
)


# -- compute_overall_status keeps #97 semantics, adds history_partial ---------


def test_history_partial_demotes_a_would_be_completed_to_partial() -> None:
    # Media reached source-end and chat is terminal-ok, but the from-start history
    # is incomplete (e.g. chat never reached back to the media beginning): honest
    # PARTIAL, never a false ``completed``.
    assert (
        compute_overall_status("completed", "completed", cancel_requested=False, history_partial=True)
        == "partial"
    )


def test_history_partial_never_upgrades_a_failure_or_cancel() -> None:
    assert (
        compute_overall_status("failed", "failed", cancel_requested=False, history_partial=True)
        == "failed"
    )
    assert (
        compute_overall_status("failed", "failed", cancel_requested=True, history_partial=True)
        == "cancelled"
    )


# -- resolve_capture_provenance: intent + run outcome -> media honesty --------


def _prov(**kw) -> MediaRunProvenance:
    base = dict(
        library_item_id="item-1",
        reached_source_end=True,
        began_at_source_start=True,
        history_complete=True,
        from_start_supported=True,
        needs_fallback_choice=False,
    )
    base.update(kw)
    return MediaRunProvenance(**base)


def test_from_start_full_history_to_end_is_completed_from_the_beginning() -> None:
    origin, media_status, media_history_complete = resolve_capture_provenance(
        start_intent="from_start", fallback_policy="allow_live_edge", outcome=_prov()
    )
    assert origin == "source_beginning"
    assert media_status == "completed"
    assert media_history_complete is True


def test_from_start_incomplete_beginning_is_partial_never_completed() -> None:
    # Began at the beginning but the provider history had a gap: media is a usable
    # PARTIAL, never a false complete, even though it reached source-end.
    origin, media_status, media_history_complete = resolve_capture_provenance(
        start_intent="from_start",
        fallback_policy="allow_live_edge",
        outcome=_prov(history_complete=False),
    )
    assert origin == "source_beginning"
    assert media_status == "partial"
    assert media_history_complete is False


def test_from_start_unsupported_with_allowed_fallback_records_live_edge() -> None:
    # From-start not supported, fallback allowed: honest edge capture, recorded as
    # live_edge so the final result shows where capture actually began.
    origin, media_status, _ = resolve_capture_provenance(
        start_intent="from_start",
        fallback_policy="allow_live_edge",
        outcome=_prov(began_at_source_start=False, from_start_supported=False),
    )
    assert origin == "live_edge"
    # Reached source-end from the edge, but the member wanted the beginning: this
    # is a partial capture of the intended whole broadcast.
    assert media_status == "partial"


def test_live_edge_intent_reaching_source_end_stays_completed() -> None:
    # #97 semantics: a deliberate edge recording that reaches source-end is
    # ``completed`` (it never claimed earlier history), unchanged by #98.
    origin, media_status, _ = resolve_capture_provenance(
        start_intent="live_edge",
        fallback_policy="allow_live_edge",
        outcome=_prov(began_at_source_start=False, from_start_supported=False),
    )
    assert origin == "live_edge"
    assert media_status == "completed"


def test_live_edge_intent_not_reaching_end_is_partial() -> None:
    _, media_status, _ = resolve_capture_provenance(
        start_intent="live_edge",
        fallback_policy="allow_live_edge",
        outcome=_prov(began_at_source_start=False, reached_source_end=False, from_start_supported=False),
    )
    assert media_status == "partial"


# -- resolve_history: recording-level provenance for the surface --------------


def test_history_complete_requires_source_beginning_and_chat_coverage() -> None:
    assert (
        resolve_history(
            start_intent="from_start",
            capture_origin="source_beginning",
            media_history_complete=True,
            chat_covered_from_start=True,
        )
        == "complete"
    )


def test_history_partial_when_chat_misses_the_beginning() -> None:
    # Media captured the whole broadcast from the beginning, but chat only covers
    # from connect forward: missing earlier chat is an explicit partial-history.
    assert (
        resolve_history(
            start_intent="from_start",
            capture_origin="source_beginning",
            media_history_complete=True,
            chat_covered_from_start=False,
        )
        == "partial"
    )


def test_history_partial_when_media_beginning_incomplete() -> None:
    assert (
        resolve_history(
            start_intent="from_start",
            capture_origin="source_beginning",
            media_history_complete=False,
            chat_covered_from_start=True,
        )
        == "partial"
    )


def test_history_from_edge_for_deliberate_edge_intent() -> None:
    # A deliberate live-edge recording is not a partial-history defect; it is the
    # intended edge capture, labelled distinctly.
    assert (
        resolve_history(
            start_intent="live_edge",
            capture_origin="live_edge",
            media_history_complete=False,
            chat_covered_from_start=False,
        )
        == "from_edge"
    )


def test_history_partial_for_from_start_intent_that_fell_back_to_edge() -> None:
    # Wanted the beginning, fell back to the edge: earlier history is missing, so
    # it is a partial-history condition, not the intended from_edge label.
    assert (
        resolve_history(
            start_intent="from_start",
            capture_origin="live_edge",
            media_history_complete=False,
            chat_covered_from_start=False,
        )
        == "partial"
    )
