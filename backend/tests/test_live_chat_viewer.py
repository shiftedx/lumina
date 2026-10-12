import json

from fastapi.testclient import TestClient

from app.main import app

from app.services.live_chat_seam import LiveChatBootstrap, LiveChatPage
from app.services.live_chat_viewer import LiveChatViewer
from app.services.remote_streaming_adapters import YtDlpLiveChatFetcher


def _action(n: int) -> dict:
    return {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "id": f"m{n}", "message": {"runs": [{"text": f"hello {n}"}]}, "authorName": {"simpleText": f"viewer{n}"},
        "authorExternalChannelId": f"UC{n}",
    }}}}


class FakeFetcher:
    def __init__(self, pages: list[list[int]] | None = None, available: bool = True) -> None:
        self.pages = pages or []
        self.available = available
        self.boots = 0
        self.fetches = 0

    def bootstrap(self, source_url, owner_user_id):  # noqa: ANN001
        self.boots += 1
        if not self.available:
            return LiveChatBootstrap(continuation=None, status="unavailable")
        return LiveChatBootstrap(continuation="c0", headers={"h": "1"})

    def fetch_page(self, continuation, *, headers):  # noqa: ANN001
        self.fetches += 1
        if not self.pages:
            raise RuntimeError("upstream down")
        return LiveChatPage(actions=[_action(n) for n in self.pages.pop(0)], continuation=f"c{self.fetches}")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_viewers_share_one_poll_and_read_from_their_cursor() -> None:
    fetcher, clock = FakeFetcher([[1, 2], [2, 3]]), Clock()
    viewer = LiveChatViewer(fetcher, clock=clock, poll_interval_seconds=2)

    first = viewer.read("https://www.youtube.com/watch?v=aaaaaaaaaaa", "u1")
    assert first["status"] == "active" and [e["text"] for e in first["events"]] == ["hello 1", "hello 2"]
    assert first["cursor"] == 2 and "continuation" not in json.dumps(first)

    # A second member within the poll interval shares the buffer: no new upstream call.
    second = viewer.read("https://www.youtube.com/watch?v=aaaaaaaaaaa", "u2")
    assert fetcher.boots == 1 and fetcher.fetches == 1 and len(second["events"]) == 2

    clock.now = 2.5  # due: one fetch; the repeated id 2 is not sent twice
    later = viewer.read("https://www.youtube.com/watch?v=aaaaaaaaaaa", "u1", after=first["cursor"])
    assert [e["text"] for e in later["events"]] == ["hello 3"] and later["cursor"] == 3 and fetcher.fetches == 2


def test_unavailable_chat_and_upstream_failures_end_quietly() -> None:
    assert LiveChatViewer(FakeFetcher(available=False)).read("u", "u1")["status"] == "unavailable"

    fetcher, clock = FakeFetcher([[1]]), Clock()
    viewer = LiveChatViewer(fetcher, clock=clock, poll_interval_seconds=1, max_consecutive_failures=2)
    viewer.read("s", "u1")
    for step in (1, 2):
        clock.now = step * 1.5
        result = viewer.read("s", "u1", after=1)
    assert result["status"] == "ended" and result["events"] == []


def test_a_source_nobody_reads_is_forgotten() -> None:
    fetcher, clock = FakeFetcher([[1], [2]]), Clock()
    viewer = LiveChatViewer(fetcher, clock=clock, idle_seconds=60)
    viewer.read("s", "u1")
    clock.now = 61
    viewer.read("other", "u1")  # any read sweeps idle sources
    assert "s" not in viewer._sources


def test_fetcher_reads_the_popout_page_and_posts_the_continuation(monkeypatch) -> None:
    calls: list[tuple[str, bytes | None]] = []
    popout = b'..."invalidationContinuationData":{"invalidationId":{"x":1},"timeoutMs":10000,"continuation":"TOKEN0"}...'
    popout = popout.replace(b'{"x":1}', b'"x"') + b'"INNERTUBE_CLIENT_VERSION":"2.20261002.01.00"'
    api = {"continuationContents": {"liveChatContinuation": {
        "actions": [_action(1)], "continuations": [{"invalidationContinuationData": {"continuation": "TOKEN1"}}]}}}

    def read(self, url, *, headers, data):  # noqa: ANN001
        calls.append((url, data))
        return popout if data is None else json.dumps(api).encode()

    monkeypatch.setattr(YtDlpLiveChatFetcher, "_read", read)
    fetcher = YtDlpLiveChatFetcher()
    assert fetcher.bootstrap("https://www.youtube.com/watch?v=x", "u").status == "unavailable" and calls == []

    boot = fetcher.bootstrap("https://www.youtube.com/watch?v=IIMr8iqlF4M", "u")
    assert boot.status == "available" and calls[0][0].endswith("v=IIMr8iqlF4M")
    page = fetcher.fetch_page(boot.continuation, headers=boot.headers)
    body = json.loads(calls[1][1])
    assert body["continuation"] == "TOKEN0" and body["context"]["client"]["clientVersion"] == "2.20261002.01.00"
    assert page.status == "active" and len(page.actions) == 1 and page.continuation.endswith("continuation=TOKEN1")


def test_the_live_chat_route_needs_a_signed_in_member() -> None:
    client = TestClient(app, base_url="http://localhost")
    assert client.get("/api/live-chat", params={"source_url": "https://www.youtube.com/watch?v=IIMr8iqlF4M"}).status_code == 401
