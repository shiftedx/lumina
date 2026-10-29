#!/usr/bin/env python3
"""Create a bounded, local Dolby Vision Profile 5 MKV fixture for opt-in checks.

The source is Apple's public Advanced HLS example.  This downloads only the
Profile 5 init section and two media fragments, then stream-copies its video
into Matroska.  The resulting file is intentionally outside the repository:
it is test evidence, not a redistributable fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


APPLE_ADVANCED_MASTER = "https://devstreaming-cdn.apple.com/videos/streaming/examples/adv_dv_atmos/main.m3u8"
MAX_PLAYLIST_BYTES = 1 << 20


def fetch(url: str, *, limit: int) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "devstreaming-cdn.apple.com":
        raise ValueError("fixture input must remain on Apple's public devstreaming CDN")
    request = Request(url, headers={"User-Agent": "Lumina DV fixture qualifier"})
    with urlopen(request, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} exceeded the {limit:,}-byte bound")
    return data


def uri_attribute(line: str) -> str | None:
    match = re.search(r'URI="([^"]+)"', line)
    return match.group(1) if match else None


def media_lines(playlist: str) -> list[str]:
    return [line.strip() for line in playlist.splitlines() if line.strip() and not line.startswith("#")]


def profile5_variant(master_url: str, master: str) -> str:
    lines = master.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:") or "dvh1.05" not in line.lower():
            continue
        for candidate in lines[index + 1:]:
            candidate = candidate.strip()
            if candidate and not candidate.startswith("#"):
                return urljoin(master_url, candidate)
    raise ValueError("Apple Advanced example no longer exposes a Dolby Vision Profile 5 variant")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="existing or new directory outside the repository")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--max-object-bytes", type=int, default=64 << 20)
    args = parser.parse_args()
    if args.max_object_bytes <= 0:
        parser.error("--max-object-bytes must be positive")

    destination = args.destination.expanduser().resolve()
    if any((candidate / ".git").exists() for candidate in [destination, *destination.parents]):
        parser.error("destination must be outside a Git checkout")
    destination.mkdir(parents=True, exist_ok=True)

    master = fetch(APPLE_ADVANCED_MASTER, limit=MAX_PLAYLIST_BYTES).decode("utf-8")
    variant_url = profile5_variant(APPLE_ADVANCED_MASTER, master)
    media = fetch(variant_url, limit=MAX_PLAYLIST_BYTES).decode("utf-8")
    map_line = next((line for line in media.splitlines() if line.startswith("#EXT-X-MAP:")), None)
    map_uri = uri_attribute(map_line or "")
    segments = media_lines(media)[:2]
    if map_uri is None or len(segments) != 2:
        raise ValueError("expected one init map and at least two Profile 5 fragments")
    if "BYTERANGE" in media:
        raise ValueError("the bounded qualifier deliberately does not reconstruct byte-range media")

    init = fetch(urljoin(variant_url, map_uri), limit=args.max_object_bytes)
    fragment_data = [fetch(urljoin(variant_url, segment), limit=args.max_object_bytes) for segment in segments]
    input_mp4 = destination / "apple-dv5-two-fragment.mp4"
    output_mkv = destination / "apple-dv5-two-fragment.mkv"
    input_mp4.write_bytes(init + b"".join(fragment_data))
    subprocess.run(
        [args.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(input_mp4),
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "96k", "-shortest", str(output_mkv)],
        check=True,
        timeout=120,
    )
    print(f"fixture={output_mkv}")
    print(f"sha256={hashlib.sha256(output_mkv.read_bytes()).hexdigest()}")
    print(f"source={APPLE_ADVANCED_MASTER}")
    print("set LUMINA_DV5_MKV_FIXTURE to this MKV for the opt-in NativeVideoSource contract")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"DV5 fixture qualification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
