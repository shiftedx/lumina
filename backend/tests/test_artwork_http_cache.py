from starlette.requests import Request

from app.main import _library_artwork_response, _remote_artwork_response


def request_with_headers(*, if_none_match: str | None = None) -> Request:
    headers = [] if if_none_match is None else [(b"if-none-match", if_none_match.encode("ascii"))]
    return Request({"type": "http", "method": "GET", "path": "/art", "headers": headers})


def test_remote_artwork_is_private_browser_cacheable_and_revalidates() -> None:
    first = _remote_artwork_response(request_with_headers(), "image/jpeg", b"cached-thumbnail")

    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, max-age=3600, stale-while-revalidate=21600"
    assert first.headers["vary"] == "Cookie, Authorization"
    assert first.headers["etag"].startswith('"')

    revalidated = _remote_artwork_response(
        request_with_headers(if_none_match=first.headers["etag"]),
        "image/jpeg",
        b"cached-thumbnail",
    )

    assert revalidated.status_code == 304
    assert revalidated.body == b""
    assert revalidated.headers["etag"] == first.headers["etag"]

    weak_list = _remote_artwork_response(
        request_with_headers(if_none_match=f'"other", W/{first.headers["etag"]}'),
        "image/jpeg",
        b"cached-thumbnail",
    )
    assert weak_list.status_code == 304

    wildcard = _remote_artwork_response(
        request_with_headers(if_none_match="*"),
        "image/jpeg",
        b"cached-thumbnail",
    )
    assert wildcard.status_code == 304


def test_library_artwork_is_stored_but_revalidates_authorization_on_every_reuse() -> None:
    first = _library_artwork_response(request_with_headers(), "image/jpeg", b"private-thumbnail")

    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, no-cache"
    assert first.headers["vary"] == "Cookie, Authorization"

    revalidated = _library_artwork_response(
        request_with_headers(if_none_match=first.headers["etag"]),
        "image/jpeg",
        b"private-thumbnail",
    )

    assert revalidated.status_code == 304
    assert revalidated.body == b""


def test_paging_five_pages_of_warm_thumbnails_never_hits_the_rate_limit(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import socket

    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.models import User
    from app.security import get_current_user
    from app.services.artwork import ArtworkService, LibraryArtwork, RemoteArtworkResponse
    from app.services.network_policy import PublicSourcePolicy
    from app.services.rate_limit import rate_limiter

    def resolve(host, port, *args):  # noqa: ANN001, ANN002
        del args
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    fetches = {"count": 0}

    class CountingFetcher:
        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            validate_redirect(url)
            fetches["count"] += 1
            return RemoteArtworkResponse(content_type="image/jpeg", body=[b"thumbnail-bytes"])

    class GridLibraryAccess:
        def find_visible_artwork(self, household_member_id: str, library_item_id: str) -> LibraryArtwork:
            del household_member_id
            return LibraryArtwork(
                library_item_id=library_item_id,
                media_path=None,
                provider_artwork_url=f"https://images.example/{library_item_id}.jpg",
                library_root=tmp_path,
            )

    service = ArtworkService(
        remote_fetcher=CountingFetcher(),
        library_access=GridLibraryAccess(),
        library_root=tmp_path,
        public_source_policy=PublicSourcePolicy(resolver=resolve),
    )
    monkeypatch.setattr(main_module, "artwork", service)
    member = User(id="member-1", username="alice", display_name="Alice", role="viewer", is_active=True)
    main_module.app.dependency_overrides[get_current_user] = lambda: member
    rate_limiter.clear()
    client = TestClient(main_module.app, base_url="http://localhost")
    try:
        # Warm the artwork cache outside the HTTP surface: a 300-item grid.
        for index in range(300):
            service.load_library_artwork(member.id, f"grid-{index:03d}")
        assert fetches["count"] == 300

        # Five pages of 60 warm thumbnails serve without consuming the
        # upstream-fetch budget: no 429 and no new upstream fetches.
        statuses = {client.get(f"/api/library/grid-{index:03d}/artwork").status_code for index in range(300)}
        assert statuses == {200}
        assert fetches["count"] == 300

        # 304 revalidations are budget-free too.
        etag = client.get("/api/library/grid-000/artwork").headers["etag"]
        revalidated = client.get("/api/library/grid-000/artwork", headers={"if-none-match": etag})
        assert revalidated.status_code == 304

        # Cold thumbnails still hit upstream and keep the 120/min amplification bound.
        cold_statuses = [
            client.get(f"/api/library/cold-{index:03d}/artwork").status_code for index in range(121)
        ]
        assert cold_statuses.count(200) == 120
        assert cold_statuses[-1] == 429
        assert fetches["count"] == 420
    finally:
        client.close()
        main_module.app.dependency_overrides.clear()
        rate_limiter.clear()
