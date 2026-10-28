# Native iOS background lifecycle pass — 2026-10-28

The owner reported ordinary device use with Lumina in the background, followed by an Apple
logo and a passcode request. This is consistent with a device reboot. It is not reproduced
or attributed to Lumina. The physical
phone was unavailable to CoreDevice during this pass, and no report from the incident
was available. An app watchdog termination, memory-pressure eviction, SpringBoard restart,
and kernel panic require different evidence. Obtain the incident time, build, device/OS
and matching Lumina, JetsamEvent or panic report before claiming a cause. The owner found no
matching panic-full entry. The supplied accessory report records an audio-accessory RTKitOS
HARDFAULT, not a Lumina process crash or iPhone kernel panic. It does not establish a cause
for the reported phone reboot. Its accessory heap counters must not be interpreted as
Lumina or iPhone memory use. The original diagnostic remains outside the repository.

## Findings and changes

- Music and reader checkpoints acquired finite UIKit background time without expiration
  handlers. Both now end their assertion exactly once on completion or expiration. Expiration
  cancels the operation; the reader also cancels its outstanding save request. Atomic local
  music checkpoint writes remain bounded and can finish independently. Denied background
  time does not start new asynchronous work.
- The shared artwork cache had 128 MiB decoded and 32 MiB compressed RAM eviction targets
  (NSCache targets, not hard caps), but no suspension or memory-warning response. Backgrounding
  now drops both RAM tiers and cancels queued/speculative lookahead. Explicit image consumers,
  including system Now Playing artwork, remain usable, and private disk renditions survive.
  Late background completions cannot refill the RAM tiers. Foreground lookahead resumes.
  Views can still retain their displayed images; this does not claim to release every image.
- Lyric refresh and Sonos status polling now stop in the background and resume when active.
  Chromecast music/video status requests are skipped while inactive; canceled maintenance
  tasks cannot issue one final status request after waking. The existing receiver renewal
  policy remains foreground-only.
- Foreground connectivity and shared-queue refreshes already reject concurrent duplicate
  refreshes. Their cancellation semantics were left unchanged: connectivity currently treats
  a canceled request as an offline failure, so canceling it on every scene transition would
  introduce false offline states. These bounded one-shot refreshes are distinct from polling.

## Existing behavior retained

Home hero rotation and artwork motion already require an active scene. Paused music stops
its playback clock and pauses the audio engine. Local music retains its transport and system
metadata in the background. Video uses AVFoundation's automatic background/PiP policy;
backgrounding does not preempt automatic PiP by explicitly pausing the video. Connectivity
monitoring is event-driven. The app declares audio background mode only; this pass adds no
background mode, scheduler, dependency or telemetry upload.

## Focused evidence

The artwork regression failed against the previous behavior (resident images and background
lookahead remained), then passed after the change. Tests also cover cancellation with four
admitted decodes and twelve queued images, disk reuse on foreground return, explicit background
artwork access, and late completion after background-task expiration.

A standalone optimized macOS component probe loaded 100 distinct synthetic 400×600 JPEGs.
Three fresh processes per revision used the production cache/preparation code, with disk
caching disabled. Median physical footprint immediately before/after the background transition:

| Revision | Loaded | Background | Cached images after transition |
| --- | ---: | ---: | ---: |
| Previous behavior | 11.67 MiB | 11.67 MiB | 100 |
| This change | 11.66 MiB | 8.45 MiB | 0 |

Footprint is `proc_pid_rusage(RUSAGE_INFO_V4).ri_phys_footprint`; background sampling waits
200 ms after transition. Reproduce with `apple/scripts/benchmark_background_artwork.swift`
(the compile command is in its header; `--foreground-control` leaves the warm cache resident).
This is a host component result, not an entire-app, phone battery,
GPU or physical-device memory measurement. Ignored probe sources and raw outputs are in
`output/background-lifecycle/`.

Targeted Core checks: 18 passing cases across `BillboardArtworkPreparationTests` and
`ExpiringBackgroundWorkTests`, including the reproduced expiration-before-dispatch race.
Five native workflow checks passed on the isolated iOS 27.1 Duo simulator: three idle
Home → Settings → Home cycles, three music
background/foreground cycles with lock-screen artwork, EPUB checkpoint/restore, video
foreground recovery with deliberate pause retained, and the Cast picker return to local playback.
The focused result bundle is `/tmp/lumina-background-lifecycle-proof.xcresult`. A normal
Debug iOS Simulator app build without UI-testing compilation flags also passed after the
expiration race fix. No full suite was run.

Initial validation attempts encountered navigation setup blockages before the lifecycle step:
the general Books journey tapped under the closed Duo utility rail, and the standard-phone
tour helper could not reach its Home Browse menu. The focused reader check taps the available
leading area of the EPUB card. These setup failures are retained in the local result bundles.
The Duo simulator reports PiP unsupported: its unsupported-control check passed, but this is
not evidence of actual background PiP continuation. Physical-device PiP, energy use, receiver
status maintenance and the reported reboot still need device/receiver diagnostics.

Apple references: [finite background time](https://developer.apple.com/documentation/uikit/uiapplication/beginbackgroundtask(withname:expirationhandler:)),
[memory warnings](https://developer.apple.com/documentation/uikit/responding-to-memory-warnings),
and [jetsam reports](https://developer.apple.com/documentation/xcode/identifying-high-memory-use-with-jetsam-event-reports).
