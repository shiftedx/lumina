from __future__ import annotations

from pathlib import Path

import pytest

from app.services.artwork import (
    ArtworkContentError,
    ArtworkService,
    RemoteArtworkResponse,
)
from app.services.chat_emotes import ChatEmoteArtworkService
from app.services.network_policy import PublicSourcePolicy


class ImageFetcher:
    def __init__(self, content: bytes = b"emote") -> None:
        self.content = content
        self.urls: list[str] = []

    def fetch(self, url, *, validate_redirect, headers=None):  # noqa: ANN001
        validate_redirect(url)
        self.urls.append(url)
        return RemoteArtworkResponse(content_type="image/png", body=[self.content], final_url=url)


def _policy(calls: list[tuple[str, int]] | None = None) -> PublicSourcePolicy:
    def resolve(host, port, *_args):  # noqa: ANN001
        if calls is not None:
            calls.append((host, port))
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    return PublicSourcePolicy(resolver=resolve)


def _id(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def test_one_response_never_self_evicts_returned_emote_ids() -> None:
    service = ChatEmoteArtworkService(
        remote_fetcher=ImageFetcher(),
        public_source_policy=_policy(),
        max_remote_sources=2,
    )
    resolve = service.new_resolver()

    paths = [resolve(f"https://images.example/emote-{index}.png") for index in range(3)]

    assert paths[2] is None
    assert all(path and path.startswith("/api/artwork/emotes/") for path in paths[:2])
    assert [service.load_remote_artwork(_id(path)).content for path in paths[:2] if path] == [b"emote", b"emote"]


def test_response_resolver_deduplicates_urls_and_resolves_each_host_once() -> None:
    dns: list[tuple[str, int]] = []
    service = ChatEmoteArtworkService(
        remote_fetcher=ImageFetcher(),
        public_source_policy=_policy(dns),
        token_factory=iter(("one", "two")).__next__,
    )
    resolve = service.new_resolver()

    first = resolve("https://images.example/one.png")
    duplicate = resolve(" https://images.example/one.png ")
    second = resolve("https://images.example/two.png")

    assert (first, duplicate, second) == (
        "/api/artwork/emotes/one",
        "/api/artwork/emotes/one",
        "/api/artwork/emotes/two",
    )
    assert dns == [("images.example", 443)]


def test_artwork_registration_without_a_resolved_set_keeps_the_one_argument_policy_call() -> None:
    class OneArgumentPolicy:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def validate_url(self, url: str) -> str:
            self.urls.append(url)
            return url

    policy = OneArgumentPolicy()
    service = ArtworkService(
        remote_fetcher=ImageFetcher(),
        public_source_policy=policy,  # type: ignore[arg-type]
        token_factory=lambda: "unchanged",
    )

    assert service.register_remote_artwork("https://images.example/poster.png") == "unchanged"
    assert policy.urls == ["https://images.example/poster.png"]


def test_response_resolver_rejects_invalid_or_private_urls_without_raising() -> None:
    def resolve(host, port, *_args):  # noqa: ANN001
        address = "127.0.0.1" if host == "private.example" else "93.184.216.34"
        return [(2, 1, 6, "", (address, port))]

    service = ChatEmoteArtworkService(
        remote_fetcher=ImageFetcher(),
        public_source_policy=PublicSourcePolicy(resolver=resolve),
    )
    resolver = service.new_resolver()

    assert resolver("http://images.example/plain.png") is None
    assert resolver("https://private.example/secret.png") is None
    assert resolver("not a URL") is None
    assert resolver("https://images.example/" + "x" * 2049) is None


def test_emote_registry_and_ram_cache_are_independent_from_posters(tmp_path: Path) -> None:
    poster = ArtworkService(
        remote_fetcher=ImageFetcher(b"poster"),
        public_source_policy=_policy(),
        cache_root=tmp_path / "posters",
        token_factory=lambda: "poster",
    )
    poster_id = poster.register_remote_artwork("https://images.example/poster.png")
    emotes = ChatEmoteArtworkService(
        remote_fetcher=ImageFetcher(),
        public_source_policy=_policy(),
        token_factory=lambda: "emote",
    )

    emote_path = emotes.new_resolver()("https://images.example/emote.png")
    assert emote_path == "/api/artwork/emotes/emote"
    assert poster.load_remote_artwork(poster_id).content == b"poster"
    assert emotes.load_remote_artwork("emote").content == b"emote"
    assert emotes._cache_root is None
    assert emotes._max_cache_bytes == 16 * 1024 * 1024
    assert emotes._max_cached_artworks == 256


def test_emote_images_have_a_128_kibibyte_limit() -> None:
    service = ChatEmoteArtworkService(
        remote_fetcher=ImageFetcher(b"x" * (128 * 1024 + 1)),
        public_source_policy=_policy(),
        token_factory=lambda: "large",
    )
    path = service.new_resolver()("https://images.example/large.png")

    assert path == "/api/artwork/emotes/large"
    with pytest.raises(ArtworkContentError):
        service.load_remote_artwork("large")
