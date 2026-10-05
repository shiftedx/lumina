"""Transcripts become subtitle tracks (labels, ISO codes, SRT/VTT, stable t: indices, Jellyfin streams)."""
from __future__ import annotations

import pytest

from app.models import TranscriptCue
from app.services.transcripts import (
    TranscriptError,
    TranscriptService,
    jellyfin_subtitle_streams,
    language_label,
    parse_caption,
    render,
    to_iso639_2,
)

CUES = [(0, 1500, "Tom & Jerry < 3"), (2000, 3500, "Second line"), (7_200_001, 7_200_900, "Two hours in")]


@pytest.mark.parametrize("fmt", ["srt", "vtt"])
def test_render_round_trips_through_parse_caption(fmt: str) -> None:
    text = render(CUES, fmt)
    assert parse_caption(text.encode(), fmt) == CUES
    assert text.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nTom &amp; Jerry &lt; 3") if fmt == "vtt" else text.startswith("1\n00:00:00,000 --> 00:00:01,500\n")


@pytest.mark.parametrize("fmt", ["srt", "vtt"])
def test_hostile_cue_text_cannot_inject_cues(fmt: str) -> None:
    hostile = [
        (0, 1000, "one\n\n2\n00:00:05,000 --> 00:00:06,000\ninjected"),
        (1000, 2000, "a --> b"),
        (2000, 3000, "<c.red>hi</c> <b>x"),
    ]
    parsed = parse_caption(render(hostile, fmt).encode(), fmt)
    assert [(start, end) for start, end, _ in parsed] == [(0, 1000), (1000, 2000), (2000, 3000)]


def test_render_rejects_unknown_format() -> None:
    with pytest.raises(TranscriptError):
        render(CUES, "ass")


def test_language_labels_and_iso_codes() -> None:
    assert language_label("english", "asr") == "English (generated)"  # ASR verbose_json returns full names
    assert language_label("es", "translated") == "Spanish (translated)"
    assert language_label("en", "source_caption") == "English"
    assert language_label("pt-BR", "synced") == "Portuguese (synced)"
    assert language_label("xx", "asr") == "xx (generated)"
    assert language_label("und", "asr") == "Unknown (generated)"
    assert (to_iso639_2("fra"), to_iso639_2("French"), to_iso639_2("en_US"), to_iso639_2("klingon"), to_iso639_2(None)) == ("fre", "fre", "eng", None, None)


def test_store_keeps_words_and_derived_from(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        service = TranscriptService(db)
        transcript = service.store(
            "item", language="en", source_kind="asr", cues=[(0, 900, "hello there")],
            words=[[[0, 400, "hello"], [500, 900, "there"]]], derived_from=None,
        )
        cue = db.query(TranscriptCue).filter_by(transcript_id=transcript.id).one()
        assert cue.words == [[0, 400, "hello"], [500, 900, "there"]]
        synced = service.store("item", language="en", source_kind="synced", cues=[(100, 1000, "hello there")], derived_from=transcript.id)
        assert synced.derived_from == transcript.id
        with pytest.raises(TranscriptError):
            service.store("item", language="en", source_kind="asr", cues=[(0, 1, "a"), (2, 3, "b")], words=[None])


def test_track_indices_only_append(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        service = TranscriptService(db)
        asr = service.store("item", language="english", source_kind="asr", cues=[(0, 900, "one")])
        caption = service.store("item", language="en", source_kind="source_caption", cues=[(0, 900, "uno")])
        service.store("item", language="en", source_kind="source_caption", cues=[(0, 900, "side")], derived_from="sidecar:a.en.srt")
        service.store("item", language="en", source_kind="source_caption", cues=[(0, 900, "emb")], derived_from="stream:2")
        before = [track.id for track in service.subtitle_tracks("item")]
        assert before == [f"t:{asr.id}", f"t:{caption.id}"]  # mirrors of s:/e: tracks are not t: tracks
        translated = service.store("item", language="spa", source_kind="translated", cues=[(0, 900, "uno")], derived_from=asr.id)
        newer_asr = service.store("item", language="english", source_kind="asr", cues=[(0, 900, "one!")])
        tracks = service.subtitle_tracks("item")
    assert [track.id for track in tracks] == [f"t:{newer_asr.id}", f"t:{caption.id}", f"t:{translated.id}"]
    assert [(t.label, t.language, t.origin, t.format) for t in tracks] == [
        ("English (generated)", "eng", "generated", "text"),
        ("English", "eng", "caption", "text"),
        ("Spanish (translated)", "spa", "translated", "text"),
    ]
    assert tracks[0].url == f"/api/library/item/subtitle-tracks/t:{newer_asr.id}.vtt"


def test_synced_track_from_an_s_mirror_source_is_listed(db_factory) -> None:  # noqa: ANN001
    """Only source_caption rows that mirror an s:/e: track are hidden; a synced/translated
    result derived from that same mirror (resolve_track('s:n') lookup) is a real t: track."""
    with db_factory() as db:
        service = TranscriptService(db)
        service.store("item", language="en", source_kind="source_caption", cues=[(0, 900, "side")], derived_from="sidecar:a.en.srt")
        synced = service.store("item", language="en", source_kind="synced", cues=[(50, 950, "side")], derived_from="sidecar:a.en.srt")
        tracks = service.subtitle_tracks("item")
    assert [track.id for track in tracks] == [f"t:{synced.id}"]
    assert tracks[0].label == "English (synced)"


def test_timing_transcript_prefers_asr_then_synced_then_captions(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        service = TranscriptService(db)
        assert service.timing_transcript("item") is None
        caption = service.store("item", language="en", source_kind="source_caption", cues=[(0, 900, "a")])
        assert service.timing_transcript("item").id == caption.id
        synced = service.store("item", language="en", source_kind="synced", cues=[(0, 900, "a")], derived_from=caption.id)
        service.store("item", language="es", source_kind="translated", cues=[(0, 900, "b")], derived_from=caption.id)
        assert service.timing_transcript("item").id == synced.id  # a translation never wins
        asr = service.store("item", language="en", source_kind="asr", cues=[(0, 900, "a")])
        assert service.timing_transcript("item").id == asr.id


def test_render_transcript_from_the_database(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        service = TranscriptService(db)
        transcript = service.store("item", language="en", source_kind="asr", cues=CUES)
        assert service.cue_tuples(transcript.id) == CUES
        assert service.render_transcript(transcript.id, "srt") == render(CUES, "srt")


def test_jellyfin_transcript_streams_append_after_first_index(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        service = TranscriptService(db)
        service.store("item", language="english", source_kind="asr", cues=[(0, 900, "one")])
        streams = jellyfin_subtitle_streams(service.subtitle_tracks("item"), 3, lambda index: f"/Videos/x/x/Subtitles/{index}/Stream.srt")
    assert streams == [{
        "Index": 3, "Type": "Subtitle", "Codec": "subrip", "Language": "eng",
        "Title": "English (generated)", "DisplayTitle": "English (generated)",
        "IsDefault": False, "IsForced": False, "IsHearingImpaired": False, "IsExternal": True,
        "IsTextSubtitleStream": True, "SupportsExternalStream": True,
        "DeliveryMethod": "External", "DeliveryUrl": "/Videos/x/x/Subtitles/3/Stream.srt",
    }]
