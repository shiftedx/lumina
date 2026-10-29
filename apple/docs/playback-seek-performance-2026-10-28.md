# Native video seek performance — 2026-10-28

The iOS timeline and horizontal gesture preview locally and commit once on
release. Buttons and remote commands use the same `PlaybackModel` seek path.
That path used to retain the newest destination while waiting for an older
AVPlayer seek to finish. A slow, obsolete HTTP read could therefore delay a
new destination that was already cached.

Committed requests now supersede the active submission immediately.
`PlaybackModel` cancels the obsolete task and native item seek before issuing
the latest target. Operation identity and item generation reject old
completions; live-window bounds are revalidated before submission. Playback
intent is retained, and cancelling gesture previews still issues no seek.
Dismissal and stream transitions also cancel pending native gesture seeks.

## Measurements

The retained `NativeVideoSourceTests.testRepeatedHTTPSeeksDoNotWaitForObsoleteTargets`
uses real AVPlayer decoding and NativeVideoSource/PrismCore packaging, with a
synthetic 90-second H.264/AAC MKV, ten-second keyframe intervals, and the existing
paced loopback HTTP fixture (64 KiB chunks, 100 ms between chunks).

| Scenario | Before | After |
| --- | ---: | ---: |
| New cached target after an uncached seek starts | 2.645 s to seek completion | 0.050 s through decoded frame |
| Individual uncached HTTP jumps | 2.56–3.12 s | 2.52–3.11 s |
| Local-file demux seeks | approximately 30–120 ms | approximately 30–120 ms |

The cached-target regression requires both destination completion and a decoded
frame within one second. The original serialized implementation failed this
check. State tests retain stale-completion, invalid-target, replacement, and
moving-window contracts while replacing the old serial-queue expectation.

A separate local 720p MP4 experiment measured approximately 38 ms median through
frame delivery with exact, 250 ms, and one-second seek tolerances. There was no
material benefit from reducing accuracy, so production tolerances are unchanged.

These are macOS AVFoundation measurements on synthetic media, not physical
Duo, 4K/HDR, WAN, or server-transcode latency guarantees. Fresh remote jumps
still need source bytes; this change removes the avoidable wait behind another
seek. Server transcoding and PrismCore internals were inspected but not changed.

Focused command:

```sh
mise exec -- swift test --package-path apple/Core --filter 'VideoPlaybackGestureStateTests|NativeVideoSourceTests'
```

Validation: 13 focused Core tests passed, the iPhone 17/iOS 27 gesture workflow
passed (101.6 s), and the shared tvOS Simulator Debug build passed. The UI workflow
covers paused forward/back surface scrubs, timeline dragging, skips and rotation.
Its entry route now uses Library. Playback is entered in landscape because a
separate portrait detail-page tab-bar overlap obstructs this fixture's Play CTA;
that UI defect is not changed here. No full suite was run.
