from __future__ import annotations

import asyncio

import anyio
import pytest

from app.http_bandwidth import BandwidthPacingMiddleware, DeliveryPolicy
from app.services.bandwidth import BandwidthLimiter


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


@pytest.mark.anyio
async def test_shared_household_bucket_limits_members_without_request_bursts() -> None:
    time = FakeTime()
    limiter = BandwidthLimiter(burst_bytes=10, clock=time.clock, sleep=time.sleep)

    await limiter.acquire(user_id="one", household_bps=80, member_bps=0, byte_count=10)
    await limiter.acquire(user_id="two", household_bps=80, member_bps=0, byte_count=10)

    assert time.sleeps == [1.0]


@pytest.mark.anyio
async def test_rate_update_preserves_spent_allowance() -> None:
    time = FakeTime()
    limiter = BandwidthLimiter(burst_bytes=10, clock=time.clock, sleep=time.sleep)

    await limiter.acquire(user_id="member", household_bps=80, member_bps=0, byte_count=10)
    await limiter.acquire(user_id="member", household_bps=800, member_bps=0, byte_count=10)

    # A change to a faster cap refills from elapsed time only; it does not
    # grant another ten-byte burst.
    assert time.sleeps == [0.1]


@pytest.mark.anyio
async def test_combined_member_and_household_take_uses_the_stricter_wait() -> None:
    time = FakeTime()
    limiter = BandwidthLimiter(burst_bytes=10, clock=time.clock, sleep=time.sleep)

    await limiter.acquire(user_id="member", household_bps=800, member_bps=80, byte_count=10)
    await limiter.acquire(user_id="member", household_bps=800, member_bps=80, byte_count=10)

    assert time.sleeps == [1.0]


@pytest.mark.anyio
async def test_middleware_paces_and_splits_authenticated_remote_body() -> None:
    time = FakeTime()
    limiter = BandwidthLimiter(burst_bytes=4, clock=time.clock, sleep=time.sleep)

    async def app(scope, receive, send):  # noqa: ANN001
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"abcdefghij", "more_body": False})

    async def policy_loader(scope, user_id):  # noqa: ANN001
        assert user_id == "member"
        return DeliveryPolicy(household_bps=80, member_bps=0)

    middleware = BandwidthPacingMiddleware(app, limiter=limiter, policy_loader=policy_loader)
    sent: list[dict] = []

    async def capture(message):  # noqa: ANN001
        sent.append(message)

    async def receive():  # noqa: ANN001
        await anyio.sleep_forever()

    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/media",
            "headers": [],
            "state": {"resource_user_id": "member"},
            "extensions": {"http.response.pathsend": {}},
        },
        receive,
        capture,
    )

    bodies = [message["body"] for message in sent if message["type"] == "http.response.body"]
    assert bodies == [b"abcd", b"efgh", b"ij"]
    assert b"".join(bodies) == b"abcdefghij"
    assert sum(time.sleeps) == pytest.approx(0.6)
    headers = sent[0]["headers"]
    assert (b"x-accel-buffering", b"no") in headers


@pytest.mark.anyio
async def test_disconnect_stops_a_paced_full_body_and_runs_cleanup() -> None:
    disconnected = anyio.Event()
    closed = []
    sent = []

    async def app(scope, receive, send):
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"x" * 100, "more_body": False})
        finally:
            closed.append(True)

    async def policy(scope, user):
        return DeliveryPolicy(8, 0)

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            sent.append(message["body"])
            disconnected.set()

    middleware = BandwidthPacingMiddleware(app, limiter=BandwidthLimiter(burst_bytes=4), policy_loader=policy)
    with anyio.fail_after(1):
        await middleware({"type": "http", "method": "GET", "headers": [], "state": {"resource_user_id": "member"}}, receive, send)
    assert sent == [b"xxxx"] and closed == [True]


@pytest.mark.anyio
async def test_active_policy_change_interrupts_a_wait_without_renewing_the_burst() -> None:
    time = FakeTime()
    limiter = BandwidthLimiter(burst_bytes=4, clock=time.clock, sleep=time.sleep)
    await limiter.acquire(user_id="member", household_bps=80, member_bps=0, byte_count=4)

    async def current_rates():
        return (80 if time.now < 0.25 else 8, 0)

    await limiter.acquire(user_id="member", household_bps=80, member_bps=0, byte_count=4, current_rates=current_rates)
    assert time.now >= 3.5
    assert max(time.sleeps) <= 0.25


@pytest.mark.anyio
@pytest.mark.parametrize("ranged", [False, True])
async def test_paced_media_disconnect_has_one_receive_owner_and_closes_file(tmp_path, monkeypatch, ranged) -> None:
    from app.services import media_response
    media = tmp_path / "synthetic.bin"
    media.write_bytes(b"x" * (2 * 1024 * 1024))
    disconnected = anyio.Event()
    sent = []
    readers = 0
    maximum_readers = 0
    closed = []

    class Response(media_response.MediaFileResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                closed.append(True)

    async def policy(scope, user):
        return DeliveryPolicy(128_000, 0)

    async def receive():
        nonlocal readers, maximum_readers
        readers += 1
        maximum_readers = max(maximum_readers, readers)
        try:
            await disconnected.wait()
            return {"type": "http.disconnect"}
        finally:
            readers -= 1

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sent.append(message["body"])
            disconnected.set()

    headers = [(b"range", b"bytes=0-")] if ranged else []
    wrapped = BandwidthPacingMiddleware(Response(media), policy_loader=policy)
    with anyio.fail_after(1):
        await wrapped({"type": "http", "method": "GET", "headers": headers, "state": {"resource_user_id": "member"},
                       "asgi": {"spec_version": "2.4"}}, receive, send)
    assert maximum_readers == 1 and readers == 0 and closed == [True]
    assert 0 < sum(map(len, sent)) <= 32768


@pytest.mark.anyio
async def test_middleware_skips_policy_loading_for_unauthenticated_response() -> None:
    async def app(scope, receive, send):  # noqa: ANN001
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def policy_loader(scope, user_id):  # noqa: ANN001
        raise AssertionError("unauthenticated responses must not read resource policy")

    middleware = BandwidthPacingMiddleware(app, policy_loader=policy_loader)
    sent: list[dict] = []

    async def capture(message):  # noqa: ANN001
        sent.append(message)

    async def receive():  # noqa: ANN001
        return {"type": "http.disconnect"}

    await middleware({"type": "http", "method": "GET", "path": "/health", "headers": [], "state": {}}, receive, capture)

    assert [message["body"] for message in sent if message["type"] == "http.response.body"] == [b"ok"]
