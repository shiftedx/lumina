# ADR 0031: On-device Matroska packaging for native video

- Status: Accepted
- Date: 2026-10-20

## Context

AVPlayer rejects original Matroska files even when their compressed video and audio are supported. The previous native profile therefore requested server HLS remuxing. Advertising MKV without a client demuxer would select an unplayable original. The goal is original-file delivery without server media processing, retaining AVKit controls, hardware decoding and existing progress/reporting behavior.

## Decision

Use exactly pinned PrismCore 3.8.1 plus Lumina's minimal final-segment
conversion-statistics patch through Swift Package Manager. The fork changes only
the Profile 7 converter-statistics publication after the EOF tail has been
flushed; its immutable revision and public source are recorded in the package
pin and bundled notices. Its LGPL MPVKit 1.0.1 dependency supplies FFmpeg
demux/mux libraries. This is a necessary native media dependency: AVFoundation
has no Matroska demuxer. Do not replace AVPlayer or enable the library's
software player, audio bridge, dialogue processing or object-audio renderer in
this slice.

`NativeVideoSource` owns one session and supplies an AVPlayerItem using a random loopback-only HLS URL. Input is the server's same-origin authenticated static original-file URL. The session packages compressed streams on demand on the device. Use coordinated HTTP range input, one muxed video/default-audio stream, a 64 MiB segment-cache budget and a four-second AVPlayer forward-buffer preference. A requested source audio-stream index uses a separate native candidate. Lumina accepts it only when PrismCore confirms that exact stream is stream-copied; a bridgeable or unavailable selection fails so the caller can retain the current item and use its server fallback. When Prism produces embedded WebVTT renditions, Lumina serves a short-lived, loopback-only subtitle master that declares only those subtitle playlists and the existing muxed variant; it does not enable Prism's all-audio-rendition master, which could select an audio bridge. The client supplies the source bitrate to that fixed variant; a conservative 50 Mb/s fallback is a scheduling ceiling, not a media capability claim. A selected external sidecar is first fetched and URL-validated by the client into a bounded local SRT/VTT file. For a Prism source it is registered before start and appears in that master. For a server HLS stream, one bounded local WebVTT sidecar is copied into the same private wrapper with a VOD playlist and an `X-TIMESTAMP-MAP`; progressive MP4 cannot receive an appended AVFoundation legible group, so the client explicitly requests compatible server HLS for that selection. The library's cache budget is a retention budget, not a hard cap on process RSS or on all buffered data. Bound the separate host subtitle-cue replay history to one byte (live-only): Lumina uses AVKit and registers no host cue overlay. Packaged WebVTT production remains intact. A future host overlay must explicitly choose its own replay window rather than restoring lifetime retention by default.

Advertise MKV only with eligible H.264/HEVC and AAC/AC-3/E-AC-3/ALAC/FLAC. Existing video-depth, FLAC-depth and channel restrictions still apply. MKV+MP3 retains the previous server remux because this library's local audio policy would bridge it instead of copying it. AV1 MKV also uses server HLS stream copy when the native AV1 HLS capability is present; it is not advertised as qualified Prism packaging. DTS/TrueHD, unsupported video and subtitle burn-in retain existing server fallback decisions. An initial codec/container failure can request H.264/SDR HLS once, retaining source, quality, track selections and position. The same guard covers rejection just after readiness but before playback advances. Authorization, network, cancellation and transient-resource failures do not trigger this retry; an already-compatible request cannot retry again. This does not recursively start another local session.

Native playback profiles are built for each request from VideoToolbox hardware-decoder availability, exact AVFoundation MP4 codec queries and current `AVPlayer.eligibleForHDRPlayback`. HEVC/AV1 fMP4 HLS eligibility also relies on Apple's documented HLS support; a MIME query alone does not validate an HLS manifest. Explicit SDR/HDR10/HLG and bit-depth/4K ceilings prevent direct-play claims from broadening a more limited HLS path. Display changes are reflected by the next request. Dolby Vision/HDR10+ re-encoding and receiver/display qualification remain separate.

Dolby Vision direct delivery uses a separate `LuminaDolbyVisionProfiles` array
on each `DirectPlayProfiles` entry. Each capability names `Profile`,
`CompatibilityIDs` and `MaxLevel`; it never broadens shared video-range or HLS
encoder claims. The native policy requires Main 10 HEVC, current HDR eligibility,
a Dolby Vision-specific output-route mode and an exact positive MP4 Dolby
Vision codec query. Profile 5 additionally requires Dolby Vision hardware
decode. Profile 8 compatibility ID 1 requires iOS/iPadOS 27 or the supported
tvOS runtime; compatibility ID 4 is the older HLG-compatible variant. Unknown
profiles, Profile 7 and enhancement layers are not authorized by this policy.
The backend checks the matching container/audio/video row and fully probed
profile, compatibility ID, level and RPU/base/enhancement-layer flags. An empty
array denies Dolby Vision direct play on that row; an absent extension retains
older clients' existing behavior. This exception applies only to an otherwise
eligible direct candidate: audio, subtitles or quality constraints cannot carry
it into a server HLS stream-copy decision. Ordinary base-layer fallback remains
separate from a Dolby Vision preservation claim. The Profile 8.1 OS boundary
follows [Apple's 2026 HLS update](https://developer.apple.com/streaming/Whats-new-HLS.pdf);
the Profile 8.4 path follows [Apple's AVFoundation guidance](https://developer.apple.com/av-foundation/Incorporating-HDR-video-with-Dolby-Vision-into-your-apps.pdf).

The same bounded Profile 5/8 capabilities are advertised on the HEVC Matroska
row after qualifying the pinned engine's packaging path independently. That
path retains the source Dolby configuration and complete RPU sequence; adding
an external subtitle retains Prism's full master, including primary or
supplemental Dolby signaling and the PQ/HLG range. The tests materialize every
independent seek fragment and compare its configuration and packet-level RPU
sequence with the source. This is stream-copy delivery, not Dolby Vision
re-encoding, Profile 7 conversion, or physical-display certification.

Profile 7 has a separate MKV-only packaging declaration,
`LuminaDolbyVisionPackagingConversions`. The accepted shape is source profile
7/compatibility 6 to target profile 8/compatibility 1, with a bounded `MaxLevel`
and explicit `EnhancementLayerDisposition: "dropped"`. It requires Main 10
HEVC and fully probed RPU/base/enhancement-layer flags. Native clients emit it
only with the existing Profile 8.1 decoder/output capability and a build that
includes the pinned engine's Libdovi converter. Original-preservation profiles,
MP4 and server HLS do not inherit this permission. Other quality, track, audio
and subtitle constraints remain in force.

Prism's mode-2 conversion rewrites P7 RPUs to single-layer P8.1, removes EL
NALs and copies the base pictures. It does not distinguish MEL from FEL; FEL
enhancement precision is lost. The playback-info row describes that conversion
and explicitly says the enhancement layer is omitted. This is not full P7/FEL
decoding or a lossless Dolby Vision preservation claim.

`NativeVideoSource` exposes the pinned engine's produced-segment conversion
verdict. Non-clean evidence, including zero converted RPUs or zero removed EL
NALs, fails validation. The minimal fork publishes the same snapshot after the
EOF tail is flushed, so a final-tail conversion refusal cannot remain hidden
behind the preceding clean segment. The existing one-second playback callback
samples this evidence with source/request identity guards; known failures pause
playback and can use one compatible retry even after advancement, retaining
position and selected options. Playback/rate guards keep a known-bad source
paused if that replacement fails. This remains observation rather than an
atomic all-file preflight: the result says what the producer processed, not
that a device presented Dolby Vision. Separate-track EL inputs and physical
output still need qualification; the strict conversion verdict may retain their
server fallback.

Subtitle timing uses Prism’s existing bounded offset before session start; positive values show text later. Timing changes replace candidates transactionally so AVPlayer cannot retain subtitle segments produced with the old offset. Selected server-HLS WebVTT sidecars are staged with adjusted cue timing. This does not add a caption overlay or change burned-in/bitmap captions. Quality ceilings use the existing server PlaybackInfo decision and preserve this native decoding boundary.

Close, completion, failure and cancellation invalidate the owned session; stop/deinitialization tears down the listener and temporary files. Replay creates a new session. Pose changes on Duo retain the existing PlaybackModel and player. Playback reports refer to the original server request and its session, never the loopback URL.

## Consequences

- Qualified originals need no server FFmpeg child. Device demux/packaging costs replace server work; decoding remains native. Direct delivery does not mean zero client CPU.
- One default or explicitly requested, stream-copyable audio stream is carried per native candidate, matching the old server HLS route while allowing the host to swap candidates transactionally. Embedded text tracks and registered local external SRT/VTT sidecars are declared as native AVPlayer subtitle options without advertising alternate audio routes. A direct server HLS item can similarly offer one downloaded VTT sidecar; a direct progressive MP4 cannot, and the client requests server HLS rather than silently substituting another local pipeline. Broader original subtitle rendering, HDR/Dolby Vision output and hardware audio output remain separate qualification tasks.
- Exact package lockfiles, bundled attribution/license text and in-app source/license access accompany the dependency. Resolution downloads frameworks for multiple platforms; downloaded size is not installed app size. The optimized host benchmark's binary-size increase is recorded in the performance review.
- The dependency's shipped TrueHDObjects archive lacks usable x86_64 tvOS simulator objects. Exclude that simulator architecture explicitly; physical Apple TV and Apple Silicon simulators use arm64. Intel tvOS simulator support requires an upstream archive correction. This is unrelated to enabling TrueHD playback, which remains off.
- Physical-device energy, thermal behavior, receiver output, large malformed/unindexed files, PiP/AirPlay and all older OS runtimes need release qualification. Focused frame/audio/seek, lifetime and real-server checks gate this slice; the full suite remains a release gate per the user.
- This on-device Matroska route does not establish support for a new server HLS
  video codec. In particular, AV1 fMP4 HLS is an AVFoundation delivery
  qualification separate from PrismCore's local packaging: decoder availability
  and direct-MP4 container checks can make it eligible, but a loopback HLS
  probe must observe decoded frames, time advancement and seek for the exact
  platform/profile before it is reported as locally qualified. That probe does
  not certify HDR display, HDMI or audio output.
