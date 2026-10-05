from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services import artwork as artwork_module
from app.services.artwork import (
    ArtworkContentError,
    ArtworkCapacityError,
    ArtworkNotFoundError,
    ArtworkService,
    ArtworkUnavailableError,
    LibraryArtwork,
    RemoteArtworkResponse,
)
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError


class PublicFixtureFetcher:
    def fetch(self, url, *, validate_redirect):  # noqa: ANN001
        validate_redirect(url)
        return RemoteArtworkResponse(content_type="image/jpeg", body=[b"fixture-artwork"])


class LibraryAccess:
    def __init__(self, artwork: LibraryArtwork) -> None:
        self.artwork = artwork

    def find_visible_artwork(self, household_member_id: str, library_item_id: str) -> LibraryArtwork | None:
        if household_member_id != "viewer" or library_item_id != "library-item":
            return None
        return self.artwork


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int, *args):  # noqa: ANN001
        import socket

        del args
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))
            for address in mapping[host]
        ]

    return resolve


def test_remote_artwork_is_loaded_through_an_opaque_identifier() -> None:
    service = ArtworkService(
        remote_fetcher=PublicFixtureFetcher(),
        public_source_policy=PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]})),
        token_factory=lambda: "opaque-artwork-id",
    )

    artwork_id = service.register_remote_artwork("https://images.example/poster.jpg?private=marker")
    artwork = service.load_remote_artwork(artwork_id)

    assert artwork_id == "opaque-artwork-id"
    assert "images.example" not in artwork_id
    assert artwork.content_type == "image/jpeg"
    assert artwork.content == b"fixture-artwork"


def test_reregistering_a_known_artwork_source_skips_the_dns_lookup() -> None:
    lookups: list[str] = []
    resolve = resolver_for({"images.example": ["93.184.216.34"]})
    service = ArtworkService(
        remote_fetcher=PublicFixtureFetcher(),
        public_source_policy=PublicSourcePolicy(resolver=lambda host, *args: lookups.append(host) or resolve(host, *args)),
    )

    first = service.register_remote_artwork("https://images.example/poster.jpg")
    assert service.register_remote_artwork(" https://images.example/poster.jpg ") == first
    assert lookups == ["images.example"]


def test_library_artwork_requires_visibility_and_prefers_a_safe_adjacent_image(tmp_path: Path) -> None:
    media_path = tmp_path / "library" / "movie.mp4"
    media_path.parent.mkdir()
    media_path.write_bytes(b"media")
    media_path.with_suffix(".webp").write_bytes(b"local-poster")
    service = ArtworkService(
        remote_fetcher=PublicFixtureFetcher(),
        library_access=LibraryAccess(
            LibraryArtwork(library_item_id="library-item", media_path=media_path, provider_artwork_url="https://images.example/poster.jpg")
        ),
        library_root=tmp_path / "library",
        public_source_policy=PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]})),
    )

    with pytest.raises(ArtworkNotFoundError):
        service.load_library_artwork("different-viewer", "library-item")

    artwork = service.load_library_artwork("viewer", "library-item")
    assert artwork.content_type == "image/webp"
    assert artwork.content == b"local-poster"


def test_remote_artwork_rejects_private_redirects_unsupported_content_and_oversized_bodies() -> None:
    class RedirectingFetcher:
        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            del url
            validate_redirect("https://private.example/artwork.jpg")
            raise AssertionError("private redirect should be rejected before it is fetched")

    policy = PublicSourcePolicy(
        resolver=resolver_for({"images.example": ["93.184.216.34"], "private.example": ["127.0.0.1"]})
    )
    redirect_service = ArtworkService(remote_fetcher=RedirectingFetcher(), public_source_policy=policy)
    redirect_id = redirect_service.register_remote_artwork("https://images.example/poster.jpg")
    with pytest.raises(PublicSourcePolicyError):
        redirect_service.load_remote_artwork(redirect_id)

    class InvalidFetcher:
        def __init__(self, response: RemoteArtworkResponse) -> None:
            self.response = response

        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            validate_redirect(url)
            return self.response

    for response in (
        RemoteArtworkResponse(content_type="text/html", body=[b"not an image"]),
        RemoteArtworkResponse(content_type="image/jpeg", body=[b"12345"]),
    ):
        service = ArtworkService(
            remote_fetcher=InvalidFetcher(response),
            public_source_policy=PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]})),
            max_artwork_bytes=4,
        )
        artwork_id = service.register_remote_artwork("https://images.example/poster.jpg")
        with pytest.raises(ArtworkContentError):
            service.load_remote_artwork(artwork_id)


def test_remote_artwork_closes_rejected_responses() -> None:
    closed = []

    class InvalidFetcher:
        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            validate_redirect(url)
            return RemoteArtworkResponse(content_type="text/html", body=[b"not-art"], close=lambda: closed.append(True))

    service = ArtworkService(
        remote_fetcher=InvalidFetcher(),
        public_source_policy=PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]})),
    )
    artwork_id = service.register_remote_artwork("https://images.example/poster.jpg")

    with pytest.raises(ArtworkContentError):
        service.load_remote_artwork(artwork_id)

    assert closed == [True]


def test_public_fetcher_closes_transport_on_open_and_final_url_validation_failures(monkeypatch) -> None:
    closed: list[str] = []

    class FakeResponse:
        url = "https://private.example/art.jpg"
        status = 200
        headers = {"Content-Type": "image/jpeg"}

        def close(self) -> None:
            closed.append("response")

    class FakeYDL:
        fail_open = True

        def __init__(self, options, policy):  # noqa: ANN001
            del options, policy

        def urlopen(self, request):  # noqa: ANN001
            del request
            if self.fail_open:
                raise OSError("open failed")
            return FakeResponse()

        def close(self) -> None:
            closed.append("ydl")

    monkeypatch.setattr(artwork_module, "PolicyYoutubeDL", FakeYDL)
    fetcher = artwork_module.PublicArtworkFetcher(policy=PublicSourcePolicy(resolver=resolver_for({})))

    with pytest.raises(OSError, match="open failed"):
        fetcher.fetch("https://images.example/art.jpg", validate_redirect=lambda value: value)
    assert closed == ["ydl"]

    FakeYDL.fail_open = False
    with pytest.raises(PublicSourcePolicyError):
        fetcher.fetch(
            "https://images.example/art.jpg",
            validate_redirect=lambda value: (_ for _ in ()).throw(PublicSourcePolicyError()) if "private" in value else value,
        )
    assert closed == ["ydl", "response", "ydl"]


def test_remote_artwork_coalesces_refreshes_caches_then_serves_stale_on_transient_failure() -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    now = [100.0]

    class SlowThenUnavailableFetcher:
        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            nonlocal calls
            validate_redirect(url)
            calls += 1
            if calls == 1:
                entered.set()
                assert release.wait(timeout=2)
                return RemoteArtworkResponse(content_type="image/jpeg", body=[b"cached-artwork"])
            raise OSError("provider temporarily unavailable")

    service = ArtworkService(
        remote_fetcher=SlowThenUnavailableFetcher(),
        public_source_policy=PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]})),
        token_factory=lambda: "coalesced-id",
        clock=lambda: now[0],
        cache_ttl_seconds=10,
        stale_if_error_seconds=30,
    )
    artwork_id = service.register_remote_artwork("https://images.example/poster.jpg")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.load_remote_artwork, artwork_id)
        assert entered.wait(timeout=2)
        second = executor.submit(service.load_remote_artwork, artwork_id)
        release.set()
        assert first.result(timeout=2).content == b"cached-artwork"
        assert second.result(timeout=2).content == b"cached-artwork"

    assert calls == 1
    assert service.load_remote_artwork(artwork_id).content == b"cached-artwork"
    assert calls == 1

    now[0] += 11
    assert service.load_remote_artwork(artwork_id).content == b"cached-artwork"
    assert calls == 2

    now[0] += 31
    with pytest.raises(ArtworkUnavailableError):
        service.load_remote_artwork(artwork_id)


def test_remote_artwork_disk_cache_survives_restart_without_persisting_source_urls(tmp_path: Path) -> None:
    calls = 0
    now = [1_000.0]

    class CountingFetcher:
        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            nonlocal calls
            validate_redirect(url)
            calls += 1
            return RemoteArtworkResponse(content_type="image/jpeg", body=[b"durable-artwork"])

    source_url = "https://images.example/poster.jpg?provider-secret-marker"
    policy = PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]}))
    cache_root = tmp_path / "artwork-cache"
    first = ArtworkService(
        remote_fetcher=CountingFetcher(),
        public_source_policy=policy,
        token_factory=lambda: "first-process-id",
        cache_root=cache_root,
        clock=lambda: now[0],
    )
    first_id = first.register_remote_artwork(source_url)
    assert first.load_remote_artwork(first_id).content == b"durable-artwork"
    assert calls == 1

    second = ArtworkService(
        remote_fetcher=CountingFetcher(),
        public_source_policy=policy,
        token_factory=lambda: "second-process-id",
        cache_root=cache_root,
        clock=lambda: now[0],
    )
    second_id = second.register_remote_artwork(source_url)
    assert second_id != first_id
    assert second.load_remote_artwork(second_id).content == b"durable-artwork"
    assert calls == 1
    assert source_url.encode() not in b"".join(path.read_bytes() for path in cache_root.iterdir())


def test_remote_artwork_disk_cache_is_private_and_refuses_symlink_entries(tmp_path: Path) -> None:
    calls = 0

    class CountingFetcher:
        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            nonlocal calls
            validate_redirect(url)
            calls += 1
            return RemoteArtworkResponse(content_type="image/jpeg", body=[b"safe-artwork"])

    policy = PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]}))
    cache_root = tmp_path / "artwork-cache"
    source_url = "https://images.example/poster.jpg"
    first = ArtworkService(remote_fetcher=CountingFetcher(), public_source_policy=policy, cache_root=cache_root)
    artwork_id = first.register_remote_artwork(source_url)
    assert first.load_remote_artwork(artwork_id).content == b"safe-artwork"
    assert cache_root.stat().st_mode & 0o777 == 0o700

    content_path = next(cache_root.glob("*.bin"))
    content_path.unlink()
    sensitive = tmp_path / "sensitive.txt"
    sensitive.write_bytes(b"must-not-be-served")
    content_path.symlink_to(sensitive)

    second = ArtworkService(remote_fetcher=CountingFetcher(), public_source_policy=policy, cache_root=cache_root)
    second_id = second.register_remote_artwork(source_url)
    assert second.load_remote_artwork(second_id).content == b"safe-artwork"
    assert calls == 2


def test_remote_artwork_disk_cache_sweeps_expired_entries_on_restart(tmp_path: Path) -> None:
    calls = 0
    now = [100.0]

    class CountingFetcher:
        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            nonlocal calls
            validate_redirect(url)
            calls += 1
            return RemoteArtworkResponse(content_type="image/jpeg", body=[b"fresh-artwork"])

    policy = PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]}))
    cache_root = tmp_path / "artwork-cache"
    first = ArtworkService(
        remote_fetcher=CountingFetcher(), public_source_policy=policy, cache_root=cache_root,
        clock=lambda: now[0], cache_ttl_seconds=10, stale_if_error_seconds=20,
    )
    first_id = first.register_remote_artwork("https://images.example/poster.jpg")
    assert first.load_remote_artwork(first_id).content == b"fresh-artwork"
    assert list(cache_root.iterdir())
    (cache_root / ".interrupted.bin.deadbeef.tmp").write_bytes(b"partial")

    now[0] = 131.0
    ArtworkService(
        remote_fetcher=CountingFetcher(), public_source_policy=policy, cache_root=cache_root,
        clock=lambda: now[0], cache_ttl_seconds=10, stale_if_error_seconds=20,
    )
    assert list(cache_root.iterdir()) == []


def test_remote_fetches_and_duplicate_waiters_are_bounded() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingFetcher:
        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            validate_redirect(url)
            entered.set()
            assert release.wait(timeout=2)
            return RemoteArtworkResponse(content_type="image/jpeg", body=[b"artwork"])

    tokens = iter(["first", "second"])
    service = ArtworkService(
        remote_fetcher=BlockingFetcher(),
        public_source_policy=PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]})),
        token_factory=lambda: next(tokens),
        max_concurrent_fetches=1,
        max_waiters_per_fetch=1,
        coalesced_wait_timeout_seconds=0.01,
    )
    first_id = service.register_remote_artwork("https://images.example/first.jpg")
    second_id = service.register_remote_artwork("https://images.example/second.jpg")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.load_remote_artwork, first_id)
        assert entered.wait(timeout=2)
        with pytest.raises(ArtworkCapacityError):
            service.load_remote_artwork(second_id)
        with pytest.raises(ArtworkCapacityError):
            service.load_remote_artwork(first_id)
        release.set()
        assert first.result(timeout=2).content == b"artwork"


def test_remote_cache_evicts_lru_entries_without_exposing_provider_urls() -> None:
    calls: dict[str, int] = {"one": 0, "two": 0}
    tokens = iter(["one-id", "two-id"])

    class CountingFetcher:
        def fetch(self, url, *, validate_redirect):  # noqa: ANN001
            validate_redirect(url)
            source = "one" if "/one" in url else "two"
            calls[source] += 1
            return RemoteArtworkResponse(content_type="image/png", body=[source.encode()])

    service = ArtworkService(
        remote_fetcher=CountingFetcher(),
        public_source_policy=PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]})),
        token_factory=lambda: next(tokens),
        max_cached_artworks=1,
    )
    first_id = service.register_remote_artwork("https://images.example/one.png")
    second_id = service.register_remote_artwork("https://images.example/two.png")

    assert service.load_remote_artwork(first_id).content == b"one"
    assert service.load_remote_artwork(second_id).content == b"two"
    assert service.load_remote_artwork(first_id).content == b"one"
    assert calls == {"one": 2, "two": 1}
    assert "images.example" not in first_id
    assert "images.example" not in second_id


def test_library_artwork_falls_back_to_remote_and_rejects_paths_outside_the_library_root(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    media_path = library_root / "movie.mp4"
    media_path.write_bytes(b"media")
    access = LibraryAccess(
        LibraryArtwork(library_item_id="library-item", media_path=media_path, provider_artwork_url="https://images.example/poster.jpg")
    )
    service = ArtworkService(
        remote_fetcher=PublicFixtureFetcher(),
        library_access=access,
        library_root=library_root,
        public_source_policy=PublicSourcePolicy(resolver=resolver_for({"images.example": ["93.184.216.34"]})),
    )

    assert service.load_library_artwork("viewer", "library-item").content == b"fixture-artwork"

    access.artwork = LibraryArtwork(
        library_item_id="library-item",
        media_path=tmp_path / "outside" / "../secret.mp4",
        provider_artwork_url="https://images.example/poster.jpg",
    )
    with pytest.raises(ArtworkNotFoundError):
        service.load_library_artwork("viewer", "library-item")
