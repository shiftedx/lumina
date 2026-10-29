# ADR 0012: Pure playback decisions with probed hardware acceleration

- Status: Accepted
- Date: 2026-09-25

## Context

The web player and Jellyfin clients need direct play when possible, and a hardware transcode otherwise, including 4K HDR tone-mapped to SDR on common Intel, AMD and NVIDIA Linux servers. Jellyfin 10.11 transcoded everything by treating unknown device-profile conditions as failures. The previous container dropped supplementary groups (`gosu`), so `/dev/dri` was unreachable. Re-encoding audio only to normalize loudness or mute words wastes CPU and breaks direct play.

## Decision

- The playback decision is one pure, table-tested function, `decide(client caps, streams, limits, audio, subtitle)`. Its preference order is direct > remux > audio-only encode > video encode. Video is never re-encoded because of audio or a text subtitle. Unknown profile conditions are ignored, and reasons use Jellyfin's `TranscodeReasons` names. Those reasons travel in the `TranscodingUrl` query string (`TranscodeReasons=...`), the field Jellyfin clients read them from; `MediaSourceInfo` JSON never carries them.
- A client audio bit-depth ceiling is checked against normalized probe facts for direct delivery and HLS stream copy. Unknown depth or an exceeded ceiling keeps audio conversion while compatible video stays copied. The native FLAC profile uses this to retain 16/24-bit lossless audio without falsely advertising 32-bit decoding.
- Hardware acceleration (`auto|off|qsv|vaapi|nvenc`) is probed at startup with a one-frame H.264 encode. Linux `auto` tries Intel QSV, NVIDIA NVENC, then VA-API; it uses only a candidate whose actual probe succeeds. QSV and VA-API use the configured DRM render node, while NVENC/NVDEC/CUDA use the configured visible CUDA GPU. Hardware decode is used only when that input is accepted; a session may retain hardware encode after falling back to software decode. A hardware session that fails before its first manifest retries once in software, and repeated hardware failures pin the process to software until restart.
- The container ships jellyfin-ffmpeg, Mesa VA-API userspace drivers and the Intel OpenCL runtime on amd64. The NVIDIA driver is host-owned and mounted by NVIDIA Container Toolkit; the image never bundles a proprietary NVIDIA library. The `setpriv` drop grants only the host render group for DRM paths. Separate Compose overrides pass either `/dev/dri` or the NVIDIA GPU, and the probe keeps a misconfigured host on software conversion.
- A passing OpenCL or CUDA tone-map path enables Dolby Vision profile 5 reshaping. CUDA uses the metadata-preserving NVDEC path and explicit DOVI processing; a failed DV5 session is never sent through the software tone mapper. The synthetic PQ probe checks the pipeline, while RPU metadata handling still requires a real DV5 sample on the target GPU.
- Native servers also accept `videotoolbox` (macOS) and `amf` (Windows) when their FFmpeg and drivers pass the encoder probe. macOS auto selects VideoToolbox; Windows tries QSV, NVENC and AMF. VideoToolbox can decode to CPU frames for the shared filter graph; AMF uses CPU decoding and filtering. The Windows server path remains unqualified. These native backends are not available inside the Linux image.
- Loudness normalization and profanity mute are applied in the browser by one shared Web Audio graph. Audio is never re-encoded for either, and ffmpeg `volume=` is added only when audio is already being encoded for a Jellyfin client.

## Consequences

- Linux Docker Engine supports Intel QSV, Intel/AMD VA-API and NVIDIA NVENC/NVDEC/CUDA when the host driver, container access and its installed GPU capabilities permit them. Docker Desktop for Mac has no `/dev/dri` and NVIDIA Container Toolkit does not support it, so the Linux image remains software-only there. Native VideoToolbox and AMF are outside this Linux container deployment.
- One encode per session bounds GPU cost. Quality changes restart the session at the current position.
- Output stays H.264/AAC for broad HLS client compatibility. HEVC and AV1 encoding are not negotiated yet, and Rockchip needs a separate FFmpeg/device integration; these are deliberate parity gaps rather than claimed support.
- Mute and normalization are web-player features. Infuse and other apps play unfiltered, and the settings copy says so.

## Roku audio rendition compatibility

Roku HLS playback exposes separate fMP4 video and audio renditions because some Roku decoders play combined fMP4 video without audio. The existing decision and encoder stay shared; completed segments are remuxed once into cached video-only and audio-only segments without re-encoding. Both playlists use the same whole-file plan and packet timestamps. Cached renditions count against the existing playback cache cap and disappear with the session. Other clients retain combined fMP4 delivery.

Roku's HLS fallback uses stereo AAC even when its profile advertises Dolby passthrough. Copied EAC3 can leave an unreadable fMP4 initialization header, and the separate Dolby rendition can fail in the player. Compatible direct playback retains the client's original audio capabilities; the fallback still copies video whenever possible. PlaybackInfo and the session use the same fallback capabilities.
