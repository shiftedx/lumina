"""Small in-process admission registry for protected local playback."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.models import User
from app.services.playback_decision import ClientCaps, Decision, Limits, decide
from app.services.resource_policy import effective, household, remote, runtime_state

IDLE_SECONDS = 600
DELIVERY_HEADROOM_BPS = 500_000
MIN_STREAM_BPS = 2_000_000


@dataclass(frozen=True)
class Admission:
    key: tuple[str, str, str, str]
    decision: Decision
    protected: bool
    remote: bool
    allocation_bps: int | None = None
    token: str = ""


@dataclass
class _Reservation:
    admission: Admission
    video_encode: bool
    touched: float
    previous: list[Admission]


class PlaybackResources:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str, str], _Reservation] = {}
        self._lock = threading.Lock()

    def enabled(self, db: Session) -> bool:
        return runtime_state()[0] and household(db).upload_bps is not None

    def preview(
        self, db: Session, user: User, request: Request, item_id: str, device: str,
        facts: dict[str, Any], caps: ClientCaps, *, max_height: int | None = None,
        max_bitrate: int | None = None, audio_index: int | None = None, subtitle_index: int | None = None,
        logical_id: str | tuple[str, ...] | None = None,
    ) -> Admission:
        policy = household(db)
        if policy.upload_bps is None:
            return Admission((user.id, device, item_id, "legacy"), decide(caps, facts, Limits(max_height, max_bitrate), audio_index, subtitle_index), False, False)
        is_remote = remote(request, policy)
        allowance = effective(db, user.id)
        # LAN playback has the member slots but no upload allocation. Remote bytes
        # retain enough room for encoded audio, muxing, and the delivery bucket.
        allocation: int | None = None
        bitrate = max_bitrate
        if is_remote:
            with self._lock:
                self._reap_locked()
                logical_ids = (logical_id,) if isinstance(logical_id, str) else logical_id or ()
                mine = {(user.id, device, item_id, value) for value in logical_ids}
                member_used = sum(r.admission.allocation_bps or 0 for r in self._items.values() if r.admission.remote and r.admission.key[0] == user.id and r.admission.key not in mine)
                household_used = sum(r.admission.allocation_bps or 0 for r in self._items.values() if r.admission.remote and r.admission.key not in mine)
            member_left = max(0, allowance.remote_member_bps - member_used) if allowance.remote_member_bps else None
            house_left = max(0, policy.upload_bps - household_used)
            allocation = self._allocation(allowance.remote_stream_bps, member_left, house_left)
            bitrate = self._delivery_bitrate(allocation, bitrate)
        decision = self._decision(facts, caps, max_height, bitrate, audio_index, subtitle_index, is_remote and allocation is not None)
        if decision.mode == "unavailable":
            raise HTTPException(409, "This source cannot meet the household playback limit.")
        return Admission((user.id, device, item_id, ""), decision, True, is_remote, allocation)

    def admit(self, db: Session, user: User, request: Request, item_id: str, device: str, logical_id: str,
              facts: dict[str, Any], caps: ClientCaps, **kwargs: Any) -> Admission:
        key = (user.id, device, item_id, logical_id)
        # A range retry or a repeat HLS master request keeps its allocation, but
        # still re-evaluates changed quality, audio, and subtitle choices.
        with self._lock:
            self._reap_locked()
            existing = self._items.get(key)
        # Policy reads are deliberately outside the admission lock; the lock only
        # serializes the small in-memory reservation commit below.
        policy = household(db)
        if policy.upload_bps is None:
            return Admission(key, decide(caps, facts, Limits(kwargs.get("max_height"), kwargs.get("max_bitrate")), kwargs.get("audio_index"), kwargs.get("subtitle_index")), False, False)
        is_remote = remote(request, policy)
        allowance = effective(db, user.id)
        with self._lock:
            self._reap_locked()
            existing = self._items.get(key)
            if existing is not None:
                other_member = [r for r in self._items.values() if r.admission.key[0] == user.id and r is not existing]
                allocation = existing.admission.allocation_bps if existing.admission.remote and is_remote else None
                if is_remote and allocation is None:
                    used_member = sum(r.admission.allocation_bps or 0 for r in other_member if r.admission.remote)
                    used_house = sum(r.admission.allocation_bps or 0 for r in self._items.values() if r is not existing and r.admission.remote)
                    member_left = max(0, allowance.remote_member_bps - used_member) if allowance.remote_member_bps else None
                    allocation = self._allocation(allowance.remote_stream_bps, member_left, max(0, policy.upload_bps - used_house))
                ceilings = [value for value in (allocation, allowance.remote_stream_bps, allowance.remote_member_bps, policy.upload_bps) if value]
                current_ceiling = min(ceilings) if ceilings else allocation
                bitrate = self._delivery_bitrate(current_ceiling, kwargs.get("max_bitrate")) if is_remote and current_ceiling is not None else kwargs.get("max_bitrate")
                decision = self._decision(
                    facts, caps, kwargs.get("max_height"), bitrate, kwargs.get("audio_index"), kwargs.get("subtitle_index"), is_remote, allocation,
                )
                if decision.mode == "unavailable":
                    raise HTTPException(409, "This source cannot meet the household playback limit.")
                if decision.video == "encode" and allowance.video_encodes and sum(r.video_encode for r in other_member) >= allowance.video_encodes:
                    raise HTTPException(429, "Your video encode limit is reached.")
                # A seek may replace the ffmpeg runtime without changing rendition.
                # Every start therefore gets a new owner token, so disposing its
                # predecessor cannot release the current reservation.
                existing.previous.append(existing.admission)
                existing.admission = Admission(key, decision, True, is_remote, allocation, uuid.uuid4().hex)
                existing.video_encode = decision.video == "encode"
                existing.touched = time.monotonic()
                return existing.admission
            member = [r for r in self._items.values() if r.admission.key[0] == user.id]
            if allowance.playback_sessions and len(member) >= allowance.playback_sessions:
                raise HTTPException(429, "Your playback session limit is reached.")
            # Recalculate while holding the lock so simultaneous starts cannot overbook bytes.
            if is_remote:
                used_member = sum(r.admission.allocation_bps or 0 for r in member if r.admission.remote)
                used_house = sum(r.admission.allocation_bps or 0 for r in self._items.values() if r.admission.remote)
                left_member = max(0, allowance.remote_member_bps - used_member) if allowance.remote_member_bps else None
                left_house = max(0, policy.upload_bps - used_house)
                allocation = self._allocation(allowance.remote_stream_bps, left_member, left_house)
                bitrate = self._delivery_bitrate(allocation, kwargs.get("max_bitrate"))
                decision = self._decision(facts, caps, kwargs.get("max_height"), bitrate, kwargs.get("audio_index"), kwargs.get("subtitle_index"), True)
                preview = Admission(key, decision, True, True, allocation, uuid.uuid4().hex)
            else:
                decision = decide(caps, facts, Limits(kwargs.get("max_height"), kwargs.get("max_bitrate")), kwargs.get("audio_index"), kwargs.get("subtitle_index"))
                preview = Admission(key, decision, True, False, None, uuid.uuid4().hex)
            if preview.decision.mode == "unavailable":
                raise HTTPException(409, "This source cannot meet the household playback limit.")
            if preview.decision.video == "encode" and allowance.video_encodes and sum(r.video_encode for r in member) >= allowance.video_encodes:
                raise HTTPException(429, "Your video encode limit is reached.")
            self._items[key] = _Reservation(preview, preview.decision.video == "encode", time.monotonic(), [])
        return preview

    def admit_direct(
        self, db: Session, user: User, request: Request, item_id: str, device: str,
        facts: Any, caps: ClientCaps,
    ) -> Admission | None:
        """Lazy direct-file guard for the PM-owned route; disabled policy does no probe."""
        if not self.enabled(db):
            return None
        loaded = facts() if callable(facts) else facts
        admission = self.admit(db, user, request, item_id, device, "direct", loaded, caps)
        if admission.decision.mode == "direct":
            self.commit(admission)
        else:
            self.restore(admission)
        return admission

    def touch(self, admission: Admission | None) -> None:
        if admission is None:
            return
        with self._lock:
            if found := self._items.get(admission.key):
                found.touched = time.monotonic()

    def release(self, admission: Admission | None) -> None:
        if admission and admission.protected:
            with self._lock:
                current = self._items.get(admission.key)
                if current is not None and current.admission.token == admission.token:
                    self._items.pop(admission.key, None)

    def restore(self, admission: Admission | None) -> None:
        """Keep a superseded runtime's allocation if its replacement fails to start."""
        if admission is None:
            return
        with self._lock:
            current = self._items.get(admission.key)
            if current is None:
                return
            if current.admission.token != admission.token:
                current.previous = [prior for prior in current.previous if prior.token != admission.token]
                return
            if not current.previous:
                self._items.pop(admission.key, None)
            else:
                current.admission = current.previous.pop()
                current.video_encode = current.admission.decision.video == "encode"

    def commit(self, admission: Admission | None) -> None:
        """A started/reused runtime owns the reservation; old failed starts no longer matter."""
        if admission is None:
            return
        with self._lock:
            current = self._items.get(admission.key)
            if current is not None and current.admission.token == admission.token:
                current.previous.clear()

    def release_direct(self, user_id: str, item_id: str, device: str) -> None:
        with self._lock:
            self._items.pop((user_id, device, item_id, "direct"), None)

    def touch_direct(self, user_id: str, item_id: str, device: str) -> None:
        with self._lock:
            reservation = self._items.get((user_id, device, item_id, "direct"))
            if reservation is not None:
                reservation.touched = time.monotonic()

    def reap(self) -> None:
        with self._lock:
            self._reap_locked()

    @staticmethod
    def _allocation(stream_limit: int, member_left: int | None, house_left: int) -> int:
        """Return the tightest finite limit; zero remaining is never unlimited."""
        limits = [house_left]
        if stream_limit:
            limits.append(stream_limit)
        if member_left is not None:
            limits.append(member_left)
        allocation = min(limits)
        if allocation < MIN_STREAM_BPS:
            raise HTTPException(429, "Household playback bandwidth is busy. Try again shortly.")
        return allocation

    @staticmethod
    def _delivery_bitrate(allocation: int, requested: int | None) -> int:
        usable = allocation - DELIVERY_HEADROOM_BPS
        if usable < 1_500_000:
            raise HTTPException(429, "Household playback bandwidth is busy. Try again shortly.")
        return min(requested, usable) if requested else usable

    @staticmethod
    def _audio_budget(facts: dict[str, Any], audio_index: int | None) -> int:
        audio = next((stream for stream in facts.get("streams") or [] if stream.get("type") == "audio" and (audio_index is None or stream.get("index") == audio_index)), None)
        return 384_000 if audio is not None and (audio.get("channels") or 2) > 2 else 160_000

    def _decision(
        self, facts: dict[str, Any], caps: ClientCaps, max_height: int | None, bitrate: int | None,
        audio_index: int | None, subtitle_index: int | None, protected_remote: bool, allocation: int | None = None,
    ) -> Decision:
        limits = Limits(max_height=max_height, max_bitrate=bitrate, require_known_bitrate=protected_remote)
        decision = decide(caps, facts, limits, audio_index, subtitle_index)
        if decision.mode == "direct" or not protected_remote:
            return decision
        # A transcode/remux cannot carry unknown lossless audio under a video-only
        # maxrate. Re-run it with known AAC and reserve that output separately.
        audio_budget = self._audio_budget(facts, audio_index)
        output_bitrate = (bitrate or 0) - audio_budget
        if allocation is not None:
            output_bitrate = min(output_bitrate, allocation - DELIVERY_HEADROOM_BPS - audio_budget)
        if output_bitrate < 1_500_000:
            raise HTTPException(429, "Household playback bandwidth is busy. Try again shortly.")
        return decide(caps, facts, Limits(max_height=max_height, max_bitrate=output_bitrate, require_known_bitrate=True, force_audio_encode=True), audio_index, subtitle_index)

    def _reap_locked(self) -> None:
        deadline = time.monotonic() - IDLE_SECONDS
        for key in [key for key, value in self._items.items() if value.touched < deadline]:
            self._items.pop(key, None)


playback_resources = PlaybackResources()
