"""Shared, monotonic token accounting for outbound household delivery.

The limiter deliberately owns no request state.  HTTP code supplies the current
household and member rates for each chunk, which lets a changed setting affect
an already-open transfer without giving that transfer a new burst.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable


DEFAULT_BURST_BYTES = 32 * 1024


@dataclass
class _Bucket:
    rate_bps: int
    tokens: float
    updated_at: float


class BandwidthLimiter:
    """Atomically pace the household and one member bucket together.

    Rates are stored in bits/second at the policy boundary.  A bucket is
    persistent for the process lifetime, so opening another request cannot
    obtain another initial allowance.  This is intentionally process-local:
    ADR 0006 defines Lumina's supported topology as one server process.
    """

    def __init__(
        self,
        *,
        burst_bytes: int = DEFAULT_BURST_BYTES,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if burst_bytes <= 0:
            raise ValueError("burst_bytes must be positive")
        self.burst_bytes = burst_bytes
        self._clock = clock
        self._sleep = sleep
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        """Drop process-local accounting; primarily useful for isolated tests."""
        self._buckets.clear()

    def _refill(self, bucket: _Bucket, now: float, rate_bps: int) -> None:
        elapsed = max(0.0, now - bucket.updated_at)
        # Retaining the current balance is what prevents a policy update from
        # resetting a busy transfer to a full burst.
        bucket.tokens = min(
            float(self.burst_bytes),
            bucket.tokens + elapsed * (rate_bps / 8),
        )
        bucket.rate_bps = rate_bps
        bucket.updated_at = now

    def _bucket(self, key: str, rate_bps: int, now: float) -> _Bucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(rate_bps=rate_bps, tokens=float(self.burst_bytes), updated_at=now)
            self._buckets[key] = bucket
        else:
            self._refill(bucket, now, rate_bps)
        return bucket

    async def acquire(
        self,
        *,
        user_id: str,
        household_bps: int,
        member_bps: int,
        byte_count: int,
        current_rates: Callable[[], Awaitable[tuple[int, int] | None]] | None = None,
    ) -> None:
        """Wait until ``byte_count`` may leave under both finite caps.

        A zero member allowance is unlimited.  The household allowance is
        expected to be positive when this method is called.  Waiting happens
        after releasing the accounting lock so unrelated connections can make
        progress and task cancellation remains prompt.
        """
        if byte_count <= 0:
            return
        if household_bps <= 0:
            return
        if byte_count > self.burst_bytes:
            raise ValueError("byte_count must not exceed burst_bytes")

        while True:
            if current_rates is not None:
                rates = await current_rates()
                if rates is None:
                    return
                household_bps, member_bps = rates
            async with self._lock:
                now = self._clock()
                buckets = [self._bucket("household", household_bps, now)]
                if member_bps > 0:
                    buckets.append(self._bucket(f"member:{user_id}", member_bps, now))

                shortages = [byte_count - bucket.tokens for bucket in buckets if bucket.tokens < byte_count]
                if not shortages:
                    for bucket in buckets:
                        bucket.tokens -= byte_count
                    return

                waits = [
                    shortage / (bucket.rate_bps / 8)
                    for bucket in buckets
                    for shortage in [byte_count - bucket.tokens]
                    if shortage > 0
                ]
                delay = max(waits)
            await self._sleep(min(delay, 0.25) if current_rates is not None else delay)
