from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from pathlib import Path
from threading import Event, Lock, Thread

from app.services.remote_streaming import ByteRange, RemoteStreamingService, UpstreamMediaResponse
from app.services.stream_cache import (
    MEDIA_FILE_CHUNK_SIZE,
    PersistentStreamRangeCache,
    StreamCacheKey,
    StreamCachePolicy,
    _entry_digest,
)


def _key(
    *,
    owner: str = "owner-1",
    source: str = "https://source.example/watch/one",
    track: str = "track-one",
    start: int = 0,
    end: int = 3,
) -> StreamCacheKey:
    return StreamCacheKey(
        owner_user_id=owner,
        source_identity=source,
        track_fingerprint=track,
        range_start=start,
        range_end=end,
    )


def _capture(cache: PersistentStreamRangeCache, key: StreamCacheKey, payload: bytes) -> bytes:
    body, close = cache.capture(
        key,
        status_code=206,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Length": str(len(payload)),
            "Content-Range": f"bytes {key.range_start}-{key.range_end}/100",
            "Content-Type": "video/mp4",
        },
        expected_length=len(payload),
        body=[payload],
        close=lambda: None,
    )
    try:
        return b"".join(body)
    finally:
        close()


def _read(cache: PersistentStreamRangeCache, key: StreamCacheKey) -> bytes | None:
    cached = cache.lookup(key)
    if cached is None:
        return None
    body, close = cached.open()
    try:
        return b"".join(body)
    finally:
        close()


def test_persistent_cache_survives_restart_without_storing_private_identities(tmp_path: Path) -> None:
    root = tmp_path / "stream-cache"
    key = _key()
    first = PersistentStreamRangeCache(root)

    assert _capture(first, key, b"data") == b"data"
    assert _read(first, key) == b"data"
    restarted = PersistentStreamRangeCache(root)
    assert _read(restarted, key) == b"data"

    stored_metadata = "".join(path.read_text() for path in root.rglob("*.json"))
    assert "owner-1" not in stored_metadata
    assert "saved-sign-in-1" not in stored_metadata
    assert "source.example" not in stored_metadata
    assert "signed" not in stored_metadata
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.rglob("*.bin"))


def test_incomplete_response_is_never_committed(tmp_path: Path) -> None:
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    key = _key()
    body, close = cache.capture(
        key,
        status_code=206,
        headers={"Content-Length": "4"},
        expected_length=4,
        body=[b"ab", b"cd"],
        close=lambda: None,
    )

    iterator = iter(body)
    assert next(iterator) == b"ab"
    close()

    assert cache.lookup(key) is None
    assert list((tmp_path / "cache" / ".tmp").iterdir()) == []


def test_cached_media_reads_in_bounded_playback_chunks_and_closes_on_cancel(tmp_path: Path) -> None:
    payload = b"a" * MEDIA_FILE_CHUNK_SIZE + b"tail"
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    key = _key(end=len(payload) - 1)
    _capture(cache, key, payload)
    cached = cache.lookup(key)
    assert cached is not None
    body, close = cached.open()

    chunks = iter(body)
    assert next(chunks) == payload[:MEDIA_FILE_CHUNK_SIZE]
    close()  # StreamingResponse's background task does this when the client disconnects.

    assert body._file.closed is True


def test_owner_policy_evicts_old_videos_and_enforces_byte_quota(tmp_path: Path) -> None:
    policy = StreamCachePolicy(recent_video_limit=2, max_bytes=6)
    now = count(1).__next__
    cache = PersistentStreamRangeCache(
        tmp_path / "cache",
        policy_provider=lambda _owner: policy,
        max_bytes_global=100,
        clock=now,
    )
    first = _key(source="https://source.example/one")
    second = _key(source="https://source.example/two")
    third = _key(source="https://source.example/three")

    _capture(cache, first, b"1111")
    _capture(cache, second, b"2222")
    assert cache.lookup(first) is None
    assert _read(cache, second) == b"2222"

    _capture(cache, third, b"3333")
    assert cache.lookup(second) is None
    assert _read(cache, third) == b"3333"


def test_cache_is_owner_scoped_even_for_identical_source_and_range(tmp_path: Path) -> None:
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    owner = _key(owner="owner-1")
    other = _key(owner="owner-2")
    _capture(cache, owner, b"data")

    assert _read(cache, owner) == b"data"
    assert cache.lookup(other) is None


def test_disabled_owner_policy_stops_reusing_existing_ranges(tmp_path: Path) -> None:
    enabled = True
    cache = PersistentStreamRangeCache(
        tmp_path / "cache",
        policy_provider=lambda _owner: StreamCachePolicy(5, 1024) if enabled else StreamCachePolicy(0, 0),
    )
    key = _key()
    _capture(cache, key, b"data")
    assert _read(cache, key) == b"data"

    enabled = False

    assert cache.lookup(key) is None


def test_policy_failure_fails_closed_for_existing_and_new_ranges(tmp_path: Path) -> None:
    failing = False

    def policy(_owner: str) -> StreamCachePolicy:
        if failing:
            raise RuntimeError("settings unavailable")
        return StreamCachePolicy(5, 1024)

    cache = PersistentStreamRangeCache(tmp_path / "cache", policy_provider=policy)
    key = _key()
    _capture(cache, key, b"data")
    failing = True

    assert cache.lookup(key) is None
    assert _capture(cache, _key(track="new-track"), b"more") == b"more"
    assert cache.lookup(_key(track="new-track")) is None


def test_disabling_during_an_inflight_capture_discards_the_completed_write(tmp_path: Path) -> None:
    enabled = True
    cache = PersistentStreamRangeCache(
        tmp_path / "cache",
        policy_provider=lambda _owner: StreamCachePolicy(5, 1024) if enabled else StreamCachePolicy(0, 0),
    )
    key = _key()
    body, close = cache.capture(
        key,
        status_code=206,
        headers={"Content-Length": "4", "Content-Range": "bytes 0-3/100"},
        expected_length=4,
        body=[b"data"],
        close=lambda: None,
    )
    enabled = False
    try:
        assert b"".join(body) == b"data"
    finally:
        close()

    assert list((tmp_path / "cache" / "entries").rglob("*.bin")) == []


def test_disable_prune_serializes_with_the_final_cache_replacement(tmp_path: Path) -> None:
    enabled = True
    policy_calls = 0
    policy_calls_lock = Lock()
    commit_policy_read = Event()
    release_commit_policy = Event()

    def policy(_owner: str) -> StreamCachePolicy:
        nonlocal policy_calls
        with policy_calls_lock:
            policy_calls += 1
            call = policy_calls
            selected = enabled
        if call == 2:
            commit_policy_read.set()
            release_commit_policy.wait(timeout=2)
        return StreamCachePolicy(5, 1024) if selected else StreamCachePolicy(0, 0)

    cache = PersistentStreamRangeCache(tmp_path / "cache", policy_provider=policy)
    key = _key()
    body, close = cache.capture(
        key,
        status_code=206,
        headers={"Content-Length": "4", "Content-Range": "bytes 0-3/100"},
        expected_length=4,
        body=[b"data"],
        close=lambda: None,
    )
    consume_errors: list[BaseException] = []

    def consume() -> None:
        try:
            assert b"".join(body) == b"data"
        except BaseException as exc:  # pragma: no cover - assertion is reported below
            consume_errors.append(exc)
        finally:
            close()

    consumer = Thread(target=consume)
    consumer.start()
    assert commit_policy_read.wait(timeout=1)
    enabled = False
    prune_finished = Event()

    def prune() -> None:
        cache.prune_owner(key.owner_user_id)
        prune_finished.set()

    pruner = Thread(target=prune)
    pruner.start()
    prune_finished_before_release = prune_finished.wait(timeout=0.2)
    release_commit_policy.set()
    consumer.join(timeout=2)
    pruner.join(timeout=2)

    assert not prune_finished_before_release
    assert not consumer.is_alive()
    assert not pruner.is_alive()
    assert consume_errors == []
    assert list((tmp_path / "cache" / "entries").rglob("*.bin")) == []


def test_cache_rejects_symlink_bodies_and_sweeps_orphans(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    cache = PersistentStreamRangeCache(root)
    key = _key()
    _capture(cache, key, b"data")
    body_path = next(root.rglob("*.bin"))
    target = tmp_path / "target"
    target.write_bytes(b"PWN!")
    body_path.unlink()
    body_path.symlink_to(target)

    assert cache.lookup(key) is None

    orphan = root / "entries" / "aa" / f"{'a' * 64}.bin"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    PersistentStreamRangeCache(root)
    assert not orphan.exists()


def test_cache_never_traverses_a_symlink_shard_directory(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    PersistentStreamRangeCache(root)
    key = _key()
    shard = root / "entries" / _entry_digest(key)[:2]
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel_json = outside / "sentinel.json"
    sentinel_bin = outside / "sentinel.bin"
    sentinel_json.write_text("do not delete")
    sentinel_bin.write_bytes(b"do not delete")

    shard.symlink_to(outside, target_is_directory=True)
    restarted = PersistentStreamRangeCache(root)
    assert not shard.exists()
    assert sentinel_json.read_text() == "do not delete"
    assert sentinel_bin.read_bytes() == b"do not delete"

    shard.symlink_to(outside, target_is_directory=True)
    assert _capture(restarted, key, b"data") == b"data"
    assert shard.is_dir() and not shard.is_symlink()
    assert sentinel_json.read_text() == "do not delete"
    assert sentinel_bin.read_bytes() == b"do not delete"
    assert _read(restarted, key) == b"data"


def test_restart_prune_applies_a_reduced_global_limit(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    key = _key()
    _capture(PersistentStreamRangeCache(root, max_bytes_global=100), key, b"data")
    restarted = PersistentStreamRangeCache(root, max_bytes_global=2)
    restarted.prune()
    assert restarted.lookup(key) is None


def _progressive_info(signed_url: str) -> dict:
    return {
        "formats": [
            {
                "format_id": "18",
                "url": signed_url,
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "avc1.42001e",
                "acodec": "mp4a.40.2",
                "height": 720,
                "filesize": 17,
            }
        ]
    }


@dataclass
class _CountingReader:
    opens: int = 0
    payload: bytes = b"progressive-media"

    def open(self, track, byte_range: ByteRange | None, timeout_seconds=None):  # noqa: ANN001
        del track, timeout_seconds
        self.opens += 1
        start = byte_range.start if byte_range and byte_range.start is not None else 0
        end = byte_range.end if byte_range and byte_range.end is not None else len(self.payload) - 1
        body = self.payload[start : end + 1]
        return UpstreamMediaResponse(
            206 if byte_range else 200,
            {
                "Content-Length": str(len(body)),
                "Content-Range": f"bytes {start}-{end}/{len(self.payload)}",
                "Accept-Ranges": "bytes",
            },
            [body],
        )


class _NoRefreshResolver:
    def resolve(self, source_url, owner_user_id):  # noqa: ANN001
        raise AssertionError((source_url, owner_user_id))


def test_remote_service_reuses_validated_range_across_new_signed_urls(tmp_path: Path) -> None:
    reader = _CountingReader()
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    tokens = iter(("stream-one", "stream-two"))
    service = RemoteStreamingService(
        resolver=_NoRefreshResolver(),
        reader=reader,
        token_factory=tokens.__next__,
        clock=lambda: 1_000.0,
        stream_cache=cache,
    )
    first = service.register(
        owner_user_id="owner",
        source_url="https://source.example/watch/one",
        info=_progressive_info("https://signed.example/media?signature=first"),
    )
    first_response = service.serve_content("owner", first.stream_id, "bytes=2-6")
    assert b"".join(first_response.body) == b"ogres"
    first_response.close()
    service.release("owner", first.stream_id)

    second = service.register(
        owner_user_id="owner",
        source_url="https://source.example/watch/one",
        info=_progressive_info("https://signed.example/media?signature=second"),
    )
    cached_response = service.serve_content("owner", second.stream_id, "bytes=2-6")
    assert b"".join(cached_response.body) == b"ogres"
    cached_response.close()

    assert reader.opens == 1


def test_remote_service_caches_a_complete_small_progressive_response(tmp_path: Path) -> None:
    reader = _CountingReader()
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    tokens = iter(("stream-one", "stream-two"))
    service = RemoteStreamingService(
        resolver=_NoRefreshResolver(), reader=reader, token_factory=tokens.__next__, clock=lambda: 1_000.0, stream_cache=cache,
    )
    for signed in ("first", "second"):
        playback = service.register(
            owner_user_id="owner", source_url="https://source.example/watch/one",
            info=_progressive_info(f"https://signed.example/media?signature={signed}"),
        )
        response = service.serve_content("owner", playback.stream_id)
        assert b"".join(response.body) == b"progressive-media"
        response.close()
        service.release("owner", playback.stream_id)
    assert reader.opens == 1


def test_full_progressive_cache_normalizes_a_prior_partial_response(tmp_path: Path) -> None:
    reader = _CountingReader()
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    tokens = iter(("stream-one", "stream-two"))
    service = RemoteStreamingService(
        resolver=_NoRefreshResolver(), reader=reader, token_factory=tokens.__next__, clock=lambda: 1_000.0, stream_cache=cache,
    )
    first = service.register(
        owner_user_id="owner", source_url="https://source.example/watch/one",
        info=_progressive_info("https://signed.example/media?signature=first"),
    )
    ranged = service.serve_content("owner", first.stream_id, "bytes=0-16")
    assert ranged.status_code == 206
    assert b"".join(ranged.body) == b"progressive-media"
    ranged.close()
    service.release("owner", first.stream_id)

    second = service.register(
        owner_user_id="owner", source_url="https://source.example/watch/one",
        info=_progressive_info("https://signed.example/media?signature=second"),
    )
    full = service.serve_content("owner", second.stream_id)
    assert full.status_code == 200
    assert not any(name.casefold() == "content-range" for name in full.headers)
    assert b"".join(full.body) == b"progressive-media"
    full.close()
    assert reader.opens == 1


def test_full_progressive_cache_can_satisfy_a_later_full_range_request(tmp_path: Path) -> None:
    reader = _CountingReader()
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    tokens = iter(("stream-one", "stream-two"))
    service = RemoteStreamingService(
        resolver=_NoRefreshResolver(), reader=reader, token_factory=tokens.__next__, clock=lambda: 1_000.0, stream_cache=cache,
    )
    first = service.register(
        owner_user_id="owner", source_url="https://source.example/watch/one",
        info=_progressive_info("https://signed.example/media?signature=first"),
    )
    full = service.serve_content("owner", first.stream_id)
    assert full.status_code == 200
    assert b"".join(full.body) == b"progressive-media"
    full.close()
    service.release("owner", first.stream_id)

    second = service.register(
        owner_user_id="owner", source_url="https://source.example/watch/one",
        info=_progressive_info("https://signed.example/media?signature=second"),
    )
    ranged = service.serve_content("owner", second.stream_id, "bytes=0-16")
    # Ignoring a Range request and returning the complete representation is valid.
    assert ranged.status_code == 200
    assert b"".join(ranged.body) == b"progressive-media"
    ranged.close()
    assert reader.opens == 1


def test_generic_sources_with_same_extractor_id_do_not_share_cached_bytes(tmp_path: Path) -> None:
    class SourceReader(_CountingReader):
        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            self.payload = b"AAAA" if "one" in track.url else b"BBBB"
            return super().open(track, byte_range, timeout_seconds)

    reader = SourceReader()
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    tokens = iter(("one", "two"))
    service = RemoteStreamingService(
        resolver=_NoRefreshResolver(), reader=reader, token_factory=tokens.__next__, clock=lambda: 1_000.0, stream_cache=cache,
    )
    outputs = []
    for host in ("one", "two"):
        info = _progressive_info(f"https://signed.example/{host}")
        info.update({"id": "video", "extractor": "generic"})
        info["formats"][0]["filesize"] = 4
        playback = service.register(
            owner_user_id="owner", source_url=f"https://{host}.example/video", info=info,
        )
        response = service.serve_content("owner", playback.stream_id, "bytes=0-3")
        outputs.append(b"".join(response.body))
        response.close()
    assert outputs == [b"AAAA", b"BBBB"]
    assert reader.opens == 2


def test_generic_url_containing_youtu_cannot_seed_a_youtube_cache_identity(tmp_path: Path) -> None:
    class SourceReader(_CountingReader):
        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            self.payload = b"EVIL" if "attacker" in track.url else b"REAL"
            return super().open(track, byte_range, timeout_seconds)

    reader = SourceReader()
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    tokens = iter(("generic", "youtube"))
    service = RemoteStreamingService(
        resolver=_NoRefreshResolver(), reader=reader, token_factory=tokens.__next__, clock=lambda: 1_000.0, stream_cache=cache,
    )
    generic_info = _progressive_info("https://attacker.example/media")
    generic_info.update({"id": "same-video-id", "extractor": "generic"})
    generic_info["formats"][0]["filesize"] = 4
    youtube_info = _progressive_info("https://signed.youtube.example/media")
    youtube_info.update({"id": "same-video-id", "extractor_key": "Youtube"})
    youtube_info["formats"][0]["filesize"] = 4

    generic = service.register(
        owner_user_id="owner", source_url="https://attacker.example/youtu/watch", info=generic_info,
    )
    generic_response = service.serve_content("owner", generic.stream_id, "bytes=0-3")
    assert b"".join(generic_response.body) == b"EVIL"
    generic_response.close()

    youtube = service.register(
        owner_user_id="owner", source_url="https://youtube.com/watch?v=same-video-id", info=youtube_info,
    )
    youtube_response = service.serve_content("owner", youtube.stream_id, "bytes=0-3")
    assert b"".join(youtube_response.body) == b"REAL"
    youtube_response.close()
    assert reader.opens == 2


def test_generic_tracking_and_query_order_variants_share_cached_bytes(tmp_path: Path) -> None:
    reader = _CountingReader(payload=b"DATA")
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    tokens = iter(("first", "second"))
    service = RemoteStreamingService(
        resolver=_NoRefreshResolver(), reader=reader, token_factory=tokens.__next__, clock=lambda: 1_000.0, stream_cache=cache,
    )
    source_urls = (
        "HTTPS://Example.TEST:443/watch?utm_source=mail&b=2&a=1#chapter",
        "https://example.test/watch?a=1&utm_MEDIUM=email&b=2",
    )
    for index, source_url in enumerate(source_urls):
        info = _progressive_info(f"https://signed.example/media?signature={index}")
        info.update({"id": "generic-video", "extractor": "generic"})
        info["formats"][0]["filesize"] = 4
        playback = service.register(
            owner_user_id="owner", source_url=source_url, info=info,
        )
        response = service.serve_content("owner", playback.stream_id, "bytes=0-3")
        assert b"".join(response.body) == b"DATA"
        response.close()
    assert reader.opens == 1


def test_meaningful_generic_query_changes_do_not_share_cached_bytes(tmp_path: Path) -> None:
    class SourceReader(_CountingReader):
        def open(self, track, byte_range, timeout_seconds=None):  # noqa: ANN001
            self.payload = b"AAAA" if "first" in track.url else b"BBBB"
            return super().open(track, byte_range, timeout_seconds)

    reader = SourceReader()
    cache = PersistentStreamRangeCache(tmp_path / "cache")
    tokens = iter(("first", "second"))
    service = RemoteStreamingService(
        resolver=_NoRefreshResolver(), reader=reader, token_factory=tokens.__next__, clock=lambda: 1_000.0, stream_cache=cache,
    )
    outputs = []
    for value, signed in (("original", "first"), ("alternate", "second")):
        info = _progressive_info(f"https://signed.example/{signed}")
        info.update({"id": "generic-video", "extractor": "generic"})
        info["formats"][0]["filesize"] = 4
        playback = service.register(
            owner_user_id="owner",
            source_url=f"https://example.test/watch?edition={value}",
            info=info,
        )
        response = service.serve_content("owner", playback.stream_id, "bytes=0-3")
        outputs.append(b"".join(response.body))
        response.close()
    assert outputs == [b"AAAA", b"BBBB"]
    assert reader.opens == 2


def test_cache_failure_never_breaks_remote_playback() -> None:
    class BrokenCache:
        def lookup(self, key):  # noqa: ANN001
            raise OSError(key)

        def capture(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise OSError((args, kwargs))

    reader = _CountingReader()
    service = RemoteStreamingService(
        resolver=_NoRefreshResolver(),
        reader=reader,
        token_factory=lambda: "stream",
        clock=lambda: 1_000.0,
        stream_cache=BrokenCache(),  # type: ignore[arg-type]
    )
    playback = service.register(
        owner_user_id="owner",
        source_url="https://source.example/watch/one",
        info=_progressive_info("https://signed.example/media"),
    )

    response = service.serve_content("owner", playback.stream_id, "bytes=2-6")
    assert b"".join(response.body) == b"ogres"
    response.close()


def test_unavailable_cache_directory_degrades_to_a_miss(tmp_path: Path) -> None:
    unavailable = tmp_path / "not-a-directory"
    unavailable.write_text("occupied")
    cache = PersistentStreamRangeCache(unavailable)
    key = _key()

    assert cache.lookup(key) is None
    assert _capture(cache, key, b"data") == b"data"
    assert cache.lookup(key) is None
