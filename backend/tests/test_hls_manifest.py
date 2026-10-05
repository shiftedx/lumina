from __future__ import annotations

import pytest

from app.services.hls_manifest import (
    MalformedManifestError,
    classify,
    rewrite_master,
    rewrite_media,
)


def _rewriter():
    """Record every reference and rewrite it to a compact, opaque address."""

    seen: list[tuple[str, str]] = []

    def rewrite(kind: str, url: str) -> str:
        seen.append((kind, url))
        return f"/relay/{kind}/{len(seen)}"

    return rewrite, seen


def test_classify_distinguishes_master_from_media() -> None:
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nvariant.m3u8\n"
    media = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    assert classify(master) == "master"
    assert classify(media) == "media"


def test_classify_rejects_documents_without_the_hls_tag() -> None:
    for text in ("", "   ", "not-a-playlist\nseg.ts\n", "#EXT-X-TARGETDURATION:6\n"):
        with pytest.raises(MalformedManifestError):
            classify(text)


def test_classify_rejects_recursive_documents_mixing_master_and_media_markers() -> None:
    recursive = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1000\n"
        "#EXTINF:6.0,\n"
        "seg0.ts\n"
    )
    with pytest.raises(MalformedManifestError):
        classify(recursive)


def test_rewrite_master_rewrites_variants_media_and_session_keys() -> None:
    master = (
        "#EXTM3U\n"
        "#EXT-X-SESSION-KEY:METHOD=AES-128,URI=\"https://cdn.example/master.key\"\n"
        "#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"aud\",NAME=\"English\",URI=\"audio/eng.m3u8\"\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS=\"avc1.4d401e,mp4a.40.2\",AUDIO=\"aud\"\n"
        "chunked/360p.m3u8\n"
        "#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=90000,URI=\"iframe/360p.m3u8\"\n"
    )
    rewrite, seen = _rewriter()
    out = rewrite_master(master, "https://cdn.example/vod/master.m3u8", rewrite)

    # Every upstream address is resolved to absolute and handed to the rewriter.
    assert ("key", "https://cdn.example/master.key") in seen
    assert ("media_playlist", "https://cdn.example/vod/audio/eng.m3u8") in seen
    assert ("media_playlist", "https://cdn.example/vod/chunked/360p.m3u8") in seen
    assert ("media_playlist", "https://cdn.example/vod/iframe/360p.m3u8") in seen
    # No upstream host survives in the rewritten output.
    assert "cdn.example" not in out
    assert "https://" not in out
    # Descriptive attributes are preserved so the browser can pick a rendition.
    assert "RESOLUTION=640x360" in out
    assert "CODECS=\"avc1.4d401e,mp4a.40.2\"" in out


def test_rewrite_master_rejects_media_segments_in_a_master() -> None:
    poisoned = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n#EXTINF:6.0,\nseg0.ts\n"
    rewrite, _seen = _rewriter()
    with pytest.raises(MalformedManifestError):
        rewrite_master(poisoned, "https://cdn.example/master.m3u8", rewrite)


def test_rewrite_media_rewrites_segments_keys_maps_and_preserves_discontinuities() -> None:
    media = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        "#EXT-X-TARGETDURATION:6\n"
        "#EXT-X-MAP:URI=\"init.mp4\"\n"
        "#EXT-X-KEY:METHOD=AES-128,URI=\"../keys/k1.bin\",IV=0x1\n"
        "#EXTINF:6.0,\n"
        "seg0.m4s\n"
        "#EXT-X-DISCONTINUITY\n"
        "#EXTINF:6.0,\n"
        "https://cdn.example/vod/seg1.m4s\n"
        "#EXT-X-BYTERANGE:1000@0\n"
        "#EXTINF:6.0,\n"
        "seg2.m4s\n"
        "#EXT-X-ENDLIST\n"
    )
    rewrite, seen = _rewriter()
    out = rewrite_media(media, "https://cdn.example/vod/chunked/360p.m3u8", rewrite)

    kinds = [kind for kind, _ in seen]
    urls = [url for _, url in seen]
    assert ("map", "https://cdn.example/vod/chunked/init.mp4") in seen
    assert ("key", "https://cdn.example/vod/keys/k1.bin") in seen  # relative "../"
    assert "https://cdn.example/vod/chunked/seg0.m4s" in urls
    assert "https://cdn.example/vod/seg1.m4s" in urls  # absolute preserved through resolve
    assert kinds.count("segment") == 3
    # Structural directives survive untouched.
    assert "#EXT-X-DISCONTINUITY" in out
    assert "#EXT-X-BYTERANGE:1000@0" in out
    assert "#EXTINF:6.0," in out
    assert "IV=0x1" in out
    assert "cdn.example" not in out


def test_rewrite_media_leaves_method_none_keys_without_a_uri_untouched() -> None:
    media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-KEY:METHOD=NONE\n"
        "#EXTINF:6.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    rewrite, seen = _rewriter()
    out = rewrite_media(media, "https://cdn.example/m.m3u8", rewrite)
    assert [kind for kind, _ in seen] == ["segment"]
    assert "#EXT-X-KEY:METHOD=NONE" in out


def test_rewrite_media_rejects_a_media_playlist_that_nests_a_master() -> None:
    nested = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg0.ts\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1\ninner.m3u8\n"
    )
    rewrite, _seen = _rewriter()
    with pytest.raises(MalformedManifestError):
        rewrite_media(nested, "https://cdn.example/m.m3u8", rewrite)


def test_rewrite_rejects_documents_missing_the_hls_header() -> None:
    rewrite, _seen = _rewriter()
    with pytest.raises(MalformedManifestError):
        rewrite_media("seg0.ts\n", "https://cdn.example/m.m3u8", rewrite)
    with pytest.raises(MalformedManifestError):
        rewrite_master("#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n", "https://cdn.example/m.m3u8", rewrite)


def test_rewrite_master_rejects_uri_bearing_session_data() -> None:
    poisoned = (
        "#EXTM3U\n"
        "#EXT-X-SESSION-DATA:DATA-ID=\"com.example.move\",URI=\"https://cdn.example/move.json?sig=SECRET\"\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n"
    )
    rewrite, seen = _rewriter()
    with pytest.raises(MalformedManifestError) as caught:
        rewrite_master(poisoned, "https://cdn.example/master.m3u8", rewrite)
    assert "SECRET" not in str(caught.value)
    assert not any("SECRET" in url for _kind, url in seen)


def test_rewrite_master_rejects_content_steering_server_uri() -> None:
    poisoned = (
        "#EXTM3U\n"
        "#EXT-X-CONTENT-STEERING:SERVER-URI=\"https://cdn.example/steer?sig=SECRET\",PATHWAY-ID=\"a\"\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n"
    )
    rewrite, _seen = _rewriter()
    with pytest.raises(MalformedManifestError) as caught:
        rewrite_master(poisoned, "https://cdn.example/master.m3u8", rewrite)
    assert "SECRET" not in str(caught.value)


def test_rewrite_media_rewrites_unquoted_key_uri_without_leaking() -> None:
    # Lenient HLS parsers accept an unquoted URI; the browser would fetch it
    # directly, bypassing the relay. It must be rewritten, not passed through.
    media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXT-X-KEY:METHOD=AES-128,URI=https://cdn.example/keys/k.bin?sig=SECRETTOKEN\n"
        "#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    rewrite, seen = _rewriter()
    out = rewrite_media(media, "https://cdn.example/m.m3u8", rewrite)
    assert "SECRETTOKEN" not in out
    assert "cdn.example" not in out
    assert ("key", "https://cdn.example/keys/k.bin?sig=SECRETTOKEN") in seen


def test_rewrite_media_rewrites_map_uri_with_whitespace_around_equals() -> None:
    media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXT-X-MAP:URI =\"https://cdn.example/init.mp4?sig=SECRETTOKEN\"\n"
        "#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    rewrite, seen = _rewriter()
    out = rewrite_media(media, "https://cdn.example/m.m3u8", rewrite)
    assert "SECRETTOKEN" not in out
    assert "cdn.example" not in out
    assert ("map", "https://cdn.example/init.mp4?sig=SECRETTOKEN") in seen


def test_rewrite_media_fails_closed_on_residual_address_on_a_recognized_tag() -> None:
    # A recognized tag carrying an address in a non-URI attribute the rewriter does
    # not target must fail closed rather than leak the residual upstream URL.
    media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXT-X-KEY:METHOD=AES-128,URI=\"k.bin\",KEYFORMAT=\"https://drm.example/leak?sig=SECRETTOKEN\"\n"
        "#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    rewrite, _seen = _rewriter()
    with pytest.raises(MalformedManifestError):
        rewrite_media(media, "https://cdn.example/m.m3u8", rewrite)


def test_rewrite_media_drops_interstitial_asset_list_daterange_without_leaking() -> None:
    # HLS Interstitials: X-ASSET-LIST is a standardized, player-dereferenced URI
    # whose attribute name ends in LIST, not URI, so the name-based detector misses
    # it. DATERANGE is timed metadata the player never needs to decode media, so an
    # address-bearing DATERANGE is dropped whole: the address must not survive into
    # the output or reach the rewriter, and playback must continue.
    media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXT-X-DATERANGE:ID=\"ad1\",CLASS=\"com.apple.hls.interstitial\","
        "X-ASSET-LIST=\"https://attacker.example/list.json?sig=SECRETTOKEN\"\n"
        "#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    rewrite, seen = _rewriter()
    out = rewrite_media(media, "https://cdn.example/m.m3u8", rewrite)
    assert "SECRETTOKEN" not in out
    assert "EXT-X-DATERANGE" not in out
    assert not any("SECRETTOKEN" in url for _kind, url in seen)
    assert ("segment", "https://cdn.example/seg0.ts") in seen


def test_rewrite_media_drops_protocol_relative_address_daterange() -> None:
    media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXT-X-DATERANGE:ID=\"ad1\",X-ASSET-LIST=\"//attacker.example/list.json?sig=SECRETTOKEN\"\n"
        "#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    rewrite, _seen = _rewriter()
    out = rewrite_media(media, "https://cdn.example/m.m3u8", rewrite)
    assert "SECRETTOKEN" not in out
    assert "EXT-X-DATERANGE" not in out


@pytest.mark.parametrize(
    "asset_list",
    [
        "https:\\/\\/attacker.example/list.json?sig=SECRETTOKEN",
        "https:/\\attacker.example/list.json?sig=SECRETTOKEN",
        "\\/\\/attacker.example/list.json?sig=SECRETTOKEN",
    ],
)
def test_rewrite_media_drops_backslash_obfuscated_address_dateranges(asset_list: str) -> None:
    # WHATWG new URL() (a hls.js code path) lenifies backslashes to slashes for
    # special schemes, so a backslash-obfuscated separator that carries no literal
    # "://" is still an address form and the DATERANGE carrying it is dropped.
    media = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        f"#EXT-X-DATERANGE:ID=\"ad1\",X-ASSET-LIST=\"{asset_list}\"\n"
        "#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    rewrite, _seen = _rewriter()
    out = rewrite_media(media, "https://cdn.example/m.m3u8", rewrite)
    assert "SECRETTOKEN" not in out
    assert "EXT-X-DATERANGE" not in out


def test_rewrite_media_serves_twitch_live_playlist_with_trigger_daterange() -> None:
    # Twitch's live media playlists carry DATERANGE metadata whose
    # X-TV-TWITCH-TRIGGER-URL attribute holds an upstream address. The relay must
    # drop that metadata line (never reject the playlist, never leak the address)
    # while address-free DATERANGE metadata and the fMP4 init map survive.
    media = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:6\n"
        "#EXT-X-TARGETDURATION:6\n"
        "#EXT-X-MEDIA-SEQUENCE:6894\n"
        "#EXT-X-TWITCH-ELAPSED-SECS:13788.000\n"
        "#EXT-X-DATERANGE:ID=\"playlist-creation-1\",CLASS=\"timestamp\","
        "START-DATE=\"2026-07-21T04:07:58.734Z\",END-ON-NEXT=YES,X-SERVER-TIME=\"1784606878.73\"\n"
        "#EXT-X-DATERANGE:ID=\"trigger-1\",CLASS=\"twitch-trigger\","
        "START-DATE=\"2026-07-21T04:07:26.704Z\",END-ON-NEXT=YES,"
        "X-TV-TWITCH-TRIGGER-URL=\"https://use23.playlist.ttvnw.net/trigger/SECRETTOKEN\"\n"
        "#EXT-X-MAP:URI=\"https://cdn.example/init.mp4?dna=SECRETTOKEN2\"\n"
        "#EXT-X-PROGRAM-DATE-TIME:2026-07-21T04:07:26.704Z\n"
        "#EXTINF:2.000,live\nhttps://cdn.example/seg1.mp4\n"
    )
    rewrite, seen = _rewriter()
    out = rewrite_media(media, "https://cdn.example/m.m3u8", rewrite)
    assert "SECRETTOKEN" not in out
    assert "twitch-trigger" not in out
    assert "CLASS=\"timestamp\"" in out  # address-free metadata survives verbatim
    assert "#EXT-X-TWITCH-ELAPSED-SECS:13788.000" in out
    assert ("map", "https://cdn.example/init.mp4?dna=SECRETTOKEN2") in seen
    assert ("segment", "https://cdn.example/seg1.mp4") in seen
    assert not any("ttvnw.net" in url for _kind, url in seen)


def test_rewrite_master_drops_interstitial_asset_list_daterange_without_leaking() -> None:
    master = (
        "#EXTM3U\n"
        "#EXT-X-DATERANGE:ID=\"ad1\",X-ASSET-LIST=\"https://attacker.example/list.json?sig=SECRETTOKEN\"\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n"
    )
    rewrite, seen = _rewriter()
    out = rewrite_master(master, "https://cdn.example/master.m3u8", rewrite)
    assert "SECRETTOKEN" not in out
    assert "EXT-X-DATERANGE" not in out
    assert ("media_playlist", "https://cdn.example/v.m3u8") in seen


def test_rewrite_master_allows_uri_free_custom_and_value_form_tags() -> None:
    benign = (
        "#EXTM3U\n"
        "#EXT-X-TWITCH-INFO:ORIGIN=\"s3\",B=\"false\",REGION=\"EU\"\n"
        "#EXT-X-SESSION-DATA:DATA-ID=\"com.example.title\",VALUE=\"My VOD\"\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n"
    )
    rewrite, seen = _rewriter()
    out = rewrite_master(benign, "https://cdn.example/master.m3u8", rewrite)
    assert "#EXT-X-TWITCH-INFO:ORIGIN=\"s3\"" in out
    assert "VALUE=\"My VOD\"" in out
    assert [kind for kind, _ in seen] == ["media_playlist"]


def test_rewrite_media_rejects_uri_bearing_rendition_report() -> None:
    poisoned = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXT-X-RENDITION-REPORT:URI=\"https://cdn.example/1080/report.m3u8?sig=SECRET\",LAST-MSN=9\n"
        "#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    rewrite, seen = _rewriter()
    with pytest.raises(MalformedManifestError) as caught:
        rewrite_media(poisoned, "https://cdn.example/m.m3u8", rewrite)
    assert "SECRET" not in str(caught.value)
    assert not any("SECRET" in url for _kind, url in seen)


def test_rewrite_propagates_a_rewriter_veto_as_fail_closed() -> None:
    def veto(kind: str, url: str) -> str:
        raise MalformedManifestError("blocked address")

    media = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nhttp://127.0.0.1/seg0.ts\n#EXT-X-ENDLIST\n"
    with pytest.raises(MalformedManifestError):
        rewrite_media(media, "https://cdn.example/m.m3u8", veto)
