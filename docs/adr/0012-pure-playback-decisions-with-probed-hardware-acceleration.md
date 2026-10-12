# ADR 0012: Pure playback decisions with probed hardware acceleration

- Status: Accepted
- Date: 2026-09-25

## Context

The web player and Jellyfin clients need direct play when possible, and a hardware transcode otherwise, including 4K HDR tone-mapped to SDR on an Intel QSV host. Jellyfin 10.11 transcoded everything by treating unknown device-profile conditions as failures. The previous container dropped supplementary groups (`gosu`), so `/dev/dri` was unreachable. Re-encoding audio only to normalize loudness or mute words wastes CPU and breaks direct play.

## Decision

- The playback decision is one pure, table-tested function, `decide(client caps, streams, limits, audio, subtitle)`. Its preference order is direct > remux > audio-only encode > video encode. Video is never re-encoded because of audio or a text subtitle. Unknown profile conditions are ignored, and reasons use Jellyfin's `TranscodeReasons` names. Those reasons travel in the `TranscodingUrl` query string (`TranscodeReasons=...`), the field Jellyfin clients read them from; `MediaSourceInfo` JSON never carries them.
- Hardware acceleration (`auto|off|qsv|vaapi`) is probed at startup with a one-frame test encode. `auto` tries qsv, then vaapi, then software. A hardware session that fails before its first manifest retries once in software, and repeated failures pin the process to software until restart. The container ships jellyfin-ffmpeg, the Intel OpenCL runtime on amd64, and a `setpriv` drop that grants only the host render group.
- Loudness normalization and profanity mute are applied in the browser by one shared Web Audio graph. Audio is never re-encoded for either, and ffmpeg `volume=` is added only when audio is already being encoded for a Jellyfin client.

## Consequences

- Hosts without a GPU and Docker Desktop on a Mac keep working in software with the same code path.
- One rendition per session bounds GPU cost. Quality changes restart the session at the current position.
- Mute and normalization are web-player features. Infuse and other apps play unfiltered, and the settings copy says so.
