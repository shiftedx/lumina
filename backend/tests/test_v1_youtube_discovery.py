"""Public YouTube search kinds, stable identities, partial results, no credentials."""

from __future__ import annotations

import yt_dlp

from app.models import User
from app.services.channel_discovery import ChannelDiscoveryService, canonical_channel_identity
from app.services.member_follows import FollowRequest, MemberFollowService
from app.services.yt_dlp_service import PUBLIC_OPTION_ALLOWLIST, YtDlpService, _SEARCH_CACHE
from support import memory_session_factory, seed_app_settings

CHANNEL_ID = "UCHnyfMqiRRG1u-2MsSQLbXA"


def _session():
    db = memory_session_factory()()
    seed_app_settings(db)
    return db


def _fake_ydl(entries_by_prefix, seen_options):
    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            seen_options.append(dict(options))

        def __enter__(self):
            return self

        def __exit__(self, *_exc):  # noqa: ANN002
            return False

        def extract_info(self, query, download=False):  # noqa: ANN001, ARG002
            entries = entries_by_prefix[query.split(":", 1)[0].rstrip("0123456789")]
            if isinstance(entries, Exception):
                raise entries
            return {"_type": "playlist", "entries": entries}

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    return FakeYDL


RECORDED_YTSEARCH = [
    {"_type": "url", "ie_key": "Youtube", "id": "dQw4w9WgXcQ", "title": "A video",
     "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "channel_id": CHANNEL_ID,
     "uploader_id": "@Veritasium", "channel_url": f"https://www.youtube.com/channel/{CHANNEL_ID}", "channel": "Veritasium"},
    {"_type": "url", "ie_key": "Youtube", "id": "abcdefghijk", "title": "A short",
     "url": "https://www.youtube.com/shorts/abcdefghijk", "channel_id": CHANNEL_ID, "channel": "Veritasium"},
    {"_type": "url", "ie_key": "Youtube", "id": "LiveLive123", "title": "Now live",
     "url": "https://www.youtube.com/watch?v=LiveLive123", "live_status": "is_live"},
    # Same stable id, different title: a duplicate, not a second result.
    {"_type": "url", "ie_key": "Youtube", "id": "dQw4w9WgXcQ", "title": "A video (retitled)",
     "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    # Same title as the first, different id: a distinct result.
    {"_type": "url", "ie_key": "Youtube", "id": "zzzzzzzzzzz", "title": "A video",
     "url": "https://youtu.be/zzzzzzzzzzz"},
    {"_type": "url", "ie_key": "YoutubeTab", "id": "PL123", "title": "A playlist",
     "url": "https://www.youtube.com/playlist?list=PL123"},
    {"_type": "url", "ie_key": "YoutubeTab", "id": CHANNEL_ID, "title": "Veritasium",
     "url": "https://www.youtube.com/@Veritasium"},
]


def test_youtube_kind_and_identity() -> None:
    _SEARCH_CACHE.clear()
    service = YtDlpService(_session(), ydl_factory=_fake_ydl({"ytsearch": RECORDED_YTSEARCH}, []))
    items = service.youtube_search("veritasium", limit=12).items

    assert [(item.id, item.kind) for item in items] == [
        ("dQw4w9WgXcQ", "video"), ("abcdefghijk", "short"), ("LiveLive123", "live"),
        ("zzzzzzzzzzz", "video"), ("PL123", "playlist"), (CHANNEL_ID, "channel"),
    ]
    assert items[1].webpage_url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert items[3].webpage_url == "https://www.youtube.com/watch?v=zzzzzzzzzzz"
    assert items[0].uploader_id == CHANNEL_ID  # the immutable id, not the @handle

    # Every address variant of one channel converges on the channel-id identity.
    canonical = f"https://www.youtube.com/channel/{CHANNEL_ID}"
    assert canonical_channel_identity("https://youtube.com/@Veritasium/videos", CHANNEL_ID) == canonical
    assert canonical_channel_identity(f"https://m.youtube.com/channel/{CHANNEL_ID}/streams?view=0") == canonical
    assert canonical_channel_identity("https://www.youtube.com/@Veritasium/featured") == (
        canonical_channel_identity("youtube.com/@veritasium")
    )
    candidates = ChannelDiscoveryService().channels_from_search([
        {"uploader": "Veritasium", "channel_url": "https://www.youtube.com/@veritasium", "channel_id": CHANNEL_ID},
        {"uploader": "Veritasium", "channel_url": f"https://www.youtube.com/channel/{CHANNEL_ID}/videos"},
    ])
    assert [candidate.channel_key for candidate in candidates] == [canonical]

    # A follow through either variant is one follow.
    db = _session()
    member = User(id="m", username="m", display_name="M", role="viewer", is_active=True)
    db.add(member)
    db.flush()
    follows = MemberFollowService(db, validate_url=lambda url: url)
    first = follows.follow_channels(member, [FollowRequest(source_url=candidates[0].source_url, display_name="Veritasium")])
    again = follows.follow_channels(member, [FollowRequest(
        source_url=f"https://youtube.com/channel/{CHANNEL_ID}/streams", display_name="Veritasium",
    )])
    assert [o.status for o in first + again] == ["created", "existing"]


def test_youtube_partial_search() -> None:
    _SEARCH_CACHE.clear()
    failing = yt_dlp.utils.DownloadError("HTTP Error 503: Service Unavailable")
    service = YtDlpService(_session(), ydl_factory=_fake_ydl({"ytsearch": RECORDED_YTSEARCH[:1], "scsearch": failing}, []))

    response = service.source_search("veritasium", limit=4)

    assert [item.id for item in response.items] == ["dQw4w9WgXcQ"]
    assert [(error.source, error.retryable) for error in response.errors] == [("soundcloud", True)]
    assert response.errors[0].message


def test_youtube_no_credentials() -> None:
    _SEARCH_CACHE.clear()
    seen: list[dict] = []
    service = YtDlpService(_session(), ydl_factory=_fake_ydl({"ytsearch": RECORDED_YTSEARCH, "scsearch": []}, seen))
    service.source_search("veritasium", limit=4)
    service.youtube_search("another", limit=4)

    assert seen
    for options in seen:
        assert set(options) <= PUBLIC_OPTION_ALLOWLIST
        assert not {"cookiefile", "cookiesfrombrowser", "username", "password", "usenetrc", "http_headers"} & set(options)
