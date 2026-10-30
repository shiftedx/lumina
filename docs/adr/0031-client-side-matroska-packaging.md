# ADR 0031: On-device Matroska packaging for native video

- Status: Accepted
- Date: 2026-10-20

## Context

AVPlayer rejects original Matroska files even when their compressed video and audio are supported. The previous native profile therefore requested server HLS remuxing. Advertising MKV without a client demuxer would select an unplayable original. The goal is original-file delivery without server media processing, retaining AVKit controls, hardware decoding and existing progress/reporting behavior.

## Decision

Use exactly pinned PrismCore 3.8.1 plus Lumina's final-segment
conversion-statistics and explicitly paired Profile 7 packaging patches through
Swift Package Manager. The fork's immutable revision and public source are
recorded in the package pin and bundled notices. Its LGPL MPVKit 1.0.1
dependency supplies FFmpeg demux/mux libraries. This is a necessary native
media dependency: AVFoundation has no Matroska demuxer. Do not replace AVPlayer
or enable its software player, dialogue processing or object-audio renderer in
this slice. The selected audio track must be
stream-copyable; a bridgeable selection uses the server fallback.

`NativeVideoSource` owns one session and supplies an AVPlayerItem using a random loopback-only HLS URL. Input is the server's same-origin authenticated static original-file URL. The session packages compressed streams on demand on the device. Use coordinated HTTP range input, one selected stream-copyable audio choice, a 64 MiB segment-cache budget and a four-second AVPlayer forward-buffer preference. The complete master can carry audio as a separate HLS rendition rather than muxing it into the video fragments. A requested source audio-stream index uses a separate native candidate. Lumina accepts it only when PrismCore confirms that exact stream is stream-copied; a bridgeable or unavailable selection fails so the caller can retain the current item and use its server fallback. Retain Prism's complete master when supplied, including Dolby signaling and declared audio/subtitle renditions. When Prism supplies media-direct delivery, retain it without constructing a new master: the engine may have deliberately declined a master for the current display. An explicitly requested subtitle on that route reports a narrow selection failure and uses the existing one-shot compatible server delivery; unselected embedded subtitle tracks must not turn otherwise playable video into an invalid master. Genuine engine masters retain their complete audio/subtitle and HDR declarations. The local subtitle wrapper remains for selected server-HLS WebVTT sidecars, using the source bitrate or a conservative 50 Mb/s scheduling ceiling. A selected external sidecar is first fetched and URL-validated by the client into a bounded local SRT/VTT file. For a Prism source it is registered before start and appears in that master. For a server HLS stream, one bounded local WebVTT sidecar is copied into the same private wrapper with a VOD playlist and an `X-TIMESTAMP-MAP`; progressive MP4 cannot receive an appended AVFoundation legible group, so the client explicitly requests compatible server HLS for that selection. The library's cache budget is a retention budget, not a hard cap on process RSS or on all buffered data. Bound the separate host subtitle-cue replay history to one byte (live-only): Lumina uses AVKit and registers no host cue overlay. Packaged WebVTT production remains intact. A future host overlay must explicitly choose its own replay window rather than restoring lifetime retention by default.

Positive initial/resume seeks and transactional track replacements use a target-aware
readiness check. `AVPlayerItem.status == readyToPlay` alone can precede the
manifest timeline. Readiness additionally requires finite duration covering the
requested target or a seekable range containing it; it never requires media to
be buffered at that target. KVO for status, duration, seekable ranges and item
replacement shares one bounded timeout/cancellation path. Zero-position starts
retain status-only readiness. An EVENT window that does not cover a requested
position is not promoted to full-file seek evidence.

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

Separate-track packaging additionally requires literal
`LuminaDolbyVisionSeparateTrackPackaging: true` on that same MKV row. This
bounded legacy-MKV route accepts two HEVC video tracks with complementary P7
configuration records: the first carries BL only, the other EL and RPU only,
with matching profile, level and compatibility ID 6. Both records must be
complete; an arbitrary second HEVC track, a title tag or a guessed relationship
is insufficient. Older clients' conversion declaration does not gain this
permission. The producer independently validates the pair and requires one
matching EL RPU per base access unit, using presentation timestamps while
retaining base decode order. Pending packets are bounded and cleared on seek
or source reanchor; missing, duplicate, malformed, ambiguous or mismatched
input fails with a typed conversion error. The EL pictures are discarded and
only the converted RPU is appended to the untouched base pictures.

This is structural support for explicitly signaled legacy MKV, not a new
Matroska mapping or full enhancement-layer decoding.

For 192-byte M2TS, a separate top-level `LuminaDolbyVisionTransportProfiles`
capability opts into P7-to-P8.1 packaging. It is never an ordinary M2TS direct
row: older servers ignore the whole extension and retain their HLS decision.
The new server requires a local `.m2ts` original, MPEG-TS probe facts, exactly
two Main 10 HEVC streams in one program and a validated dependency marker.
A bounded four-MiB PAT/PMT scan verifies CRCs, section/packet framing, stable
program metadata and an explicit DOVI EL descriptor naming the HEVC BL PID.
The BL need not carry a Dolby configuration record. Stream order, adjacent
PIDs and conventional HDMV PID values are insufficient evidence. The
[Dolby transport-stream specification](https://professional.dolby.com/siteassets/pdfs/dolby-vision-bitstreams-in-mpeg-2-transport-stream-multiplex-v1.2.pdf)
defines that dependency and the presentation-timestamp relationship.

Only the qualified direct response carries
`LuminaClientPackaging: p7_m2ts_to_p81_drop_el`. Native M2TS demuxing requires
both that marker and the explicit source container. It also enables the
engine's required-pairing option: the engine independently validates the
transport, stream IDs, codec configuration and timestamp pairing before
publishing converted output. Missing or invalid association cannot silently
become plain HDR10 playback. Annex-B base pictures remain unchanged; converted
RPUs are appended before FFmpeg normalizes the MP4 framing. Selected AAC
ADTS packets are normalized to ASC for fMP4. The selected audio must remain
stream-copyable, and external text sidecars retain the local master. The
range exception applies only to this direct candidate, never HLS fallback.
MPEG-TS's demuxer index can contain PCR seek anchors that are not picture
keyframes. The qualified route must instead observe actual BL HEVC IRAP
boundaries across the source before publishing a full VOD playlist. Bounded
sparse timestamp probes may locate those boundaries, but the timestamp index
itself is never promoted to picture evidence. Every published boundary must
be a real observed IRAP; the final tail requires EOF evidence. Neither a
head-only scout nor a partial producer cache may infer the unseen tail's GOP
cadence for this strict transport route. Otherwise a later GOP increase can
make several advertised entries resolve to the same picture, corrupting the
playlist timeline.

The scout shares a total two-second / 64-MiB AVIO budget across its seeks and
reads. An incomplete proof retains sequential EVENT playback and its produced
seek range. Source and cancellation failures keep their ordinary classification.
A complete producer-harvested map can still supply full seeking on a later
session. These limits deliberately bound client startup reads.

For larger or slower originals, the server can prepare a complete index locally
before native playback opens the source. Only the marked direct M2TS candidate
receives `LuminaM2TSIRAPIndexUrl`. The authenticated endpoint is bound to the
selected visible source, returns pending/unsupported/complete, and shares the
original-file authorization and screen-time checks. A single bounded worker
scans the full transport file, verifies stable PAT/PMT association and continuity,
records actual BL IRAPs with exact-PTS EL partners, and publishes atomically only
after EOF and unchanged size/mtime. Each byte offset starts at the earliest BL
or matching EL PES, including EL-first layouts. Ordinary non-IRAP pairs are
retired as they match, so pairing memory does not grow with runtime.

The native client polls pending preparation cancellably. A complete response
carries the original HTTP strong ETag, size, stream/timebase, base/enhancement
PIDs, actual keyframe PTS, and aligned monotonic packet offsets. The engine
accepts it only after matching source representation and program metadata;
reopens repeat that validation. Missing, malformed, stale, or unsupported maps
retain the bounded scout behavior. Authorization and cancellation remain errors.
A supplied map never becomes locally harvested proof. Deep seeks still verify
the actual BL IRAP and matching valid EL/RPU after byte reanchoring; the map
cannot bypass conversion validation. The index cache is additive filesystem
state, invalidated with the original fingerprint; no database migration is needed.

A deep reanchor uses FFmpeg timestamp seeking only as a locality hint. Before
publishing video it must locate the planned real BL IRAP and its exact-PTS
valid EL/RPU. Separate search/candidate/verified states keep post-keyframe BL
pictures while metadata is still arriving. Earlier BL/EL pre-roll is discarded
at the accepted IRAP PTS; decode order remains unchanged. Missing or malformed
pairs and exhausted locality bounds fail closed. EOF does not forgive
unmatched future enhancement metadata.

Head resets use the proven BL stream rather than the first/default video
stream, which can be EL. The startup proof is ephemeral; only normal producer
harvesting persists actual packet keys. Unsupported or unseekable sources and
incomplete bounded proof retain limited produced-range seeking.
Generic TS/MTS, unmarked HDMV layouts and offline M2TS downloads do not gain
this capability. Probe cache version 7 invalidates older facts; no SQL schema
migration is needed.

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
that a device presented Dolby Vision. Typed separate-track failures also
invalidate the verdict; native failure teardown retains that evidence until
explicit stop or replacement so the existing recovery policy can observe it.
Physical output still needs qualification.

Subtitle timing uses Prism’s existing bounded offset before session start; positive values show text later. Timing changes replace candidates transactionally so AVPlayer cannot retain subtitle segments produced with the old offset. Selected server-HLS WebVTT sidecars are staged with adjusted cue timing. This does not add a caption overlay or change burned-in/bitmap captions. Quality ceilings use the existing server PlaybackInfo decision and preserve this native decoding boundary.

Close, completion, failure and cancellation invalidate the owned session; stop/deinitialization tears down the listener and temporary files. Replay creates a new session. Pose changes on Duo retain the existing PlaybackModel and player. Playback reports refer to the original server request and its session, never the loopback URL.

## Consequences

- Qualified originals need no server FFmpeg child. Device demux/packaging costs replace server work; decoding remains native. Direct delivery does not mean zero client CPU.
- Each native candidate selects a default or explicit stream-copyable audio track and swaps candidates transactionally. The retained engine master can declare separate audio renditions; this slice qualifies the selected AAC route, not every alternate rendition or receiver format. Embedded text tracks and registered local external SRT/VTT sidecars are declared as native AVPlayer subtitle options. A direct server HLS item can similarly offer one downloaded VTT sidecar; a direct progressive MP4 cannot, and the client requests server HLS rather than silently substituting another local pipeline. Broader original subtitle rendering, HDR/Dolby Vision output and hardware audio output remain separate qualification tasks.
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
