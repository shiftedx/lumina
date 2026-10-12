"""Deterministic extractor for Lumina's reserved-domain artifact fixtures."""

from __future__ import annotations

from urllib.parse import urljoin

from yt_dlp.extractor.common import InfoExtractor


class LuminaFixtureIE(InfoExtractor):
    IE_NAME = "lumina:fixture"
    IE_DESC = False
    _VALID_URL = r"http://fixture\.lumina\.invalid:8080/(?P<id>progressive\.mp4|slow\.mp4|video\.mp4|audio\.m4a|manifest\.mpd|redirect|protected\.mp4|unavailable|partial-playlist)"

    def _real_extract(self, url: str) -> dict:
        fixture_id = self._match_id(url)
        response = self._request_webpage(url, fixture_id, note="Validating deterministic Lumina fixture")
        final_url = response.url
        response.close()
        base = urljoin(final_url, "/")
        channel_url = urljoin(base, "partial-playlist")
        if fixture_id == "partial-playlist":
            entries = [
                self.url_result(
                    urljoin(base, "audio.m4a"),
                    ie=LuminaFixtureIE.ie_key(),
                    video_id="partial-available",
                    video_title="Lumina fixture available entry",
                ),
                self.url_result(
                    "http://127.0.0.1/unavailable",
                    video_id="partial-unavailable",
                    video_title="Lumina fixture policy-rejected entry",
                ),
            ]
            result = self.playlist_result(entries, fixture_id, "Lumina fixture partial playlist")
            result.update({"channel": "Lumina Fixture Channel", "channel_url": channel_url})
            return result
        if fixture_id == "manifest.mpd":
            formats = [
                {
                    "format_id": "fixture-video",
                    "url": urljoin(base, "video.mp4"),
                    "protocol": "http",
                    "ext": "mp4",
                    "vcodec": "av01.0.04M.08",
                    "acodec": "none",
                    "height": 180,
                },
                {
                    "format_id": "fixture-audio",
                    "url": urljoin(base, "audio.m4a"),
                    "protocol": "http",
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "opus",
                },
            ]
        elif fixture_id == "audio.m4a":
            formats = [{
                "format_id": "fixture-audio", "url": final_url, "protocol": "http", "ext": "m4a",
                "vcodec": "none", "acodec": "opus",
            }]
        else:
            media_url = urljoin(base, "progressive.mp4") if fixture_id in {"redirect", "protected.mp4"} else final_url
            formats = [{
                "format_id": "fixture-av", "url": media_url, "protocol": "http", "ext": "mp4",
                "vcodec": "av01.0.04M.08", "acodec": "opus", "height": 180,
            }]
        return {
            "id": fixture_id.rsplit(".", 1)[0],
            "title": f"Lumina fixture {fixture_id}",
            "webpage_url": final_url,
            "formats": formats,
            "uploader": "Lumina Fixture Channel",
            "channel": "Lumina Fixture Channel",
            "channel_url": channel_url,
        }
