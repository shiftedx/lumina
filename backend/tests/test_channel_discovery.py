from __future__ import annotations

from datetime import UTC, datetime

from app.services.channel_discovery import (
    ChannelDiscoveryService,
    channel_display_key,
    normalize_channel_source_url,
)
from app.services.popular_discovery import PopularCategorySnapshot, PopularItem, PopularSnapshot


REFERENCE = datetime(2026, 1, 1, tzinfo=UTC)


def _item(
    item_id: str,
    categories: tuple[str, ...],
    *,
    uploader: str,
    uploader_url: str | None,
    view_count: int | None = 100,
    availability: str | None = "public",
) -> PopularItem:
    return PopularItem(
        id=item_id, title=item_id.title(), uploader=uploader, duration=120, thumbnail=None,
        artwork_url=None, webpage_url=f"https://www.youtube.com/watch?v={item_id}", view_count=view_count,
        availability=availability, published_at=None, source="youtube", source_label="YouTube",
        capabilities=None, category_keys=categories, uploader_url=uploader_url, uploader_id=None,
    )


def _snapshot(*items: PopularItem, categories: tuple[PopularCategorySnapshot, ...]) -> PopularSnapshot:
    return PopularSnapshot(
        items=items, categories=categories, state="ready", refreshing=False, stale=False,
        last_success_at=REFERENCE, refreshed_at=REFERENCE, next_refresh_at=None, error=None,
    )


def _ready(key: str) -> PopularCategorySnapshot:
    return PopularCategorySnapshot(key, key.title(), "ready", REFERENCE, None)


# --- Stable channel identity across every surface ---------------------------


def test_channel_identity_is_stable_across_host_variants_and_trailing_slash() -> None:
    canonical = normalize_channel_source_url("https://www.youtube.com/@veritasium")
    assert canonical == "https://www.youtube.com/@veritasium"
    # A pasted bare host, an m. host, and a trailing slash all resolve to the
    # same durable identity a category suggestion or existing follow uses.
    assert normalize_channel_source_url("youtube.com/@veritasium/") == canonical
    assert normalize_channel_source_url("https://m.youtube.com/@veritasium") == canonical
    assert normalize_channel_source_url("HTTPS://WWW.YOUTUBE.COM/@veritasium#tab") == canonical


def test_same_channel_from_search_paste_and_category_shares_one_identity() -> None:
    service = ChannelDiscoveryService()
    snapshot = _snapshot(
        _item("v1", ("science-technology",), uploader="Veritasium", uploader_url="https://www.youtube.com/@veritasium"),
        categories=(_ready("science-technology"),),
    )
    from_category = service.suggestions_for_categories(snapshot, ["science-technology"])[0].channels[0]
    from_search = service.channels_from_search(
        [{"uploader": "Veritasium", "channel_url": "https://m.youtube.com/@veritasium", "avatar_url": "https://img/av.jpg"}]
    )[0]
    from_paste = normalize_channel_source_url("youtube.com/@veritasium/")

    assert from_category.channel_key == from_search.channel_key == from_paste


# --- Category ranking from fresh discovery ----------------------------------


def test_category_suggestions_rank_channels_by_discovery_order_and_dedupe() -> None:
    service = ChannelDiscoveryService()
    snapshot = _snapshot(
        _item("a", ("music",), uploader="Alpha", uploader_url="https://www.youtube.com/@alpha", view_count=500),
        _item("b", ("music",), uploader="Beta", uploader_url="https://www.youtube.com/@beta", view_count=200),
        _item("a2", ("music",), uploader="Alpha", uploader_url="https://www.youtube.com/@alpha", view_count=50),
        categories=(_ready("music"),),
    )
    suggestions = service.suggestions_for_categories(snapshot, ["music"])
    assert len(suggestions) == 1
    music = suggestions[0]
    assert music.state == "ranked"
    assert [channel.display_name for channel in music.channels] == ["Alpha", "Beta"]


def test_category_suggestions_omit_unavailable_and_addressless_candidates() -> None:
    service = ChannelDiscoveryService()
    snapshot = _snapshot(
        _item("live", ("music",), uploader="Private One", uploader_url="https://www.youtube.com/@private", availability="private"),
        _item("noaddr", ("music",), uploader="No Address", uploader_url=None),
        _item("ok", ("music",), uploader="Good", uploader_url="https://www.youtube.com/@good"),
        categories=(_ready("music"),),
    )
    channels = service.suggestions_for_categories(snapshot, ["music"])[0].channels
    assert [channel.display_name for channel in channels] == ["Good"]


# --- Curated fallback for cold, empty, stale, or failed provider states ------


def test_cold_or_failed_categories_fall_back_to_curated_channels() -> None:
    curated = {"music": (("Curated Band", "https://www.youtube.com/@curatedband"),)}
    service = ChannelDiscoveryService(curated=curated)
    snapshot = PopularSnapshot(
        items=(), categories=(PopularCategorySnapshot("music", "Music", "failed", None, None),),
        state="failed", refreshing=False, stale=False, last_success_at=None, refreshed_at=None,
        next_refresh_at=None, error="down",
    )
    suggestions = service.suggestions_for_categories(snapshot, ["music"])
    assert suggestions[0].state == "curated"
    assert [channel.display_name for channel in suggestions[0].channels] == ["Curated Band"]
    assert suggestions[0].channels[0].channel_key == normalize_channel_source_url("https://www.youtube.com/@curatedband")


def test_ready_category_with_no_addressable_channels_falls_back_to_curated() -> None:
    curated = {"music": (("Curated Band", "https://www.youtube.com/@curatedband"),)}
    service = ChannelDiscoveryService(curated=curated)
    snapshot = _snapshot(
        _item("noaddr", ("music",), uploader="No Address", uploader_url=None),
        categories=(_ready("music"),),
    )
    assert service.suggestions_for_categories(snapshot, ["music"])[0].state == "curated"


# --- Existing follows are recognized everywhere -----------------------------


def test_existing_follows_are_marked_across_search_and_category() -> None:
    service = ChannelDiscoveryService()
    followed = frozenset({normalize_channel_source_url("https://www.youtube.com/@veritasium")})
    snapshot = _snapshot(
        _item("v1", ("science-technology",), uploader="Veritasium", uploader_url="https://www.youtube.com/@veritasium"),
        _item("v2", ("science-technology",), uploader="Other", uploader_url="https://www.youtube.com/@other"),
        categories=(_ready("science-technology"),),
    )
    channels = service.suggestions_for_categories(snapshot, ["science-technology"], followed_identities=followed)[0].channels
    by_name = {channel.display_name: channel for channel in channels}
    assert by_name["Veritasium"].following is True
    assert by_name["Other"].following is False

    searched = service.channels_from_search(
        [{"uploader": "Veritasium", "channel_url": "https://www.youtube.com/@veritasium"}],
        followed_identities=followed,
    )
    assert searched[0].following is True


def test_search_provides_display_name_and_accessible_artwork_fallback() -> None:
    service = ChannelDiscoveryService()
    channels = service.channels_from_search(
        [
            {"uploader": "With Art", "channel_url": "https://www.youtube.com/@art", "avatar_url": "https://img/a.jpg"},
            {"uploader": "No Art", "channel_url": "https://www.youtube.com/@noart"},
            {"uploader": "", "channel_url": "https://www.youtube.com/@nameless"},
        ]
    )
    assert [(c.display_name, c.artwork_url) for c in channels] == [
        ("With Art", "https://img/a.jpg"),
        ("No Art", None),
    ]


def test_display_key_matches_recommendation_seam_casefolding() -> None:
    # The #88 Home seam matches a casefolded uploader display name, so the key
    # #87 hands it must be derived exactly the same way.
    assert channel_display_key("  Veritasium ") == "veritasium"
    assert channel_display_key("MKBHD") == "mkbhd"


def test_normalize_rejects_a_malformed_port_without_crashing() -> None:
    # urlsplit succeeds but .port raises ValueError on a non-numeric port; a
    # crafted address must resolve to None (an invalid follow), never a crash.
    assert normalize_channel_source_url("https://youtube.com:abc/@x") is None
