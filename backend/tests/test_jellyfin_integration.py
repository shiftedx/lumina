"""Integration: Jellyfin routes that combine tracks."""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import settings
from app.main import app
from app.models import User
from app.routers import jellyfin_integration, local_playback
from app.security import hash_password
from app.services import local_playback_sessions as lps
from app.services.artwork import ArtworkResponse
from app.services.hwaccel import HwStatus, hwaccel
from app.services.jellyfin_playback import PlaybackRequest, playback_request, subtitle_format, transcoding_fields
from app.services.media_titles import jellyfin_id, parse_item_id, synthetic_id
from app.services.rate_limit import rate_limiter
from app.services.transcripts import TranscriptService
from app.services.users import UserService
from tests.test_v1_probe import make_media
from tests.test_v1_sidecars import PASSWORD, scan, write
from tests.test_v1_account_lifecycle import ORIGIN

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed")
# User ids are uuids: jellyfin_id(user.id) needs one. Usernames stay admin/bob.
ADMIN_ID = "9bc7315d-5449-457f-9b11-dc5083a92df5"
BOB_ID = "dc7b8b43-6a3b-47a2-841f-9efb8c4c595f"
AUTH = 'MediaBrowser Client="Infuse-Direct", Device="{device}", DeviceId="{device}", Version="8.1.2"'
SEASON = Path("TV") / "Vault Show (2020)" / "Season 01"
# Browser-like: mp4 h264/aac only, so an mkv or ac3 file must transcode.
BROWSER_PROFILE = {
    "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4,m4v", "VideoCodec": "h264", "AudioCodec": "aac"}],
    "TranscodingProfiles": [{"Type": "Video", "Container": "mp4", "Protocol": "hls", "VideoCodec": "h264", "AudioCodec": "aac"}],
}
# Infuse-like: plays nearly everything; the RefFrames condition is unknown to decide() and must be ignored.
INFUSE_PROFILE = {
    "DirectPlayProfiles": [{"Type": "Video", "Container": "mkv,mp4,mov", "VideoCodec": "h264,hevc", "AudioCodec": "aac,ac3,eac3,truehd"}],
    "CodecProfiles": [{"Type": "Video", "Conditions": [{"Condition": "LessThanEqual", "Property": "RefFrames", "Value": "16"}]}],
}
TRANSCODE_REASONS = {
    "ContainerNotSupported", "VideoCodecNotSupported", "AudioCodecNotSupported", "SubtitleCodecNotSupported", "AudioIsExternal",
    "SecondaryAudioNotSupported", "VideoProfileNotSupported", "VideoLevelNotSupported", "VideoResolutionNotSupported",
    "VideoBitDepthNotSupported", "VideoFramerateNotSupported", "RefFramesNotSupported", "AnamorphicVideoNotSupported",
    "InterlacedVideoNotSupported", "AudioChannelsNotSupported", "AudioProfileNotSupported", "AudioSampleRateNotSupported",
    "AudioBitDepthNotSupported", "ContainerBitrateExceedsLimit", "VideoBitrateNotSupported", "AudioBitrateNotSupported",
    "UnknownVideoStreamInfo", "UnknownAudioStreamInfo", "DirectPlayError", "VideoRangeTypeNotSupported",
    "VideoCodecTagNotSupported", "StreamCountExceedsLimit", "VideoRotationNotSupported",
}


@dataclass
class Vault:
    admin: TestClient  # Lumina API as the admin (HTTP basic), lifespan running
    jf: TestClient  # the Jellyfin surface: no basic auth, headers per request
    media: Path


@dataclass
class Login:
    token: str
    user_id: str  # Jellyfin hex
    headers: dict[str, str]


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    parent = tmp_path / "mnt"
    (parent / "media").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    db_module.init_db()
    with db_module.SessionLocal() as db:
        for user_id, username, role in ((ADMIN_ID, "admin", "admin"), (BOB_ID, "bob", "viewer")):
            db.add(User(id=user_id, username=username, display_name=username.title(), password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
    rate_limiter.clear()
    with TestClient(app, base_url="http://localhost") as admin:
        admin.auth = ("admin", PASSWORD)
        settings_response = admin.put("/api/admin/media-server", json={"jellyfin_enabled": True, "max_playback_sessions": 2})
        assert settings_response.status_code == 200, settings_response.text
        yield Vault(admin, TestClient(app, base_url="http://localhost"), parent / "media")
    lps.sessions.close_all()
    rate_limiter.clear()


def login(vault: Vault, username: str = "admin", device: str = "tv-1") -> Login:
    response = vault.jf.post("/Users/AuthenticateByName", json={"Username": username, "Pw": PASSWORD}, headers={"Authorization": AUTH.format(device=device)})
    assert response.status_code == 200, response.text
    body = response.json()
    return Login(body["AccessToken"], body["User"]["Id"], {"Authorization": f'{AUTH.format(device=device)}, Token="{body["AccessToken"]}"'})


def fake_show(media: Path, count: int = 3) -> None:
    for n in range(1, count + 1):
        write(media / SEASON / f"Vault Show - S01E0{n}.mkv")


def title_ids(vault: Vault, title_type: str) -> dict[str, str]:
    """name -> dashed title id through Lumina titles API."""
    page = vault.admin.get("/api/titles", params={"type": title_type, "limit": 200}).json()
    return {title["name"]: title["id"] for title in page["items"]}


def episodes(vault: Vault) -> list[dict]:
    """Season 1 of "Vault Show" as TitleSummary dicts (id = episode title, play_item_id = version item)."""
    series = title_ids(vault, "series")["Vault Show"]
    return vault.admin.get(f"/api/titles/{series}/episodes", params={"season": 1}).json()


def test_playback_request_reads_body_then_query_and_drops_garbage() -> None:
    parsed = playback_request(
        {"DeviceProfile": "not-a-dict", "MaxStreamingBitrate": "8000000", "SubtitleStreamIndex": -1, "AudioStreamIndex": True},
        {"starttimeticks": "10", "mediasourceid": "../x"},
    )
    assert parsed == PlaybackRequest(profile=None, max_bitrate=8_000_000, start_ticks=10, audio_index=None, subtitle_index=None, media_source_id=None)
    assert playback_request(None, {}) == PlaybackRequest(None, None, None, None, None, None)
    dashed = "0f8fad5b-d9cb-469f-a165-70867728950e"
    assert playback_request({"MediaSourceId": dashed.replace("-", "").upper()}, {}).media_source_id == dashed


def test_transcoding_fields_direct_and_hls() -> None:
    item, psid = "ab" * 16, "c" * 32
    direct = transcoding_fields(SimpleNamespace(mode="direct", height=None, reasons=()), item_id=item, play_session_id=psid, api_key="tok", audio_index=None, subtitle_index=None)
    assert direct == {"SupportsDirectPlay": True, "SupportsDirectStream": True, "SupportsTranscoding": False}
    hls = transcoding_fields(
        SimpleNamespace(mode="transcode", height=720, reasons=("AudioCodecNotSupported", "ContainerNotSupported")),
        item_id=item, play_session_id=psid, api_key="t/k", audio_index=1, subtitle_index=None,
    )
    assert hls == {
        "SupportsDirectPlay": False,
        "SupportsDirectStream": False,
        "SupportsTranscoding": True,
        "TranscodingSubProtocol": "hls",
        "TranscodingContainer": "mp4",
        "TranscodingUrl": f"/videos/{item}/master.m3u8?MediaSourceId={item}&PlaySessionId={psid}&AudioStreamIndex=1"
        "&MaxHeight=720&TranscodeReasons=AudioCodecNotSupported,ContainerNotSupported&ApiKey=t%2Fk",
    }
    # Jellyfin keeps StartTimeTicks out of HLS URLs: the timeline is the whole file and the client seeks.
    # The "unavailable" decision (no playable streams, DV profile 5 without OpenCL): no HLS offer, no non-Jellyfin reasons.
    unavailable = SimpleNamespace(mode="unavailable", height=None, reasons=("dolby_vision_p5",))
    assert transcoding_fields(unavailable, item_id=item, play_session_id=psid, api_key="tok", audio_index=None, subtitle_index=None) == {"SupportsTranscoding": False}


@needs_ffmpeg
def test_playback_info_runs_the_decision_per_media_source(vault: Vault) -> None:
    make_media(vault.media / SEASON / "Vault Show - S01E01.mkv", acodec="ac3")
    scan(vault.admin, vault.media)
    tv = login(vault)
    url = f"/Items/{jellyfin_id(episodes(vault)[0]['id'])}/PlaybackInfo"

    infuse = vault.jf.post(url, headers=tv.headers, json={"DeviceProfile": INFUSE_PROFILE}).json()
    assert infuse["MediaSources"][0]["SupportsDirectPlay"] is True
    assert "TranscodingUrl" not in infuse["MediaSources"][0]  # unknown RefFrames condition ignored (the 10.11 regression row)

    browser = vault.jf.post(url, headers=tv.headers, json={"DeviceProfile": BROWSER_PROFILE, "StartTimeTicks": 0}).json()
    source = browser["MediaSources"][0]
    assert source["SupportsDirectPlay"] is False and source["SupportsTranscoding"] is True
    assert source["TranscodingUrl"].startswith(f"/videos/{source['Id']}/master.m3u8?")
    assert f"PlaySessionId={browser['PlaySessionId']}" in source["TranscodingUrl"]
    assert f"ApiKey={tv.token}" in source["TranscodingUrl"]
    reasons = source["TranscodingUrl"].split("TranscodeReasons=")[1].split("&")[0].split(",")
    assert reasons and set(reasons) <= TRANSCODE_REASONS
    assert "TranscodeReasons" not in source  # [JsonIgnore] in Jellyfin; URL only

    for profile in ({"DirectPlayProfiles": "x", "CodecProfiles": [None, 3]}, [], "junk"):
        assert vault.jf.post(url, headers=tv.headers, json={"DeviceProfile": profile}).status_code == 200  # never 500
    assert vault.jf.post(url, headers=tv.headers, content=b"not json").status_code == 200
    assert vault.jf.get(url, headers=tv.headers).json()["MediaSources"][0]["SupportsDirectPlay"] is True  # GET: no profile, direct defaults


FAKE_FFMPEG = """#!/bin/sh
for last; do :; done
dir=$(dirname "$last")
printf 'init' > "$dir/init.mp4"
printf 'seg0' > "$dir/seg0.m4s"
printf '#EXTM3U\\n#EXT-X-MAP:URI="init.mp4"\\n#EXTINF:4.0,\\nseg0.m4s\\n#EXT-X-ENDLIST\\n' > "$last"
exec sleep 60
"""


@needs_ffmpeg
def test_transcode_sessions_are_per_device_and_stop_by_play_session(vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake = tmp_path / "ffmpeg"
    fake.write_text(FAKE_FFMPEG)
    fake.chmod(0o755)
    monkeypatch.setattr(local_playback, "media_tool", lambda db, name: str(fake) if name == "ffmpeg" else None)  # open_session's ffmpeg lookup (D4)
    make_media(vault.media / SEASON / "Vault Show - S01E01.mkv", acodec="ac3")
    scan(vault.admin, vault.media)
    tv, phone = login(vault, device="tv-1"), login(vault, device="phone-1")
    episode = episodes(vault)[0]
    info_url = f"/Items/{jellyfin_id(episode['id'])}/PlaybackInfo"
    tv_app = next(a["id"] for a in vault.admin.get("/api/connected-apps").json() if a["device_name"] == "tv-1")

    def transcode(who: Login) -> tuple[str, str, str]:
        info = vault.jf.post(info_url, headers=who.headers, json={"DeviceProfile": BROWSER_PROFILE}).json()
        source = info["MediaSources"][0]
        master = vault.jf.get(source["TranscodingUrl"])  # ApiKey only, no header
        assert master.status_code == 200, master.text
        assert master.headers["content-type"].startswith("application/vnd.apple.mpegurl")
        return info["PlaySessionId"], source["Id"], master.text

    psid, source_id, master = transcode(tv)
    other = {"MediaSourceId": source_id, "PlaySessionId": psid}
    assert vault.jf.get(f"/videos/{source_id}/master.m3u8", params={**other, "ApiKey": phone.token}).status_code == 404  # pending is per device
    assert f"hls/{psid}/index.m3u8?ApiKey={tv.token}" in master.splitlines()  # the one-rendition master playlist
    base = f"/videos/{source_id}/hls/{psid}"
    variant = vault.jf.get(f"{base}/index.m3u8", params={"ApiKey": tv.token})
    assert variant.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert f'URI="init.mp4?ApiKey={tv.token}"' in variant.text  # with_api_key: relative URIs carry the token
    assert f"seg0.m4s?ApiKey={tv.token}" in variant.text.splitlines()
    assert vault.jf.get(f"{base}/seg0.m4s", params={"ApiKey": tv.token}).content == b"seg0"
    assert vault.jf.get(f"{base}/seg0.m4s").status_code == 401
    assert vault.jf.get(f"{base}/seg0.m4s", params={"ApiKey": phone.token}).status_code == 404  # another device's session
    assert vault.jf.get(f"{base}/..%2Findex.m3u8", params={"ApiKey": tv.token}).status_code == 404
    assert vault.jf.get(f"{base}/index.m3u8", params={"ApiKey": phone.token}).status_code == 404  # another device's playlist
    assert vault.jf.get(f"/videos/{source_id}/hls/not-a-session/seg0.m4s", params={"ApiKey": tv.token}).status_code == 404
    assert vault.jf.get(f"/videos/{'f' * 32}/hls/{psid}/seg0.m4s", params={"ApiKey": tv.token}).status_code == 404  # Path item must match
    bob = login(vault, "bob", "bob-tv")
    assert vault.jf.get(f"{base}/seg0.m4s", params={"ApiKey": bob.token}).status_code == 404

    # The web player and the TV run side by side: the session rule is per (member, device).
    web = vault.admin.post(f"/api/library/{episode['play_item_id']}/playback-sessions")
    assert web.status_code == 201, web.text
    assert sorted(s.device for s in lps.sessions._sessions.values()) == sorted(["web", tv_app])  # D keys sessions per (member, device)

    stopped = vault.jf.post("/Sessions/Playing/Stopped", headers=tv.headers, json={"ItemId": jellyfin_id(episode["id"]), "MediaSourceId": source_id, "PlaySessionId": psid, "PositionTicks": 10_000_000})
    assert stopped.status_code == 204
    assert [s.device for s in lps.sessions._sessions.values()] == ["web"]

    psid2, _, _ = transcode(tv)
    assert vault.jf.delete("/Videos/ActiveEncodings", headers=tv.headers, params={"DeviceId": "tv-1", "PlaySessionId": psid2}).status_code == 204
    assert all(s.play_session_id != psid2 for s in lps.sessions._sessions.values())

    stale = {"MediaSourceId": source_id, "PlaySessionId": "0" * 32, "ApiKey": tv.token}  # e.g. after a restart
    assert vault.jf.get(f"/videos/{source_id}/master.m3u8", params=stale).status_code == 404

    psid3, _, _ = transcode(tv)
    browser = TestClient(app, base_url="http://localhost", headers={"Origin": ORIGIN})  # revoking is browser-only (2.9.0)
    csrf = browser.post("/api/session/login", json={"username": "admin", "password": PASSWORD}).json()["csrf_token"]
    assert browser.delete(f"/api/connected-apps/{tv_app}", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert all(s.play_session_id != psid3 for s in lps.sessions._sessions.values())  # revoking the device kills its sessions
    assert vault.jf.get(f"/videos/{source_id}/hls/{psid3}/seg0.m4s", params={"ApiKey": tv.token}).status_code == 401
    assert [s.device for s in lps.sessions._sessions.values()] == ["web"]
    with db_module.SessionLocal() as db:  # a password change or deactivation signs the member out: every stream ends
        UserService(db).revoke_sessions(ADMIN_ID)
        db.commit()
    assert not lps.sessions._sessions
    assert tv.token not in caplog.text and phone.token not in caplog.text and bob.token not in caplog.text


# An h264 height cap of 480 that direct/HLS-plays hevc: the cap limits Lumina's H.264 encode only.
CAPPED_PROFILE = {
    "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4", "VideoCodec": "h264,hevc", "AudioCodec": "aac"}],
    "TranscodingProfiles": [{"Type": "Video", "Container": "mp4", "Protocol": "hls", "VideoCodec": "h264,hevc", "AudioCodec": "aac"}],
    "CodecProfiles": [{"Type": "Video", "Codec": "h264", "Conditions": [{"Condition": "LessThanEqual", "Property": "Height", "Value": "480"}]}],
}
ARGS_FFMPEG = FAKE_FFMPEG.replace("exec sleep 60", 'printf \'%s\\n\' "$*" > "$dir/args"\nexec sleep 60')


def master_session(vault: Vault, who: Login, item_id: str) -> tuple[dict, lps.PlaybackSession]:
    """PlaybackInfo with CAPPED_PROFILE, then its master.m3u8: (the MediaSource, the session it started)."""
    info = vault.jf.post(f"/Items/{jellyfin_id(item_id)}/PlaybackInfo", headers=who.headers, json={"DeviceProfile": CAPPED_PROFILE}).json()
    source = info["MediaSources"][0]
    assert vault.jf.get(source["TranscodingUrl"]).status_code == 200
    return source, next(s for s in lps.sessions._sessions.values() if s.play_session_id == info["PlaySessionId"])


@needs_ffmpeg
def test_master_encodes_at_the_profile_height_and_honours_the_loudness_pref(vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Final review #4/#5: the h264 cap reaches the encode; a member who turned loudness off gets no server-side gain."""
    fake = tmp_path / "ffmpeg"
    fake.write_text(ARGS_FFMPEG)
    fake.chmod(0o755)
    monkeypatch.setattr(local_playback, "media_tool", lambda db, name: str(fake) if name == "ffmpeg" else None)
    monkeypatch.setattr(jellyfin_integration, "loudness_gain_db", lambda loudness: 6.0)  # as if loudness were measured
    monkeypatch.setattr(hwaccel, "status", lambda ffmpeg, mode: HwStatus())  # software encode; never probe the fake
    make_media(vault.media / SEASON / "Vault Show - S01E01.mkv", acodec="ac3", size="1280x720", seconds=1)
    scan(vault.admin, vault.media)
    tv = login(vault)
    episode = episodes(vault)[0]["id"]

    source, session = master_session(vault, tv, episode)
    assert "MaxHeight=480" in source["TranscodingUrl"]
    assert (session.key[3], session.key[4]) == (480, "encode")  # (height, video) from the master's own decide()
    assert "volume=6.0dB" in (session.directory / "args").read_text()  # default: loudness evening is on

    assert vault.admin.put("/api/settings/me", json={"ui_prefs": {"normalize_loudness": False}}).status_code == 200
    lps.sessions.close_all()
    _, session = master_session(vault, tv, episode)
    assert "volume=" not in (session.directory / "args").read_text()


@needs_ffmpeg
def test_master_copies_what_playback_info_promised(vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Final review #2: an h264 height cap never turns PlaybackInfo's hevc remux into a master.m3u8 encode."""
    if "libx265" not in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout:
        pytest.skip("ffmpeg lacks libx265")
    fake = tmp_path / "ffmpeg"
    fake.write_text(FAKE_FFMPEG)
    fake.chmod(0o755)
    monkeypatch.setattr(local_playback, "media_tool", lambda db, name: str(fake) if name == "ffmpeg" else None)
    make_media(vault.media / SEASON / "Vault Show - S01E01.mkv", vcodec="libx265", size="1280x720", seconds=1)
    scan(vault.admin, vault.media)
    source, session = master_session(vault, login(vault), episodes(vault)[0]["id"])
    assert "TranscodeReasons=ContainerNotSupported&" in source["TranscodingUrl"]  # PlaybackInfo: remux
    assert session.key[4] == "copy"


@pytest.mark.parametrize(("fmt", "expected"), [("srt", "srt"), ("SRT", "srt"), ("subrip", "srt"), ("vtt", "vtt"), ("webvtt", "vtt"), ("exe", None), ("", None), ("../srt", None)])
def test_subtitle_format(fmt: str, expected: str | None) -> None:
    assert subtitle_format(fmt) == expected


@needs_ffmpeg
def test_transcript_tracks_are_external_streams_rendered_from_the_db(vault: Vault) -> None:
    make_media(vault.media / SEASON / "Vault Show - S01E01.mp4")
    write(vault.media / SEASON / "Vault Show - S01E01.en.srt", "1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    scan(vault.admin, vault.media)
    episode = episodes(vault)[0]
    with db_module.SessionLocal() as db:
        TranscriptService(db).store(episode["play_item_id"], language="english", source_kind="asr", cues=[(0, 900, "Fish & <chips>"), (1000, 2000, "Bye")])
    tv = login(vault)
    source = vault.jf.get(f"/Items/{jellyfin_id(episode['id'])}/PlaybackInfo", headers=tv.headers).json()["MediaSources"][0]
    streams = source["MediaStreams"]
    assert [s["Index"] for s in streams] == [0, 1, 2, 3]  # video, audio (probe order), sidecar, transcript: append-only
    assert streams[2]["IsExternal"] and streams[2]["Language"] == "eng"
    generated = streams[3]
    assert (generated["DisplayTitle"], generated["Codec"], generated["DeliveryMethod"]) == ("English (generated)", "subrip", "External")

    subtitles = f"/Videos/{source['Id']}/{source['Id']}/Subtitles"
    srt = vault.jf.get(f"{subtitles}/3/Stream.srt", headers=tv.headers)
    assert srt.status_code == 200 and "Fish & <chips>" in srt.text and "00:00:00,900" in srt.text
    vtt = vault.jf.get(f"{subtitles}/3/0/Stream.vtt", headers=tv.headers)
    assert vtt.text.startswith("WEBVTT") and "Fish &amp; &lt;chips&gt;" in vtt.text
    assert "Hello" in vault.jf.get(f"{subtitles}/2/Stream.srt", headers=tv.headers).text  # the sidecar still serves as-is
    for bad in ("4/Stream.srt", "-1/Stream.srt", "99999999999/Stream.srt", "3/Stream.exe", "x/Stream.srt", "3/Stream..%2F..%2Fetc"):
        assert vault.jf.get(f"{subtitles}/{bad}", headers=tv.headers).status_code in (404, 422), bad
    bob = login(vault, "bob", "bob-tv")
    assert vault.jf.get(f"{subtitles}/3/Stream.srt", headers=bob.headers).status_code == 404  # private item


def test_media_segments_route(vault: Vault) -> None:
    fake_show(vault.media, 1)
    scan(vault.admin, vault.media)
    episode = episodes(vault)[0]
    saved = vault.admin.put(f"/api/library/{episode['play_item_id']}/segments", json={"segments": [
        {"type": "intro", "start_seconds": 5, "end_seconds": 35},
        {"type": "credits", "start_seconds": 1200, "end_seconds": 1300},
    ]})
    assert saved.status_code == 200, saved.text
    tv = login(vault)
    url = f"/MediaSegments/{jellyfin_id(episode['id'])}"
    body = vault.jf.get(url, headers=tv.headers).json()
    assert [(s["Type"], s["StartTicks"], s["EndTicks"]) for s in body["Items"]] == [("Intro", 50_000_000, 350_000_000), ("Outro", 12_000_000_000, 13_000_000_000)]
    assert {s["ItemId"] for s in body["Items"]} == {jellyfin_id(episode["id"])}
    assert body["Items"][0]["Id"] == jellyfin_id(synthetic_id(f"segment:{episode['play_item_id']}:intro:5000"))
    assert [s["Type"] for s in vault.jf.get(url, headers=tv.headers, params={"includeSegmentTypes": "Outro"}).json()["Items"]] == ["Outro"]
    assert [s["Type"] for s in vault.jf.get(url, headers=tv.headers, params=[("includeSegmentTypes", "Intro"), ("includeSegmentTypes", "Bogus")]).json()["Items"]] == ["Intro"]
    by_version = vault.jf.get(f"/MediaSegments/{jellyfin_id(episode['play_item_id'])}", headers=tv.headers).json()
    assert len(by_version["Items"]) == 2
    bob = login(vault, "bob", "bob-tv")
    assert vault.jf.get(url, headers=bob.headers).status_code == 404
    assert vault.jf.get("/MediaSegments/not-an-id", headers=tv.headers).status_code == 404
    assert vault.jf.get(url).status_code == 401


from app.services.jellyfin_discovery import item_types, search_hint  # noqa: E402


@pytest.mark.parametrize(("raw", "expected"), [(None, None), ("", None), ("Movie", {"movie"}), ("Movie,Series", {"movie", "series"}), ("episode, BoxSet", {"episode", "boxset"}), ("Audio", set())])
def test_item_types(raw: str | None, expected: set[str] | None) -> None:
    assert item_types(raw) == expected


def test_search_hint_from_dto() -> None:
    dto = {
        "Id": "ab" * 16, "Name": "Pilot", "Type": "Episode", "IsFolder": False, "MediaType": "Video", "IndexNumber": 1,
        "ParentIndexNumber": 1, "ProductionYear": 2020, "SeriesName": "Vault Show", "RunTimeTicks": 10, "Overview": "x",
        "ImageTags": {"Primary": "p", "Thumb": "t"}, "BackdropImageTags": ["b"], "UserData": {"Played": False},
    }
    assert search_hint(dto, "pil") == {
        "ItemId": "ab" * 16, "Id": "ab" * 16, "Name": "Pilot", "MatchedTerm": "pil", "Type": "Episode", "IsFolder": False,
        "MediaType": "Video", "IndexNumber": 1, "ParentIndexNumber": 1, "ProductionYear": 2020, "RunTimeTicks": 10,
        "Series": "Vault Show", "PrimaryImageTag": "p", "ThumbImageTag": "t", "ThumbImageItemId": "ab" * 16,
        "BackdropImageTag": "b", "BackdropImageItemId": "ab" * 16,
    }


def movie_tree(media: Path) -> None:
    for name, year, genres in (("Arrival", 2016, ("Science Fiction", "Drama")), ("Contact", 1997, ("Science Fiction", "Drama")), ("Paddington", 2014, ("Comedy",))):
        folder = media / "Movies" / f"{name} ({year})"
        write(folder / f"{name} ({year}).mkv")
        write(folder / "movie.nfo", f"<movie><title>{name}</title><year>{year}</year>" + "".join(f"<genre>{g}</genre>" for g in genres) + "</movie>")


def names(response) -> list[str]:  # noqa: ANN001
    assert response.status_code == 200, response.text
    return [row["Name"] for row in response.json()["Items"]]


def test_search_similar_and_suggestions_are_visibility_scoped(vault: Vault) -> None:
    movie_tree(vault.media)
    fake_show(vault.media, 1)
    scan(vault.admin, vault.media)
    movies = title_ids(vault, "movie")
    episode = episodes(vault)[0]
    with db_module.SessionLocal() as db:
        TranscriptService(db).store(episode["play_item_id"], language="en", source_kind="asr", cues=[(0, 4000, "the one where the whale sings")])
    tv, bob = login(vault), login(vault, "bob", "bob-tv")
    hints = "/Search/Hints"

    arrival = vault.jf.get(hints, headers=tv.headers, params={"searchTerm": "arrival"}).json()
    assert arrival["SearchHints"][0]["ItemId"] == jellyfin_id(movies["Arrival"]) and arrival["SearchHints"][0]["Type"] == "Movie"
    moment = vault.jf.get(hints, headers=tv.headers, params={"searchTerm": "whale"}).json()["SearchHints"]
    assert moment[0]["Id"] == jellyfin_id(episode["id"]) and moment[0]["Type"] == "Episode" and moment[0]["Series"] == "Vault Show"
    assert vault.jf.get(hints, headers=tv.headers, params={"searchTerm": "whale", "includeItemTypes": "Movie"}).json()["SearchHints"] == []
    assert vault.jf.get(hints, headers=tv.headers, params={"searchTerm": "x" * 201}).status_code == 422
    assert vault.jf.get(hints, headers=tv.headers).status_code == 400  # the blank-searchTerm 400 stands
    assert vault.jf.get(hints, headers=bob.headers, params={"searchTerm": "arrival"}).json() == {"SearchHints": [], "TotalRecordCount": 0}

    science = {"searchTerm": "science", "Recursive": "true", "IncludeItemTypes": "Movie"}
    assert set(names(vault.jf.get("/Items", headers=tv.headers, params=science))) == {"Arrival", "Contact"}
    assert names(vault.jf.get("/Items", headers=tv.headers, params={**science, "SortBy": "SortName"})) == ["Arrival", "Contact"]
    assert names(vault.jf.get("/Items", headers=bob.headers, params=science)) == []

    for prefix in ("Items", "Movies"):
        similar = names(vault.jf.get(f"/{prefix}/{jellyfin_id(movies['Arrival'])}/Similar", headers=tv.headers))
        assert similar[0] == "Contact" and "Arrival" not in similar
    series = title_ids(vault, "series")["Vault Show"]
    assert names(vault.jf.get(f"/Shows/{jellyfin_id(series)}/Similar", headers=tv.headers)) == []
    assert vault.jf.get(f"/Items/{jellyfin_id(movies['Arrival'])}/Similar", headers=bob.headers).status_code == 404
    assert vault.jf.get("/Items/not-an-id/Similar", headers=tv.headers).status_code == 404

    # A cold member (no favorite/played signal yet) gets [], never an unsolicited pool (ADR 0011).
    assert names(vault.jf.get("/Items/Suggestions", headers=tv.headers, params={"type": "Movie"})) == []
    assert vault.jf.post(f"/UserPlayedItems/{jellyfin_id(movies['Arrival'])}", headers=tv.headers).status_code == 200
    assert "Arrival" not in names(vault.jf.get(f"/Users/{tv.user_id}/Suggestions", headers=tv.headers, params={"type": "Movie"}))
    assert names(vault.jf.get("/Items/Suggestions", headers=bob.headers, params={"type": "Movie"})) == []
    assert vault.jf.get(f"/Users/{bob.user_id}/Suggestions", headers=tv.headers).status_code == 404  # uid must be the caller


from app.media_schemas import TitlePerson
from app.models import MediaTitle, Person
from app.services.jellyfin import person_dtos, title_metadata_fields

AMY = synthetic_id("tmdb-person:9273")


def fake_tag(target: str, image_type: str, source: str) -> str:
    return f"{target[:4]}-{image_type}-{source}"


def test_title_metadata_fields_maps_and_drops_malformed_values() -> None:
    title = SimpleNamespace(field_sources={}, locked=False, metadata_json={
        "tagline": "Why are they here?", "studios": ["21 Laps", 7, ""], "status": "Released", "end_date": "2017-01-01",
        "runtime_minutes": 116, "airsbefore_season": 2, "airsbefore_episode": "x", "airsafter_season": True,
    })
    assert title_metadata_fields(title) == {
        "Taglines": ["Why are they here?"],
        "Studios": [{"Name": "21 Laps", "Id": jellyfin_id(synthetic_id("studio:21 Laps"))}],
        "Status": "Released",
        "EndDate": "2017-01-01T00:00:00.0000000Z",
        "AirsBeforeSeasonNumber": 2,
        "RunTimeTicks": 116 * 60 * 10_000_000,
        "LockData": False, "LockedFields": [],
    }
    unlocked = {"LockData": False, "LockedFields": []}  # always present since 2.1.0
    assert title_metadata_fields(SimpleNamespace(metadata_json={"studios": None, "end_date": 5})) == unlocked
    assert title_metadata_fields(SimpleNamespace(metadata_json="junk")) == unlocked


def test_person_dtos() -> None:
    people = [
        TitlePerson(id=AMY, name="Amy Adams", role="Louise Banks", type="Actor", image_url=f"/api/people/{AMY}/image"),
        TitlePerson(id=None, name="Local Only", role=None, type="Director"),
    ]
    assert person_dtos(people, fake_tag) == [
        {"Name": "Amy Adams", "Id": jellyfin_id(AMY), "Role": "Louise Banks", "Type": "Actor", "PrimaryImageTag": fake_tag(AMY, "Primary", f"/api/people/{AMY}/image")},
        {"Name": "Local Only", "Id": jellyfin_id(synthetic_id("person-name:local only")), "Type": "Director"},
    ]


def test_people_and_studios_reach_base_item_dto(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import artwork  # the ArtworkService register(app, artwork) receives

    movie_tree(vault.media)
    scan(vault.admin, vault.media)
    arrival = title_ids(vault, "movie")["Arrival"]
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, arrival)
        title.metadata_json = {**title.metadata_json, "tagline": "Why are they here?", "studios": ["21 Laps"], "runtime_minutes": 116,
                               "people": [{"person_id": AMY, "name": "Amy Adams", "role": "Louise Banks", "type": "Actor"}]}
        db.add(Person(id=AMY, tmdb_id=9273, name="Amy Adams", profile_path="/amy.jpg"))
        db.commit()
    fetched: list[str] = []

    def fake_load_pinned(url: str, **_kwargs):  # noqa: ANN202
        fetched.append(url)
        return ArtworkResponse("image/jpeg", b"\xff\xd8face")

    monkeypatch.setattr(artwork, "load_pinned", fake_load_pinned)
    tv, bob = login(vault), login(vault, "bob", "bob-tv")
    single = vault.jf.get(f"/Items/{jellyfin_id(arrival)}", headers=tv.headers).json()
    assert single["People"][0]["Name"] == "Amy Adams" and single["People"][0]["Id"] == jellyfin_id(AMY) and single["People"][0]["PrimaryImageTag"]
    assert single["Studios"] == [{"Name": "21 Laps", "Id": jellyfin_id(synthetic_id("studio:21 Laps"))}]
    assert single["Taglines"] == ["Why are they here?"]
    assert single["RunTimeTicks"] == 116 * 60 * 10_000_000  # fake file: no probe duration, TMDB runtime fills in
    listing = {"Ids": jellyfin_id(arrival)}
    assert "People" not in vault.jf.get("/Items", headers=tv.headers, params=listing).json()["Items"][0]
    assert vault.jf.get("/Items", headers=tv.headers, params={**listing, "Fields": "People"}).json()["Items"][0]["People"][0]["Name"] == "Amy Adams"
    image = vault.jf.get(f"/Items/{jellyfin_id(AMY)}/Images/Primary", headers=tv.headers)
    assert image.status_code == 200 and image.content == b"\xff\xd8face"
    assert fetched and fetched[0].endswith("/w185/amy.jpg")
    assert vault.jf.get(f"/Items/{jellyfin_id(AMY)}/Images/Primary", headers=bob.headers).status_code == 404  # credited only on admin's private film
    assert vault.jf.get(f"/Items/{jellyfin_id(AMY)}/Images/Primary").status_code in (401, 404)  # no token, no tag
    # Infuse fetches art without a token: the signed tag from People[].PrimaryImageTag must be enough.
    signed = single["People"][0]["PrimaryImageTag"]
    tokenless = vault.jf.get(f"/Items/{jellyfin_id(AMY)}/Images/Primary", params={"tag": signed})
    assert tokenless.status_code == 200 and tokenless.content == b"\xff\xd8face"
    assert vault.jf.get(f"/Items/{jellyfin_id(AMY)}/Images/Primary", params={"tag": "0" * 32}).status_code == 404


def test_title_detail_people_get_image_urls_from_title_metadata(vault: Vault) -> None:
    """TitleService.detail hands off to title_metadata.title_people, so cast get image_url like /Items does."""
    movie_tree(vault.media)
    scan(vault.admin, vault.media)
    arrival = title_ids(vault, "movie")["Arrival"]
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, arrival)
        title.metadata_json = {**title.metadata_json, "people": [{"person_id": AMY, "name": "Amy Adams", "role": "Louise Banks", "type": "Actor"}]}
        db.add(Person(id=AMY, tmdb_id=9273, name="Amy Adams", profile_path="/amy.jpg"))
        db.commit()
    detail = vault.admin.get(f"/api/titles/{arrival}").json()
    assert detail["people"] == [{"id": AMY, "name": "Amy Adams", "role": "Louise Banks", "type": "Actor", "image_url": f"/api/people/{AMY}/image"}]


from datetime import UTC, datetime, timedelta  # noqa: E402

from app.services.jellyfin_discovery import dismisses_resume, next_up_filtered  # noqa: E402
from app.services.titles import EpisodeProgress  # noqa: E402

T = datetime(2026, 9, 1, 20, 0)


def series(completed: set[str]) -> list[EpisodeProgress]:
    # e1 was watched last (the anchor, at T); the others earlier or never.
    return [
        EpisodeProgress("e1", 1, 1, T, "e1" in completed),
        EpisodeProgress("e2", 1, 2, T - timedelta(days=30) if "e2" in completed else None, "e2" in completed),
        EpisodeProgress("e3", 1, 3, T - timedelta(days=29) if "e3" in completed else None, "e3" in completed),
    ]


@pytest.mark.parametrize(("body", "resumable", "expected"), [
    ({"PlaybackPositionTicks": 0}, True, True),
    ({"PlaybackPositionTicks": 0, "Played": False}, True, True),
    ({"PlaybackPositionTicks": 0, "Played": True}, True, False),  # mark played, not dismiss
    ({"PlaybackPositionTicks": 0}, False, False),  # nothing to remove
    ({"PlaybackPositionTicks": 10}, True, False),
    ({"PlaybackPositionTicks": False}, True, False),  # bool is not a tick count
    ({"PlaybackPositionTicks": "0"}, True, False),
    ({}, True, False),
])
def test_dismisses_resume(body: dict, resumable: bool, expected: bool) -> None:
    assert dismisses_resume(body, resumable=resumable) is expected


@pytest.mark.parametrize(("candidate", "completed", "cutoff", "rewatching", "expected"), [
    ("e2", {"e1"}, None, True, "e2"),
    ("e2", {"e1", "e2"}, None, True, "e2"),  # rewatch: the immediate successor even if seen
    ("e2", {"e1", "e2"}, None, False, "e3"),  # enableRewatching=false skips seen episodes
    ("e2", {"e1", "e2", "e3"}, None, False, None),
    ("e2", {"e1"}, T + timedelta(days=1), True, None),  # anchor older than nextUpDateCutoff
    ("e2", {"e1"}, T - timedelta(days=1), True, "e2"),
    (None, {"e1"}, None, True, None),
])
def test_next_up_filtered(candidate: str | None, completed: set[str], cutoff: datetime | None, rewatching: bool, expected: str | None) -> None:
    ordered = series(completed)
    chosen = next((e for e in ordered if e.title_id == candidate), None)
    result = next_up_filtered(ordered, chosen, cutoff=cutoff, rewatching=rewatching)
    assert (result.title_id if result else None) == expected


def test_dismiss_and_next_up_options_over_jellyfin(vault: Vault) -> None:
    fake_show(vault.media, 3)
    scan(vault.admin, vault.media)
    e1, e2, e3 = episodes(vault)
    tv = login(vault)
    hexes = {n: jellyfin_id(e["id"]) for n, e in (("e1", e1), ("e2", e2), ("e3", e3))}
    for n in ("e1", "e2", "e1"):  # watched E1, E2, then rewatched E1: anchor = E1
        assert vault.jf.post(f"/UserPlayedItems/{hexes[n]}", headers=tv.headers).status_code == 200
    nextup = "/Shows/NextUp"
    assert [i["Id"] for i in vault.jf.get(nextup, headers=tv.headers).json()["Items"]] == [hexes["e2"]]
    assert [i["Id"] for i in vault.jf.get(nextup, headers=tv.headers, params={"enableRewatching": "false"}).json()["Items"]] == [hexes["e3"]]
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert vault.jf.get(nextup, headers=tv.headers, params={"nextUpDateCutoff": future}).json()["Items"] == []
    assert vault.jf.get(nextup, headers=tv.headers, params={"nextUpDateCutoff": "not-a-date"}).status_code == 422

    progress = {"ItemId": hexes["e3"], "MediaSourceId": jellyfin_id(e3["play_item_id"]), "PositionTicks": 300_000_000}
    assert vault.jf.post("/Sessions/Playing/Progress", headers=tv.headers, json=progress).status_code == 204
    resume = "/UserItems/Resume"
    assert [i["Id"] for i in vault.jf.get(resume, headers=tv.headers).json()["Items"]] == [hexes["e3"]]

    userdata = f"/UserItems/{hexes['e3']}/UserData"
    assert vault.jf.post(userdata, headers=tv.headers, json={"PlaybackPositionTicks": 0}).status_code == 200
    assert vault.jf.get(resume, headers=tv.headers).json()["Items"] == []
    assert vault.admin.get("/api/playback/continue").json() == []  # dismissed on both surfaces
    assert vault.jf.get(userdata, headers=tv.headers).json()["PlaybackPositionTicks"] == 300_000_000  # never deleted

    assert vault.jf.post("/Sessions/Playing/Progress", headers=tv.headers, json={**progress, "PositionTicks": 310_000_000}).status_code == 204
    assert [i["Id"] for i in vault.jf.get(resume, headers=tv.headers).json()["Items"]] == [hexes["e3"]]  # playing again undoes it

    played = vault.jf.post(userdata, headers=tv.headers, json={"PlaybackPositionTicks": 0, "Played": True}).json()
    assert played["Played"] is True


import json  # noqa: E402
from collections.abc import Iterator  # noqa: E402

from starlette.routing import Match  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
OAS_PROPS = json.loads((FIXTURES / "jellyfin_oas_props.json").read_text())


def assert_jellyfin_keys(schema: str, obj: dict) -> None:
    extra = set(obj) - set(OAS_PROPS[schema])
    assert not extra, f"{schema} emits non-Jellyfin keys {sorted(extra)}"


def test_integration_payload_keys_are_jellyfin_properties(vault: Vault) -> None:
    movie_tree(vault.media)
    fake_show(vault.media, 1)
    scan(vault.admin, vault.media)
    arrival, episode = title_ids(vault, "movie")["Arrival"], episodes(vault)[0]
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, arrival)
        title.metadata_json = {**title.metadata_json, "studios": ["21 Laps"], "people": [{"person_id": None, "name": "Local Only", "role": "Self", "type": "Actor"}]}
        db.commit()
    vault.admin.put(f"/api/library/{episode['play_item_id']}/segments", json={"segments": [{"type": "intro", "start_seconds": 1, "end_seconds": 20}]})
    tv = login(vault)
    queue = jellyfin_id(synthetic_id(f"watchqueue:{ADMIN_ID}"))
    vault.jf.post(f"/Playlists/{queue}/Items", headers=tv.headers, params={"ids": jellyfin_id(arrival)})

    hints = vault.jf.get("/Search/Hints", headers=tv.headers, params={"searchTerm": "arrival"}).json()
    assert_jellyfin_keys("SearchHintResult", hints)
    for hint in hints["SearchHints"]:
        assert_jellyfin_keys("SearchHint", hint)
    assert_jellyfin_keys("PlaylistDto", vault.jf.get(f"/Playlists/{queue}", headers=tv.headers).json())
    for row in vault.jf.get(f"/Playlists/{queue}/Items", headers=tv.headers).json()["Items"]:
        assert_jellyfin_keys("BaseItemDto", row)
    segments = vault.jf.get(f"/MediaSegments/{jellyfin_id(episode['id'])}", headers=tv.headers).json()
    assert_jellyfin_keys("MediaSegmentDtoQueryResult", segments)
    for segment in segments["Items"]:
        assert_jellyfin_keys("MediaSegmentDto", segment)
    movie = vault.jf.get(f"/Items/{jellyfin_id(arrival)}", headers=tv.headers).json()
    assert_jellyfin_keys("BaseItemDto", movie)
    assert hints["SearchHints"] and segments["Items"] and movie["People"] and movie["Studios"]  # loops must check something
    for person in movie["People"]:
        assert_jellyfin_keys("BaseItemPerson", person)
    for studio in movie["Studios"]:
        assert_jellyfin_keys("NameGuidPair", studio)


@needs_ffmpeg
def test_scanned_vault_journey_across_jellyfin_and_lumina(vault: Vault) -> None:
    for n in (1, 2):
        make_media(vault.media / SEASON / f"Vault Show - S01E0{n}.mp4", seconds=4)
    make_media(vault.media / "Movies" / "Arrival (2016)" / "Arrival (2016).mkv", acodec="ac3")
    scan(vault.admin, vault.media)

    assert vault.jf.get("/System/Info/Public").json()["ProductName"] == "Jellyfin Server"
    tv = login(vault)
    assert vault.jf.get("/Users/Me", headers=tv.headers).json()["Id"] == tv.user_id
    assert {"Movies", "Shows"} <= {view["Name"] for view in vault.jf.get("/UserViews", headers=tv.headers).json()["Items"]}
    shows = vault.jf.get("/Items", headers=tv.headers, params={"IncludeItemTypes": "Series", "Recursive": "true", "Limit": 1, "StartIndex": 0}).json()
    series = shows["Items"][0]
    assert series["Name"] == "Vault Show" and shows["TotalRecordCount"] == 1
    season = vault.jf.get(f"/Shows/{series['Id']}/Seasons", headers=tv.headers).json()["Items"][0]
    eps = vault.jf.get(f"/Shows/{series['Id']}/Episodes", headers=tv.headers, params={"SeasonId": season["Id"], "Fields": "MediaSources"}).json()["Items"]
    assert [episode["IndexNumber"] for episode in eps] == [1, 2]
    e1, e2 = eps
    assert_jellyfin_keys("BaseItemDto", e1)

    info = vault.jf.post(f"/Items/{e1['Id']}/PlaybackInfo", headers=tv.headers, json={"DeviceProfile": INFUSE_PROFILE}).json()
    source = info["MediaSources"][0]
    assert source["SupportsDirectPlay"] is True and str(vault.media) not in source["Path"]
    assert_jellyfin_keys("MediaSourceInfo", source)
    stream = f"/Videos/{e1['Id']}/stream"
    params = {"MediaSourceId": source["Id"], "Static": "true", "ApiKey": tv.token}
    assert vault.jf.head(stream, params=params).status_code == 200
    ranged = vault.jf.get(stream, params=params, headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100

    report = {"ItemId": e1["Id"], "MediaSourceId": source["Id"], "PlaySessionId": info["PlaySessionId"]}
    assert vault.jf.post("/Sessions/Playing", headers=tv.headers, json={**report, "PositionTicks": 0}).status_code == 204
    assert vault.jf.post("/Sessions/Playing/Progress", headers=tv.headers, json={**report, "PositionTicks": 20_000_000}).status_code == 204
    resume = vault.jf.get("/UserItems/Resume", headers=tv.headers).json()["Items"]
    assert [row["Id"] for row in resume] == [e1["Id"]] and resume[0]["UserData"]["PlaybackPositionTicks"] == 20_000_000

    # Lumina's UI API shows the same resume point …
    shelf = vault.admin.get("/api/playback/continue").json()
    assert [(row["item_id"], row["position_seconds"]) for row in shelf] == [(parse_item_id(source["Id"]), 2)]
    assert shelf[0]["title"]["id"] == parse_item_id(e1["Id"])
    assert vault.admin.get(f"/api/titles/{parse_item_id(series['Id'])}").json()["play_next"]["id"] == parse_item_id(e1["Id"])
    # … and a web-player checkpoint shows up in Infuse.
    assert vault.admin.put(f"/api/library/{parse_item_id(source['Id'])}/playback", json={"position_seconds": 3}).status_code == 200
    assert vault.jf.get(f"/UserItems/{e1['Id']}/UserData", headers=tv.headers).json()["PlaybackPositionTicks"] == 30_000_000

    assert vault.jf.post("/Sessions/Playing/Stopped", headers=tv.headers, json={**report, "PositionTicks": source["RunTimeTicks"]}).status_code == 204
    assert vault.jf.get("/UserItems/Resume", headers=tv.headers).json()["Items"] == []
    assert [row["Id"] for row in vault.jf.get("/Shows/NextUp", headers=tv.headers).json()["Items"]] == [e2["Id"]]
    assert [title["id"] for title in vault.admin.get("/api/titles/next-up").json()] == [parse_item_id(e2["Id"])]
    assert vault.jf.post(f"/UserFavoriteItems/{e2['Id']}", headers=tv.headers).json()["IsFavorite"] is True
    assert vault.jf.get(f"/UserItems/{e1['Id']}/UserData", headers=tv.headers).json()["Played"] is True

    movie = next(row for row in vault.jf.get("/Items", headers=tv.headers, params={"IncludeItemTypes": "Movie", "Recursive": "true"}).json()["Items"] if row["Name"] == "Arrival")
    browser = vault.jf.post(f"/Items/{movie['Id']}/PlaybackInfo", headers=tv.headers, json={"DeviceProfile": BROWSER_PROFILE}).json()["MediaSources"][0]
    assert browser["SupportsTranscoding"] is True and "/master.m3u8?" in browser["TranscodingUrl"]
    assert_jellyfin_keys("MediaSourceInfo", browser)


def flat_routes(routes: list) -> Iterator:
    """FastAPI 0.139 wraps include_router() in an _IncludedRouter whose matches() is FULL for any path under it
    (it resolves the route later), so walk its effective routes instead, in registration order."""
    for route in routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        else:
            yield route


def _replay_hits(method: str, template: str) -> bool:
    path = "/".join("1" if part.startswith("{") else part for part in template.split("/"))
    path = path.replace("stream.{fmt}", "stream.srt")
    scope = {"type": "http", "path": path, "method": method}
    route = next((r for r in flat_routes(app.router.routes) if r.matches(scope)[0] == Match.FULL), None)
    return route is not None and not getattr(route, "path", "").endswith("{rest:path}")


def test_golden_traffic_replay_hits_real_routes() -> None:
    """Every captured Infuse request matches a real route, never the /jellyfin catch-all."""
    # Review 31: the matcher must still discriminate after a FastAPI bump (a router-level FULL match hits everything).
    assert not _replay_hits("GET", "/jellyfin/NoSuchRoute/{itemId}/Nope")
    golden = json.loads((FIXTURES / "jellyfin_golden_infuse.json").read_text())
    misses = [f"{request['method']} {request['template']}" for request in golden["requests"] if not _replay_hits(request["method"], request["template"])]
    assert not misses, f"Unhandled Infuse requests: {misses}"


def test_trace_logs_route_templates_and_parameter_names_only(vault: Vault, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(settings, "jellyfin_trace", True)
    caplog.set_level(logging.INFO, logger="app.jellyfin")  # scoped: unscoped INFO also lifts httpx's own request-URL logging
    tv = login(vault)
    vault.jf.get("/Items", headers=tv.headers, params={"searchTerm": "secret-title-words", "ApiKey": tv.token})
    vault.jf.get("/NoSuchRoute/abc", headers=tv.headers)
    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("jellyfin.")]
    assert "jellyfin.trace GET /jellyfin/items apikey,searchterm" in lines
    assert any(line.startswith("jellyfin.unhandled GET /jellyfin/nosuchroute/abc") for line in lines)
    assert tv.token not in caplog.text and "secret-title-words" not in caplog.text


def test_import_hooks_include_the_embedding_backfill(vault: Vault) -> None:
    from app.services import embeddings, library_import

    assert library_import.after_import_hooks.count(embeddings.start_backfill) == 1


def test_a_scanned_title_is_searchable_at_once(vault: Vault) -> None:
    movie_tree(vault.media)
    scan(vault.admin, vault.media)
    tv = login(vault)
    hints = vault.jf.get("/Search/Hints", headers=tv.headers, params={"searchTerm": "paddington"}).json()["SearchHints"]
    assert [hint["Name"] for hint in hints] == ["Paddington"]  # indexed by _link_titles, not by a boot-time rebuild


def test_add_server_sequence_never_404s(vault: Vault) -> None:
    """Infuse: sign in, then GroupingOptions (a 404 here made it report "failed to add"), then first-sync browsing."""
    who = login(vault)
    h = who.headers
    views = vault.jf.get("/UserViews", headers=h).json()["Items"]
    grouping = vault.jf.get("/UserViews/GroupingOptions", headers=h)
    assert grouping.status_code == 200
    assert grouping.json() == [{"Name": v["Name"], "Id": v["Id"]} for v in views] and views
    assert vault.jf.get(f"/UserViews/GroupingOptions?userId={who.user_id}", headers=h).status_code == 200
    assert vault.jf.get("/UserViews/GroupingOptions").status_code in (401, 403)  # auth required
    uid = who.user_id
    for path in (
        "/System/Info/Public", "/System/Info", "/Users/Me", f"/Users/{uid}", "/UserViews", f"/Users/{uid}/Views",
        "/DisplayPreferences/usersettings?client=emby", "/Items", f"/Users/{uid}/Items", "/Items/Latest", f"/Users/{uid}/Items/Resume",
        "/UserItems/Resume", "/Shows/NextUp", "/Items/Filters", "/Items/Filters2", "/Genres", "/MusicGenres", "/Studios",
        "/Persons", "/Years",
    ):
        response = vault.jf.get(path, headers=h)
        assert response.status_code == 200, (path, response.status_code)
    assert vault.jf.post("/Sessions/Capabilities/Full", headers=h, json={}).status_code == 204


def test_infuse_observed_add_sequence(vault: Vault) -> None:
    """The order a real Infuse (tvOS build on macOS) sent on 2026-10-01: VirtualFolders followed GroupingOptions and 404'd."""
    who = login(vault)
    h = who.headers
    views = vault.jf.get(f"/UserViews?userId={who.user_id}&includeExternalContent=false", headers=h).json()["Items"]
    assert vault.jf.get(f"/UserViews/GroupingOptions?userId={who.user_id}", headers=h).status_code == 200
    folders = vault.jf.get("/Library/VirtualFolders", headers=h)
    assert folders.status_code == 200
    assert [(f["Name"], f["ItemId"], f["CollectionType"]) for f in folders.json()] == [(v["Name"], v["Id"], v.get("CollectionType")) for v in views]
    assert all(f["Locations"] == [] for f in folders.json())  # never disclose server paths
    assert vault.jf.get("/Library/VirtualFolders").status_code in (401, 403)
    # Facet browsing and the matching /Items filters, as a client's library sync sends them.
    movies = next(v["Id"] for v in views if v["CollectionType"] == "movies")
    for path, params in (
        ("/Genres", {"userId": who.user_id, "parentId": movies, "includeItemTypes": "Movie", "recursive": "true", "sortBy": "SortName", "fields": "ItemCounts"}),
        ("/Studios", {"userId": who.user_id, "parentId": movies, "recursive": "true"}),
        ("/Persons", {"userId": who.user_id, "personTypes": "Actor,Director", "limit": "50"}),
        ("/Items/Filters", {"userId": who.user_id, "parentId": movies, "includeItemTypes": "Movie"}),
        ("/Items/Filters2", {"userId": who.user_id, "parentId": movies, "includeItemTypes": "Movie"}),
        ("/Items", {"userId": who.user_id, "parentId": movies, "genreIds": "0" * 32, "personIds": "0" * 32, "studioIds": "0" * 32}),
    ):
        assert vault.jf.get(path, headers=h, params=params).status_code == 200, path


def test_views_carry_child_counts(vault: Vault) -> None:
    """Infuse asks UserViews for ChildCount/RecursiveItemCount; a view with none can look empty."""
    who = login(vault)
    views = vault.jf.get(f"/UserViews?userId={who.user_id}&fields=RecursiveItemCount,ChildCount", headers=who.headers).json()["Items"]
    for view in views:
        if view["CollectionType"] in ("movies", "tvshows"):
            listed = vault.jf.get("/Items", headers=who.headers, params={"parentId": view["Id"], "limit": 0}).json()["TotalRecordCount"]
            assert view["ChildCount"] == listed and view["RecursiveItemCount"] == listed, view["Name"]


def test_items_carry_a_library_relative_path_for_infuse(vault: Vault) -> None:
    """Infuse's decoder needs Path on playable items ("DataSourceError" without it, 2026-10-01). Never a server mount path."""
    make_media(vault.media / SEASON / "Vault Show - S01E01.mkv")
    scan(vault.admin, vault.media)
    who = login(vault)
    items = vault.jf.get("/Items", headers=who.headers, params={"recursive": "true", "includeItemTypes": "Movie,Episode", "fields": "Path,MediaSources"}).json()["Items"]
    assert items
    for item in items:
        assert item["Path"].startswith("/") and item["Path"] == item["MediaSources"][0]["Path"], item["Name"]
        assert str(vault.media) not in item["Path"], item["Path"]


def test_facet_browsing_lists_and_filters_what_the_member_can_see(vault: Vault) -> None:
    movie_tree(vault.media)
    scan(vault.admin, vault.media)
    movies = title_ids(vault, "movie")
    with db_module.SessionLocal() as db:
        title = db.get(MediaTitle, movies["Arrival"])
        title.metadata_json = {**title.metadata_json, "studios": ["21 Laps"], "tags": ["cerebral"], "official_rating": "PG-13",
                               "people": [{"person_id": AMY, "name": "Amy Adams", "role": "Louise Banks", "type": "Actor"}],
                               "crew": [{"person_id": None, "name": "Local Only", "job": "Director"}]}
        db.commit()
    tv, bob = login(vault), login(vault, "bob", "bob-tv")

    def facet(path: str, who: Login, **params: str) -> dict:
        response = vault.jf.get(path, headers=who.headers, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    genres = facet("/Genres", tv)
    assert [g["Name"] for g in genres["Items"]] == ["Comedy", "Drama", "Science Fiction"] and genres["TotalRecordCount"] == 3
    assert genres["Items"][0]["Type"] == "Genre" and genres["Items"][0]["Id"] == jellyfin_id(synthetic_id("genre:Comedy"))
    assert [g["Name"] for g in facet("/Genres", tv, NameStartsWith="s")["Items"]] == ["Science Fiction"]
    assert [g["Name"] for g in facet("/Genres", tv, StartIndex="1", Limit="1")["Items"]] == ["Drama"]
    assert facet("/Genres", tv, IncludeItemTypes="Series")["Items"] == []
    studio = facet("/Studios", tv)["Items"]
    assert studio == [{"Name": "21 Laps", "ServerId": studio[0]["ServerId"], "Id": jellyfin_id(synthetic_id("studio:21 Laps")), "Type": "Studio", "ImageTags": {}, "BackdropImageTags": []}]
    people = {p["Name"]: p for p in facet("/Persons", tv)["Items"]}
    assert people["Amy Adams"]["Id"] == jellyfin_id(AMY) and people["Amy Adams"]["Type"] == "Person"
    assert people["Local Only"]["Id"] == jellyfin_id(synthetic_id("person-name:local only"))
    assert list(facet("/Persons", tv, PersonTypes="Director")["Items"][0].values())[0] == "Local Only"

    def listed(who: Login, **params: str) -> list[str]:
        return sorted(names(vault.jf.get("/Items", headers=who.headers, params={"Recursive": "true", "IncludeItemTypes": "Movie", **params})))

    comedy = jellyfin_id(synthetic_id("genre:Comedy"))
    assert listed(tv, GenreIds=comedy) == ["Paddington"]
    assert listed(tv, Genres="Comedy|Drama") == ["Arrival", "Contact", "Paddington"]
    assert listed(tv, StudioIds=jellyfin_id(synthetic_id("studio:21 Laps"))) == ["Arrival"]
    assert listed(tv, PersonIds=jellyfin_id(AMY)) == ["Arrival"] and listed(tv, Persons="local only") == ["Arrival"]
    assert listed(tv, GenreIds=jellyfin_id(synthetic_id("genre:Nope"))) == []
    assert listed(tv, Years="1997,2014") == ["Contact", "Paddington"]
    assert facet("/Items/Filters", tv, IncludeItemTypes="Movie") == {
        "Genres": ["Comedy", "Drama", "Science Fiction"], "Tags": ["cerebral"], "OfficialRatings": ["PG-13"], "Years": [1997, 2014, 2016]}
    assert facet("/Items/Filters2", tv)["Genres"][0] == {"Name": "Comedy", "Id": comedy}
    assert vault.jf.get("/Genres", headers=tv.headers, params={"ParentId": "0" * 32}).status_code == 404

    # Bob can see none of the admin's private titles: no facet, no filter match, no id lookup leaks them.
    assert facet("/Genres", bob)["Items"] == [] and facet("/Studios", bob)["Items"] == [] and facet("/Persons", bob)["Items"] == []
    assert listed(bob, GenreIds=comedy) == [] and listed(bob, Persons="Amy Adams") == []
    assert facet("/Items/Filters", bob) == {"Genres": [], "Tags": [], "OfficialRatings": [], "Years": []}
    assert vault.jf.get("/Genres").status_code in (401, 403)
