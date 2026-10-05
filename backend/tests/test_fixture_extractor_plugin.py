from __future__ import annotations

from yt_dlp import YoutubeDL


def test_artifact_fixture_extractor_is_loaded_and_reserved_domain_scoped() -> None:
    with YoutubeDL({"quiet": True}) as ydl:
        extractor_class = ydl._ies["LuminaFixture"]
        extractor = extractor_class(ydl)

    assert extractor.suitable("http://fixture.lumina.invalid:8080/progressive.mp4")
    assert extractor.suitable("http://fixture.lumina.invalid:8080/protected.mp4")
    assert extractor.suitable("http://fixture.lumina.invalid:8080/slow.mp4")
    assert extractor.suitable("http://fixture.lumina.invalid:8080/partial-playlist")
    assert not extractor.suitable("https://example.com/progressive.mp4")
    assert not extractor.suitable("http://fixture.lumina.invalid.example:8080/progressive.mp4")


def test_artifact_fixture_extractor_exposes_a_synthetic_channel_and_partial_playlist(monkeypatch) -> None:
    with YoutubeDL({"quiet": True}) as ydl:
        extractor_class = ydl._ies["LuminaFixture"]
        extractor = extractor_class(ydl)

    class Response:
        url = "http://fixture.lumina.invalid:8080/partial-playlist"

        def close(self) -> None:
            pass

    monkeypatch.setattr(extractor, "_request_webpage", lambda *_args, **_kwargs: Response())
    result = extractor._real_extract(Response.url)

    assert result["_type"] == "playlist"
    assert [entry["id"] for entry in result["entries"]] == ["partial-available", "partial-unavailable"]
    assert result["channel_url"] == Response.url


def test_artifact_fixture_extractor_declares_the_generated_open_codecs(monkeypatch) -> None:
    with YoutubeDL({"quiet": True}) as ydl:
        extractor_class = ydl._ies["LuminaFixture"]
        extractor = extractor_class(ydl)

    class Response:
        def __init__(self, url: str) -> None:
            self.url = url

        def close(self) -> None:
            pass

    monkeypatch.setattr(extractor, "_request_webpage", lambda url, *_args, **_kwargs: Response(url))
    progressive = extractor._real_extract("http://fixture.lumina.invalid:8080/progressive.mp4")
    split = extractor._real_extract("http://fixture.lumina.invalid:8080/manifest.mpd")
    audio = extractor._real_extract("http://fixture.lumina.invalid:8080/audio.m4a")

    assert [(item["vcodec"], item["acodec"]) for item in progressive["formats"]] == [("av01.0.04M.08", "opus")]
    assert [(item["vcodec"], item["acodec"]) for item in split["formats"]] == [
        ("av01.0.04M.08", "none"),
        ("none", "opus"),
    ]
    assert [(item["vcodec"], item["acodec"]) for item in audio["formats"]] == [("none", "opus")]
