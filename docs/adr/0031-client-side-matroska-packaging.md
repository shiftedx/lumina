# ADR 0031: On-device Matroska packaging for native video

- Status: Accepted
- Date: 2026-10-20

## Context

AVPlayer rejects original Matroska files even when their compressed video and audio are supported. The previous native profile therefore requested server HLS remuxing. Advertising MKV without a client demuxer would select an unplayable original. The goal is original-file delivery without server media processing, retaining AVKit controls, hardware decoding and existing progress/reporting behavior.

## Decision

Use unmodified, exactly pinned PrismCore 3.8.1 through Swift Package Manager. Its LGPL MPVKit 1.0.1 dependency supplies FFmpeg demux/mux libraries. This is a necessary native media dependency: AVFoundation has no Matroska demuxer. Do not replace AVPlayer or enable the library's software player, audio bridge, dialogue processing or object-audio renderer in this slice.

`NativeVideoSource` owns one session and supplies an AVPlayerItem using a random loopback-only HLS URL. Input is the server's same-origin authenticated static original-file URL. The session packages compressed streams on demand on the device. Use coordinated HTTP range input, one muxed video/default-audio stream, a 64 MiB segment-cache budget and a four-second AVPlayer forward-buffer preference. The library's cache budget is a retention budget, not a hard cap on process RSS or on all buffered data. Bound the separate host subtitle-cue replay history to one byte (live-only): Lumina uses AVKit and registers no host cue overlay. Packaged WebVTT production remains intact. A future host overlay must explicitly choose its own replay window rather than restoring lifetime retention by default.

Advertise MKV only with H.264/HEVC and AAC/AC-3/E-AC-3/ALAC/FLAC. Existing video-depth, FLAC-depth and channel restrictions still apply. MKV+MP3 retains the previous server remux because this library's local audio policy would bridge it instead of copying it. DTS/TrueHD, unsupported video and subtitle burn-in retain existing server fallback decisions. A local preparation failure retains the existing explicit Convert action; it does not recursively start another local session.

Close, completion, failure and cancellation invalidate the owned session; stop/deinitialization tears down the listener and temporary files. Replay creates a new session. Pose changes on Duo retain the existing PlaybackModel and player. Playback reports refer to the original server request and its session, never the loopback URL.

## Consequences

- Qualified originals need no server FFmpeg child. Device demux/packaging costs replace server work; decoding remains native. Direct delivery does not mean zero client CPU.
- One default audio stream is carried in this slice, matching the old server HLS route. Broader track/subtitle selection, original subtitle rendering, HDR/Dolby Vision output and hardware audio output remain separate qualification tasks.
- Exact package lockfiles, bundled attribution/license text and in-app source/license access accompany the dependency. Resolution downloads frameworks for multiple platforms; downloaded size is not installed app size. The optimized host benchmark's binary-size increase is recorded in the performance review.
- The dependency's shipped TrueHDObjects archive lacks usable x86_64 tvOS simulator objects. Exclude that simulator architecture explicitly; physical Apple TV and Apple Silicon simulators use arm64. Intel tvOS simulator support requires an upstream archive correction. This is unrelated to enabling TrueHD playback, which remains off.
- Physical-device energy, thermal behavior, receiver output, large malformed/unindexed files, PiP/AirPlay and all older OS runtimes need release qualification. Focused frame/audio/seek, lifetime and real-server checks gate this slice; the full suite remains a release gate per the user.
