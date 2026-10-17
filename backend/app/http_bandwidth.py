"""ASGI response-body pacing for authenticated remote household traffic."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import anyio
from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette._utils import create_collapsing_task_group
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import db as db_module
from app.services import resource_policy
from app.services.bandwidth import BandwidthLimiter


MAX_BODY_CHUNK_BYTES = 16 * 1024
POLICY_CACHE_SECONDS = 0.25


@dataclass(frozen=True)
class DeliveryPolicy:
    """The finite caps applicable to one authenticated remote response."""

    household_bps: int
    member_bps: int


PolicyLoader = Callable[[Scope, str], Awaitable[DeliveryPolicy | None]]


class ResourcePolicyCache:
    """Very short cache which keeps policy database reads off the event loop."""

    def __init__(self, *, ttl: float = POLICY_CACHE_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._entries: dict[str, tuple[float, resource_policy.HouseholdResources, resource_policy.ResourceAllowances]] = {}
        self._lock = asyncio.Lock()
        self._generation = -1

    def clear(self) -> None:
        self._entries.clear()

    @staticmethod
    def _load(user_id: str) -> tuple[resource_policy.HouseholdResources, resource_policy.ResourceAllowances]:
        # Referencing SessionLocal through the module makes the test fixture's
        # per-test rebind effective, rather than retaining a stale factory.
        db = db_module.SessionLocal()
        try:
            return resource_policy.household(db), resource_policy.effective(db, user_id)
        finally:
            db.close()

    async def resolve(self, scope: Scope, user_id: str) -> DeliveryPolicy | None:
        enabled, generation = resource_policy.runtime_state()
        if not enabled:
            return None
        now = self._clock()
        async with self._lock:
            if generation != self._generation:
                self._entries.clear()
                self._generation = generation
            entry = self._entries.get(user_id)
            if entry is None or now - entry[0] >= self.ttl:
                household, member = await anyio.to_thread.run_sync(self._load, user_id)
                self._entries[user_id] = (self._clock(), household, member)
            else:
                _, household, member = entry

        # Classification is request-specific, never cached with the member.
        if household.upload_bps is None or not resource_policy.remote(Request(scope), household):
            return None
        return DeliveryPolicy(household_bps=household.upload_bps, member_bps=member.remote_member_bps)


class BandwidthPacingMiddleware:
    """Split and pace ASGI response bodies after authentication has populated scope state."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: BandwidthLimiter | None = None,
        policy_cache: ResourcePolicyCache | None = None,
        policy_loader: PolicyLoader | None = None,
    ) -> None:
        self.app = app
        self.limiter = limiter or BandwidthLimiter()
        self.policy_cache = policy_cache or ResourcePolicyCache()
        self.policy_loader = policy_loader or self.policy_cache.resolve

    def clear(self) -> None:
        self.limiter.clear()
        self.policy_cache.clear()

    @staticmethod
    def _user_id(scope: Scope) -> str | None:
        state = scope.get("state")
        if isinstance(state, dict):
            value = state.get("resource_user_id")
            return value if isinstance(value, str) else None
        return getattr(state, "resource_user_id", None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # FileResponse can select ASGI pathsend before its first body send.
        # Authentication dependencies run inside the app, so remove the
        # zero-copy extensions before dispatch and preserve a single ordinary
        # response-body path where accounting can happen.
        extensions = scope.get("extensions", {})
        if extensions:
            extensions = {
                name: value
                for name, value in extensions.items()
                if name not in {"http.response.pathsend", "http.response.zerocopysend"}
            }
            scope = {**scope, "extensions": extensions}

        user_id: str | None = None
        watching = False
        receive_lock = anyio.Lock()
        disconnected = False

        async def shared_receive() -> Message:
            nonlocal disconnected
            # File/StreamingResponse may also listen. One reader owns the ASGI
            # channel; every consumer observes the terminal disconnect.
            if disconnected:
                return {"type": "http.disconnect"}
            async with receive_lock:
                if disconnected:
                    return {"type": "http.disconnect"}
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected = True
                return message

        async def disconnect() -> None:
            while True:
                if (await shared_receive())["type"] == "http.disconnect":
                    group.cancel_scope.cancel()
                    return
                await anyio.lowlevel.checkpoint()

        def watch_disconnect() -> None:
            nonlocal watching
            if not watching:
                watching = True
                group.start_soon(disconnect)

        async def paced_send(message: Message) -> None:
            nonlocal user_id
            kind = message["type"]
            if kind == "http.response.start":
                user_id = self._user_id(scope)
                if user_id is not None and await self.policy_loader(scope, user_id) is not None:
                    headers = MutableHeaders(raw=message["headers"])
                    headers["x-accel-buffering"] = "no"
                    headers["cache-control"] = "private, no-store"
                    watch_disconnect()
                await send(message)
                return
            if kind != "http.response.body" or user_id is None:
                await send(message)
                return

            body = bytes(message.get("body", b""))
            if not body:
                await send(message)
                return
            admission = scope.get("state", {}).get("playback_admission")
            if admission is not None:
                from app.services.playback_resources import playback_resources
                playback_resources.touch(admission)
            if await self.policy_loader(scope, user_id) is None:
                await send(message)
                return
            watch_disconnect()

            async def current_rates() -> tuple[int, int] | None:
                current = await self.policy_loader(scope, user_id)
                return (current.household_bps, current.member_bps) if current else None

            chunk_size = min(MAX_BODY_CHUNK_BYTES, self.limiter.burst_bytes)
            for offset in range(0, len(body), chunk_size):
                policy = await self.policy_loader(scope, user_id)
                chunk = body[offset:offset + chunk_size]
                last = offset + len(chunk) == len(body)
                if policy is not None:
                    await self.limiter.acquire(
                        user_id=user_id,
                        household_bps=policy.household_bps,
                        member_bps=policy.member_bps,
                        byte_count=len(chunk),
                        current_rates=current_rates,
                    )
                admission = scope.get("state", {}).get("playback_admission")
                if admission is not None:
                    from app.services.playback_resources import playback_resources
                    playback_resources.touch(admission)
                await send({
                    **message,
                    "body": chunk,
                    "more_body": True if not last else message.get("more_body", False),
                })

        async with create_collapsing_task_group() as group:
            await self.app(scope, shared_receive, paced_send)
            group.cancel_scope.cancel()
