"""GET /api/channels/youtube/{id} and POST /api/channels/resolve."""
from __future__ import annotations

import socket
from datetime import datetime

import pytest
import yt_dlp

import app.main as main
from app.models import LibraryItem, SourceAutomation
from app.services.network_policy import PublicSourcePolicyError
from app.services.youtube_channels import channel_pages
from app.services.yt_dlp_service import SearchBusyError
from support import make_user

CID = "UCabcdefghijklmnopqrstuv"
ALICE, BOB = make_user("alice"), make_user("bob")


def _public_resolver(host, port, *_args):  # noqa: ANN001, ANN202
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]


def root(**fields) -> dict:  # noqa: ANN003
    return {
        "channel_id": CID, "channel": "Harbor Films", "uploader_id": "@harborfilms", "channel_follower_count": 1200,
        "thumbnails": [{"url": "https://yt3.googleusercontent.com/a=s900", "width": 900, "height": 900}],
        "entries": [{"url": f"https://www.youtube.com/channel/{CID}/{tab}"} for tab in ("videos", "streams", "playlists")],
        **fields,
    }


def listing(count: int, **fields) -> dict:  # noqa: ANN003
    return {"entries": [{"id": f"v{n:010d}", "title": f"Film {n}", "url": f"https://www.youtube.com/watch?v=v{n:010d}",
                         "thumbnails": [{"url": f"https://i.ytimg.com/vi/v{n:010d}/hqdefault.jpg", "width": 480, "height": 360}], **fields}
                        for n in range(count)]}


@pytest.fixture
def extracted(monkeypatch):  # noqa: ANN001, ANN201
    """The route over a fake extractor; `.calls` records every (url, limit)."""
    calls: list[tuple[str, int]] = []
    behaviour: dict[str, object] = {}

    def extract(url: str, limit: int) -> dict:
        calls.append((url, limit))
        outcome = behaviour.get(url.rsplit("/", 1)[-1]) or behaviour.get("*")
        if isinstance(outcome, BaseException):
            raise outcome
        if url.endswith(CID) or "@" in url:
            return root()
        if url.endswith("/streams"):
            return listing(2, live_status="is_live", concurrent_view_count=12412)
        return listing(min(limit, 130))

    pages = channel_pages(extract)
    monkeypatch.setattr(main, "channel_pages", pages)
    monkeypatch.setattr(main.artwork._public_source_policy, "_resolver", _public_resolver)
    extract.calls, extract.behaviour = calls, behaviour  # type: ignore[attr-defined]
    yield extract
    pages.close()


def test_serves_a_channel_page_with_proxied_art_and_annotated_entries(extracted, api_client, db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add(LibraryItem(id="saved-1", user_id=ALICE.id, visibility="shared", extractor="youtube", remote_id="v0000000003", title="x", created_at=datetime(2026, 9, 1)))
        session.commit()
    body = api_client(user=ALICE, base_url="http://localhost").get(f"/api/channels/youtube/{CID}").json()
    assert body["channel"]["name"] == "Harbor Films" and body["channel"]["handle"] == "@harborfilms"
    assert body["channel"]["tabs"] == ["videos", "streams", "playlists"]
    assert body["channel"]["avatar_url"].startswith("/api/artwork/remote/")
    assert len(body["entries"]) == 60 and body["has_more"] is True and body["tab"] == "videos"
    assert all(entry["artwork_url"].startswith("/api/artwork/remote/") for entry in body["entries"])
    assert body["entries"][3]["saved_item_id"] == "saved-1" and body["stale"] is False
    assert body["channel"]["follow_id"] is None and body["channel"]["live"] is None


def test_a_streams_tab_counts_viewers_and_a_second_page_asks_for_120(extracted, api_client) -> None:  # noqa: ANN001
    client = api_client(user=ALICE, base_url="http://localhost")
    streams = client.get(f"/api/channels/youtube/{CID}?tab=streams").json()
    assert streams["entries"][0]["view_count"] == 12412 and streams["entries"][0]["capabilities"]["lifecycle"] == "live"
    more = client.get(f"/api/channels/youtube/{CID}?limit=120").json()
    assert len(more["entries"]) == 120 and (f"https://www.youtube.com/channel/{CID}/videos", 121) in extracted.calls
    # With the streams tab cached, an unfollowed channel's live band comes from it.
    assert client.get(f"/api/channels/youtube/{CID}").json()["channel"]["live"]["view_count"] == 12412


@pytest.mark.parametrize(("path", "status"), [
    (f"/api/channels/youtube/{CID[:-1]}", 404), (f"/api/channels/youtube/{CID}x", 404), ("/api/channels/youtube/@harborfilms", 404),
    (f"/api/channels/youtube/{CID}?tab=community", 422), (f"/api/channels/youtube/{CID}?limit=500", 422), (f"/api/channels/youtube/{CID}?limit=abc", 422),
])
def test_rejects_a_malformed_id_or_tab_without_extracting(extracted, api_client, path: str, status: int) -> None:  # noqa: ANN001
    assert api_client(user=ALICE, base_url="http://localhost").get(path).status_code == status
    assert extracted.calls == []


def test_follow_id_is_the_member_s_own_follow_by_id_or_handle(extracted, api_client, db_factory) -> None:  # noqa: ANN001
    at = datetime(2026, 9, 1)
    with db_factory() as session:
        for follow_id, user, url in (("bob-f", BOB, f"https://www.youtube.com/channel/{CID}"), ("alice-f", ALICE, "https://www.youtube.com/@HarborFilms")):
            session.add(SourceAutomation(
                id=follow_id, user_id=user.id, label="Harbor", source_url=url, source_type="channel", cron_expression="0 */6 * * *",
                active=True, auto_download=False, format_selection={}, output_profile={}, rules={}, duplicate_policy="skip_same_source",
                last_run_summary={}, feed_entries=[], created_at=at, updated_at=at,
            ))
        session.commit()

    class Checker:
        def live_entries(self, urls):  # noqa: ANN001, ANN202
            return []

    main_checker = main.followed_live_checker
    try:
        main.followed_live_checker = Checker()
        body = api_client(user=ALICE, base_url="http://localhost").get(f"/api/channels/youtube/{CID}").json()
    finally:
        main.followed_live_checker = main_checker
    assert body["channel"]["follow_id"] == "alice-f"


@pytest.mark.parametrize(("error", "status", "detail"), [
    (yt_dlp.utils.DownloadError("ERROR: This account has been terminated"), 404, "channel_unavailable"),
    (yt_dlp.utils.DownloadError("ERROR: Unable to download API page: HTTP Error 500"), 502, None),
    (SearchBusyError("busy"), 503, "busy"),
])
def test_maps_extractor_errors(extracted, api_client, error: BaseException, status: int, detail: str | None) -> None:  # noqa: ANN001
    extracted.behaviour["*"] = error
    response = api_client(user=ALICE, base_url="http://localhost").get(f"/api/channels/youtube/{CID}")
    assert response.status_code == status
    if detail:
        assert response.json()["detail"] == detail


def test_a_restricted_tab_is_an_empty_200(extracted, api_client) -> None:  # noqa: ANN001
    extracted.behaviour["playlists"] = yt_dlp.utils.DownloadError("ERROR: Join this channel to get access to members-only content")
    body = api_client(user=ALICE, base_url="http://localhost").get(f"/api/channels/youtube/{CID}?tab=playlists").json()
    assert body["restricted"] is True and body["entries"] == []


def test_a_slow_youtube_is_a_504(monkeypatch, api_client) -> None:  # noqa: ANN001
    from app.services.youtube_channels import ChannelTimeout

    class Slow:
        def page(self, *args):  # noqa: ANN002, ANN202
            raise ChannelTimeout(CID)

    monkeypatch.setattr(main, "channel_pages", Slow())
    response = api_client(user=ALICE, base_url="http://localhost").get(f"/api/channels/youtube/{CID}")
    assert (response.status_code, response.json()["detail"]) == (504, "YouTube did not respond in time.")


def test_resolves_a_canonical_address_locally_and_a_handle_by_one_extraction(extracted, api_client) -> None:  # noqa: ANN001
    client = api_client(user=ALICE, base_url="http://localhost")
    local = client.post("/api/channels/resolve", json={"url": f"https://m.youtube.com/channel/{CID}/videos"})
    assert local.json() == {"provider": "youtube", "channel_id": CID} and extracted.calls == []
    handle = client.post("/api/channels/resolve", json={"url": "https://www.youtube.com/@harborfilms/streams"})
    assert handle.json()["channel_id"] == CID and extracted.calls == [("https://www.youtube.com/@harborfilms", 10)]
    client.get(f"/api/channels/youtube/{CID}")  # the header was warmed by the resolution
    assert [url for url, _ in extracted.calls] == ["https://www.youtube.com/@harborfilms", f"https://www.youtube.com/channel/{CID}/videos"]


@pytest.mark.parametrize("url", ["http://www.youtube.com/@x", "https://evil.com/@x", "https://www.youtube.com/watch?v=abc", "https://www.youtube.com/"])
def test_refuses_what_is_not_a_youtube_channel_address(extracted, api_client, url: str) -> None:  # noqa: ANN001
    response = api_client(user=ALICE, base_url="http://localhost").post("/api/channels/resolve", json={"url": url})
    assert (response.status_code, response.json()["detail"]) == (400, "Paste a YouTube channel address.")
    assert extracted.calls == []


def test_resolve_errors(extracted, api_client) -> None:  # noqa: ANN001
    client = api_client(user=ALICE, base_url="http://localhost")
    extracted.behaviour["@gone"] = yt_dlp.utils.DownloadError("ERROR: This channel does not exist.")
    gone = client.post("/api/channels/resolve", json={"url": "https://www.youtube.com/@gone"})
    assert (gone.status_code, gone.json()["detail"]) == (404, "channel_unavailable")
    assert client.post("/api/channels/resolve", json={"url": "x" * 2049}).status_code == 422


def test_a_dns_or_network_failure_is_a_retryable_503_on_both_routes(extracted, api_client) -> None:  # noqa: ANN001
    client = api_client(user=ALICE, base_url="http://localhost")
    extracted.behaviour["*"] = PublicSourcePolicyError()  # what a failed resolution looks like to the policy
    page = client.get(f"/api/channels/youtube/{CID}")
    resolve = client.post("/api/channels/resolve", json={"url": "https://www.youtube.com/@harborfilms"})
    assert (page.status_code, resolve.status_code) == (503, 503)
    assert "not allowed" not in page.text and "Retry-After" in page.headers and "Retry-After" in resolve.headers


def test_channel_pages_never_spend_the_preview_budget(extracted, api_client) -> None:  # noqa: ANN001
    from app.services.rate_limit import rate_limiter

    client = api_client(user=ALICE, base_url="http://localhost")
    for _ in range(25):
        assert client.get(f"/api/channels/youtube/{CID}").status_code == 200
        assert client.post("/api/channels/resolve", json={"url": "https://www.youtube.com/@harborfilms"}).status_code == 200
    for _ in range(20):
        rate_limiter.check("preview", f"user:{ALICE.id}", user_id=ALICE.id)  # raises once 20 are spent


def test_a_streams_tab_older_than_30_minutes_shows_no_live_state(extracted, api_client, monkeypatch) -> None:  # noqa: ANN001
    import time

    client = api_client(user=ALICE, base_url="http://localhost")
    client.get(f"/api/channels/youtube/{CID}?tab=streams")
    assert client.get(f"/api/channels/youtube/{CID}").json()["channel"]["live"] is not None
    monkeypatch.setattr(main.channel_pages, "_clock", lambda: time.time() + 31 * 60)
    assert client.get(f"/api/channels/youtube/{CID}").json()["channel"]["live"] is None
