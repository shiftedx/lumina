"""The surviving live-chat seam of the 1.0 MVP cut.

The generic public-edge live-chat family (session store, /api/live-chat
routes, the viewing rail) was cut in the 1.0 MVP. What survives is the
recording chat-capture seam: a guarded capturer pulls the current
live-edge continuation from a provider through this fetcher contract and
publishes a durable timed chat asset readable through the chat-replay
path. These shapes are the whole public surface of that seam: normalized
events, opaque continuations, and server-side request headers only —
never continuations, cookies, or upstream addresses to the browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class LiveChatBootstrap:
    """The provider's live-edge entry point for a source's current chat.

    ``continuation`` is the opaque token for the current live edge (never a
    from-the-beginning token) and ``headers`` are the server-side request
    headers to apply when polling. Neither ever surfaces to the browser.
    ``status`` is ``available`` when the source has current chat, or
    ``unavailable`` when chat is disabled or absent.
    """

    continuation: str | None
    headers: Mapping[str, str] = field(default_factory=dict)
    status: str = "available"


@dataclass(frozen=True)
class LiveChatPage:
    """One fetched live-chat continuation page.

    ``actions`` are raw innertube-shaped continuation actions;
    ``continuation`` is the next live-edge token (``None`` when the
    broadcast/chat ended); ``status`` is ``active``, ``ended``
    (broadcast finished), or ``disabled`` (chat turned off mid-stream).
    """

    actions: list[Any]
    continuation: str | None
    status: str = "active"


class LiveChatFetcher(Protocol):
    def bootstrap(self, source_url: str, owner_user_id: str) -> LiveChatBootstrap: ...

    def fetch_page(self, continuation: str, *, headers: Mapping[str, str]) -> LiveChatPage: ...
