import json
from pathlib import Path

import pytest

from app.models import LibraryItem, Transcript, TranscriptCue, User
from app.services.transcripts import MAX_TRACK_BYTES, TranscriptError, TranscriptService, parse_caption

VTT = b"""WEBVTT
Kind: captions
Language: en

NOTE a comment

00:00:01.000 --> 00:00:02.500 align:start position:0%
Hello <b>world</b> &amp; friends

1:00:03.250 --> 1:00:04.000
<v Speaker>Later line</v>
"""

SRT = b"""1
00:00:01,000 --> 00:00:02,000
Bonjour

2
00:00:02,000 --> 00:00:03,000
le <i>monde</i>
"""

# YouTube rolling auto-captions repeat the previous line before the new one.
ROLLING = (
    b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n \nhello<00:00:01.000><c> world</c>\n\n"
    b"00:00:02.000 --> 00:00:02.010\nhello world\n \n\n"
    b"00:00:02.010 --> 00:00:04.000\nhello world\nthis is<00:00:03.000><c> next</c>\n"
)


def test_transcript_vtt_srt_languages() -> None:
    assert parse_caption(VTT, "vtt") == [(1000, 2500, "Hello world & friends"), (3603250, 3604000, "Later line")]
    assert parse_caption(SRT, "srt") == [(1000, 2000, "Bonjour"), (2000, 3000, "le monde")]
    assert parse_caption(ROLLING, "vtt") == [(0, 2010, "hello world"), (2010, 4000, "this is next")]
    json3 = {"events": [{"tStartMs": 0, "dDurationMs": 900}, {"tStartMs": 100, "dDurationMs": 900, "segs": [{"utf8": "Hi "}, {"utf8": "there"}]}]}
    assert parse_caption(json.dumps(json3).encode(), "json3") == [(100, 1000, "Hi there")]


@pytest.mark.parametrize(
    ("data", "ext"),
    [
        (b"WEBVTT\n\n00:00:xx.000 --> 00:00:02.000\nbad", "vtt"),
        (b"WEBVTT\n\n00:00:05.000 --> 00:00:02.000\nbackwards", "vtt"),
        (b"WEBVTT\n\nno cues here", "vtt"),
        (b"\xff\xfe\x00bad", "vtt"),
        (b"[" * 100_000, "json3"),
        (b'{"events": [{"tStartMs": "0", "segs": []}]}', "json3"),
        (b"WEBVTT\n\n" + b"x" * MAX_TRACK_BYTES, "vtt"),
    ],
)
def test_caption_malformed_bounded(data: bytes, ext: str) -> None:
    with pytest.raises(TranscriptError):
        parse_caption(data, ext)


def test_transcript_revision_idempotent(db_factory) -> None:
    with db_factory() as db:
        service = TranscriptService(db)
        first = service.store("item", language="en", source_kind="source_caption", cues=[(0, 1000, "a")])
        again = service.store("item", language="en", source_kind="source_caption", cues=[(0, 1000, "a")])
        changed = service.store("item", language="en", source_kind="source_caption", cues=[(0, 1000, "b")])
        asr = service.store("item", language="en", source_kind="asr", cues=[(0, 1000, "a")], model_label="whisper")
        assert again.id == first.id
        assert (first.revision, changed.revision, asr.revision) == (1, 2, 1)
        assert db.query(TranscriptCue).filter_by(transcript_id=first.id).one().text == "a"
        with pytest.raises(TranscriptError):
            service.store("item", language="en", source_kind="bogus", cues=[(0, 1, "a")])


def test_download_captions_ingest_skips_bad_and_foreign_tracks(db_factory, tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "v.en.vtt").write_bytes(VTT)
    (media / "v.de.vtt").write_bytes(b"garbage")
    (tmp_path / "v.fr.vtt").write_bytes(SRT)
    info = {
        "filepath": str(media / "v.mp4"),
        "requested_subtitles": {
            "en": {"ext": "vtt", "filepath": str(media / "v.en.vtt")},
            "de": {"ext": "vtt", "filepath": str(media / "v.de.vtt")},
            "fr": {"ext": "vtt", "filepath": str(tmp_path / "v.fr.vtt")},
            "live_chat": {"ext": "json", "filepath": str(media / "v.live_chat.json")},
        },
    }
    item = LibraryItem(id="item", title="t", metadata_json={})
    with db_factory() as db:
        TranscriptService(db).ingest_download_captions([(info, item)])
        assert [(t.language, t.cue_count) for t in db.query(Transcript).all()] == [("en", 2)]


def test_transcript_private_access(db_factory, api_client) -> None:
    owner = User(id="owner", username="owner", display_name="Owner", role="viewer", is_active=True)
    other = User(id="other", username="other", display_name="Other", role="admin", is_active=True)
    with db_factory.begin() as db:
        db.add_all([owner, other, LibraryItem(id="priv", user_id=owner.id, visibility="private", title="p", metadata_json={}, status="available")])
    with db_factory() as db:
        transcript = TranscriptService(db).store(
            "priv", language="en", source_kind="source_caption", cues=[(i * 1000, i * 1000 + 900, f"cue {i} secretword") for i in range(3)]
        )

    current = {"user": owner}
    client = api_client(user=lambda: current["user"], base_url="http://localhost")
    listed = client.get("/api/library/priv/transcripts")
    assert [t["id"] for t in listed.json()] == [transcript.id]
    page = client.get(f"/api/transcripts/{transcript.id}/cues", params={"limit": 2}).json()
    assert [c["ordinal"] for c in page["items"]] == [0, 1] and page["next_cursor"] == "1"
    rest = client.get(f"/api/transcripts/{transcript.id}/cues", params={"cursor": page["next_cursor"]}).json()
    assert [c["ordinal"] for c in rest["items"]] == [2] and rest["next_cursor"] is None
    hits = client.get(f"/api/transcripts/{transcript.id}/search", params={"q": "cue 1"}).json()
    assert [(h["ordinal"], h["start_ms"]) for h in hits] == [(1, 1000)]
    assert client.get(f"/api/transcripts/{transcript.id}/search", params={"q": "%"}).json() == []
    assert client.get(f"/api/transcripts/{transcript.id}/cues", params={"cursor": "1 OR 1"}).status_code == 422

    current["user"] = other
    assert client.get("/api/library/priv/transcripts").status_code == 404
    assert client.get(f"/api/transcripts/{transcript.id}").status_code == 404
    assert client.get(f"/api/transcripts/{transcript.id}/cues").status_code == 404
    assert client.get(f"/api/transcripts/{transcript.id}/search", params={"q": "secretword"}).status_code == 404
