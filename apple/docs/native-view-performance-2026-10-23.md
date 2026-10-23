# Native view performance pass — 2026-10-23

This is a factual, partial record of the native iOS and tvOS performance pass. It draws
on the disposable-loopback artifacts under `/private/tmp/lumina-native-perf-20261022`.
The artifacts contain no production server, account, or media data.

## Status at handoff

| Cohort | Status | Captured scope |
| --- | --- | --- |
| `ios-baseline-r2` | Closed, partial | Sign-in, Home, featured detail, library, movie detail, and show detail windows; 346 samples. |
| `ios-optimized` | Closed, partial | The same navigation windows after the first set of UI changes; 351 samples and a Time Profiler export. |
| `ios-resume` | Closed, partial | Home and show/season resume navigation windows; 257 samples. |
| `ios-playback` | Closed, partial | Video transport/settings, audio player/queue, and Requests windows; 784 samples and a Time Profiler export. |
| `ios-closed-isolated` | Closed, partial | Account and Home editor, prose and comic catalog/reader windows; 316 samples and a valid single-process Time Profiler attachment. |
| `ios-closed-cycles` | Closed | All five workflow cycles passed with Time Profiler. |
| `ios-closed-lifecycle-r3` | Closed | All five workflow cycles passed without Time Profiler; lifecycle instrumentation recorded unique tokens and teardown state. |
| `ios-closed-layer` | Closed | All five workflow cycles passed after the production video path moved to `AVPlayerLayer`. |
| `ios-closed-final` | Aborted, incomplete | Release test namespace isolation was absent and the previous sampler teardown timed out; it produced no usable report. |
| `ios-open-space-restored` | Interrupted, partial | Valid Open trace through Home, catalog, detail, and show; episode navigation later failed. |
| `ios-open-resume-final` | Failed, partial | Valid single-process Open trace through video, MKV client demux, and audio play; the audio-options reveal later failed. |
| `tvos-quiet` | Failed, partial | Login, remote, and Home samples were captured before an XCTest Home Details readiness failure. |
| `tvos-space-restored` | Failed, partial | Valid single-process trace through Home, details, library focus, show/season, and direct play/pause. |
| `tvos-playback-restored` | Interrupted, partial | Direct playback controls, MKV, and music phases completed before Requests navigation failed. |
| `tvos-complete` | Aborted, unusable | Stopped at remote showcase due to ENOSPC; the trace is unusable. |
| Open pose | Ready | Verified after the host was unlocked; follow-up capture remains required. |
| Full `make check` | Failed, not green | Browser: 616 passed, 1 failed, 33 not run; a repaired focused watch-web check passed, but the full gate has not rerun. |
| `tvos-music-functional-r3` | Passed, functional only | One TV music workflow passed; it contains no performance profile. |
| `tvos-music-functional-r4` | Passed, functional only | Strengthened clock assertions passed; it contains no performance profile. |
| `ios-music-bar-functional-r2` | Passed, functional only | One actual Open music workflow passed; it contains no performance profile. |
| `ios-open-account-functional` | Passed, limited functional only | One Duo Account workflow passed without a profiler; allowances did not settle. |
| tvOS video general controls | Passed, functional only | One workflow passed in 177.448 seconds; it contains no performance profile. |
| tvOS speed and chapters | Passed, functional only | One workflow passed in 156.458 seconds; it contains no performance profile. |
| tvOS timing and quality r2 | Passed, functional only | One focused workflow passed in 163.943 seconds; it contains no performance profile. |

The earlier partial cohorts report exit code 65. The completed phases above are
usable measurements, but a complete Open capture, a complete usable tvOS runtime
tour, a profile of the new tvOS UI, and the full release gate remain pending.

`ios-closed-isolated` captured one app process throughout: the capture reported
one observed PID, no PID mismatch, no timeout, no interruption, and no reader
failure. Its XCTest run still ended with exit code 65 at the Books-to-Movies
transition, where the test helper swiped the wrong offscreen rail. That helper
has since been corrected. Account and editor plus the prose and comic windows
listed above occurred before that workflow failure and remain valid partial
measurements.

The isolated run uses the `LUMINA_UI_TESTING` compilation condition for fixture
namespace isolation while retaining Release optimization. It is a test-only
isolation mechanism, not a production optimization setting.

### Isolated Closed-window samples

The following are process samples from `ios-closed-isolated/report.json`. CPU
is expressed as a fraction of one core; footprint is the sampled app-process
footprint peak in MiB.

| Settled window | CPU | Wakeups/s | Footprint peak |
| --- | ---: | ---: | ---: |
| Account | 0.0049% | 5.82 | 68.17 MiB |
| Home editor | 0.0058% | 5.95 | 75.64 MiB |
| Prose catalog | 0.0008% | 3.44 | 84.13 MiB |
| Prose reader | 0.0022% | 2.64 | 89.58 MiB |
| Comic catalog | 0.0004% | 1.33 | 87.25 MiB |
| Comic reader | 0.0006% | 1.72 | 87.74 MiB |

### Closed-cycle evidence

`ios-closed-cycles` completed all five workflow cycles with Time Profiler. Its
cycle-end app-process footprints were **106.4, 132.9, 158.2, 188.9, and 215.6
MiB**. `ios-closed-lifecycle-r3` completed the same five cycles without Time
Profiler, with corresponding footprints of **95.2, 120.2, 147.5, 172.0, and
196.7 MiB**. These are observed growth points, not a leak classification.

The lifecycle run assigned unique tokens to each cycle and recorded model
deinitialization and bridge dismantling for each one. It also recorded repeated
watcher-restart attempts as deduplicated. That evidence argues against a
retained app model as the primary explanation for the cycle footprint pattern,
but it does not establish a clean leak result.

`vmmap-final` attributes 128.4 MiB to the HTTP cache and records a 25.7 MiB
direct-fixture payload per visit. This supports the cache/payload explanation
for a material part of the growth; it does not clear other allocations.

The retained `app.memgraph` leak analysis reports **78 small leaks totaling
3,376 bytes**, including `AVProxyKVOObserver` instances. This pass therefore
does not claim zero leaks or a green memory result. A direct-stream cache-header
trial and its assertions were reverted after it did not improve the later
measurement; the Home request-count contract remains valid. The reentrant-dismiss
teardown fix was validated in the cache-fix and layer cycles, but did not resolve
the cache behavior on its own.

### `AVPlayerLayer` Closed-cycle evidence

`ios-closed-layer` completed five workflow cycles after the iOS production
playback path moved to `AVPlayerLayer`. Its cycle-end app-process footprints
were **68.4, 68.2, 68.0, 67.8, and 85.2 MiB**. The fifth cycle had already
reached 85.5 MiB before the memory probe, so the fifth measurement must not be
attributed solely to the probe. The comparatively flat first four observations
also do not establish a leak classification.

In this capture, `vmmap-final` contains no `CoreMediaHTTPCache` row; the earlier
128.4 MiB cache attribution was an AVKit-era observation and is not present in
this layer-backed measurement. The retained memory-graph analysis instead
reports **56 unnamed leak blocks totaling 1,792 bytes**. It therefore remains
incorrect to claim zero leaks or a green memory result.

The cache-header trial plus teardown changes recorded **95.6, 120.9, 147.7,
173.4, and 198.4 MiB** across its five cycles, with no observed improvement
over the prior growth pattern. The ineffective backend cache-header change and
its trial assertions were reverted.

### tvOS quiet partial evidence

The production tvOS renderer and the latest focused tvOS tour Release build
passed. `tvos-quiet` nevertheless failed before the Home Details action:
XCTest waited for `isHittable` even though the screenshot and accessibility
output showed the target. The tour-only helper now waits for a visible, enabled
frame and then performs an actual remote focus/select action. `tvos-complete`
then aborted at remote showcase with exit 241 because of ENOSPC. Its
`reader_error` was correctly recorded and its Instruments trace ended with exit
-5, leaving that trace unusable. Neither run is counted as a passed tvOS tour.

The partial quiet capture recorded **35.9 MiB** at login (with a graph probe),
**96.9 MiB** at the remote phase, and **58.9 MiB** at Home, with tiny sampled
idle CPU. These isolated samples are not a completed tvOS workflow or a leak
classification.

That quiet configuration disables
`UITargetAppPerformanceAntipatternCheckerEnabled` and sets
`OS_ACTIVITY_MODE=disable`. The earlier AVKit and layer A/B captures used the
default checker setting. Results are therefore comparable only within their
respective harnesses and do not prove a hardware-framework leak.

### Later Open and tvOS partial tours

`ios-open-space-restored` was captured at the actual Open size of **951 × 669**.
Its valid trace covers Home, catalog, detail, and show windows. Episode navigation
then encountered an oversized season accessibility frame; XCTest raised an
exception and stalled teardown. Cleanup was manually interrupted (wrapper exit
130); its sampler report records the Xcode exit as 75 and the trace remains
explicitly partial. The season navigation link interaction and accessibility
traits, plus the helper's missing-frame guard, were fixed after this capture.

`ios-open-resume-final` ended with exit 65 but retained a valid single-process
trace through show/season, direct playback settings, client-demux MKV, and
music playback. It later tried to reveal audio listening options by swiping a
covered underlying detail rather than the visible audio-player stage. The helper
source was fixed, but that correction has not yet been remeasured.

`tvos-space-restored` ended with exit 65 after a valid single-process trace
(PID 71918; Instruments exit 0) through Home, feature detail, cast/related,
library focus sweep, show/season, and direct play/pause. Its paused-chrome test
used Down while the chrome was visible, which did not exercise the intended
behavior. The tour now hides chrome before Down and uses the settings button
while paused.

`tvos-playback-restored` completed the direct Down shortcut, video-panel tabs,
client-demux MKV, and music catalog/player/options/queue phases before Requests
navigation failed because the audio stack had not unwound. XCTest then produced
an exception diagnostic and cleanup was manually interrupted; it is not a full
pass. The source now contains a bounded unwind fix that has not yet been
remeasured.

## Home request correction

The pre-fix request observation recorded two initial requests for each Home
endpoint:

```text
/jellyfin/lumina/home/layout  2
/jellyfin/lumina/home/rows    2
```

The native correction now coalesces an overlapping `LuminaHomeLayout.load()` or
`LuminaHomeFeed.load()` while that object is already loading. iOS also waits for
the paired initial layout/feed task before its foreground or `onAppear` refresh
path can start another request. A later explicit refresh and an error retry are
still permitted after the current request completes.

`LuminaHomeLayoutTests.testOverlappingHomeLoadsCoalesceToOneRequestPerEndpoint`
uses a delayed in-process URL protocol and asserts one request for layout and
one for rows when two callers overlap. The focused three-test suite passed. The disposable-server request logs also confirm the same change during the
sign-in through settled-Home window: two GETs per endpoint in `ios-baseline-r2`,
and one per endpoint in `ios-optimized`. The original request-count assertion
fails on the baseline capture and passes on the optimized capture.

## Completed validation and instrumentation

- The latest full Core run completed with **121 tests, 5 skipped, 0 failures**
  in **104 seconds**. The five skips were fixture-dependent native-server tests
  without a configured fixture, rather than failures of the portable Core suite.
- The partial iOS cohorts include periodic process CPU, wakeup, footprint,
  resident-memory, and host-load samples. Their reports retain the exact phase
  boundaries and sample counts listed above.
- The optimized and playback cohorts include Time Profiler exports. Their
  potential-hang lanes reported two and three events respectively. These are
  observations for follow-up, not an attribution to an app source change.
- The iOS production Release build passed after the production playback paths
  were moved to `AVPlayerLayer`.
- The production tvOS renderer and the latest focused tvOS tour Release build
  passed. This build evidence is separate from a completed tvOS runtime tour.
- `make-check-music.log` records **5,301** passing backend tests with **11**
  skips, **305** passing frontend test files / **2,660** tests, and passing
  frontend type-check, build, and style checks. Its browser lane then failed
  with **616** passed, one failed, and 33 not run because the watch-web dark-1536
  case lacked a `POST /api/preview` mock. The fixture was repaired in
  `frontend/e2e/v1_a11y_sweep.spec.ts`; its four adjacent desktop/phone,
  light/dark cases passed a focused recheck in **10.4 seconds**. The full gate
  is not green until it reruns and records a clean result.
- `tvos-music-functional-r3` passed its one functional workflow in **193
  seconds**; its log and XCTest result are rooted in the same run. It verified
  inline-row play, next, pause with bar hide, remote resume, artist albums and
  Back, an expanded-player pause retained after the stable `VStack` presenter
  fix, and Stop clearing the bar. This is functional evidence only: the busy
  host did not produce a performance profile.
- `ios-music-bar-functional-r2` passed its one actual Open music workflow in
  **111.119 seconds**; its log and XCTest result are rooted in the same run. The
  repaired helper navigated Library to Music and verified a full-player pause
  retains its stage, background time advances, Video and Audio browsing retain
  the track and progress, and Stop dismisses and clears the bar. This is
  functional evidence only, with no performance profile.
- `tvos-music-functional-r4` passed its strengthened clock assertions in
  **192.446 seconds**; its log and XCTest result are rooted in the same
  production run as r3. This is functional evidence only, with no performance
  profile.
- `ios-open-account-functional` passed its Duo Account workflow in **220.381
  seconds**; its log and XCTest result are rooted in the same run. The allowances
  phase explicitly emitted a skipped `allowances did not settle` result, so this
  is not full Account qualification or performance evidence.
- The combined tvOS video-settings run initially had three cases; general
  controls passed in **177.448 seconds** and speed/chapters passed in **156.458
  seconds**, while only timing/quality failed. The focused timing/quality r2
  then passed in **163.943 seconds**. The original failure reflected timing
  before 720p server-HLS conversion: HLS subtitle selection works, but timing
  is unavailable and asserted in that capability state. No production behavior
  was changed to make this test pass. These are functional results only, with
  no performance profile.

## Current implementation scope

The production iOS and tvOS playback paths share `AVPlayerLayer`. On tvOS, a
manual public preferred-display-criteria window and item lease replace the
automatic view-controller feature path while preserving the display-matching
intent. The tours include an actual client-demux MKV play/pause/settings/close
phase with 1080p MP4 and MKV fixtures, but no complete current tvOS tour has
validated all of that scope. The new tvOS music design and control-bar auto-hide
flow has functional coverage, including strengthened clock assertions, but
still requires a fresh performance cohort.

## Limits on conclusions

These captures ran on a simulator while the host was under substantial and
variable load. Host-load readings varied materially between windows and cohorts;
the simulator's resident memory also includes simulator/runtime effects. The
numbers therefore support finding repeatable request behavior and choosing
follow-up investigations, but do not establish device CPU, memory, decoder, or
battery budgets.

`workflow_ms` is XCTest automation elapsed time, not user-perceived latency.
The sampled app-process CPU excludes video decoder/GPU work and system-service
work, so it must not be used as a whole-playback CPU budget.

Animation-hitch data is not available in the recorded traces: the requested
Animation Hitches instrument was unavailable. SwiftUI and SwiftUI-cause lanes
were also unavailable; the trace metadata says those require an appropriate
SwiftUI template on a real device. Do not claim hitch clearance, view-update
causes, or real-device playback performance from this pass. In particular,
hitch measurement is unsupported here, not a zero-hitch result.

The simulator pass also cannot verify display refresh matching, HDMI output,
HDR behavior, or other real-display presentation criteria. Those require
physical-hardware capture and remain outside the conclusions of this document.

## Artifact retention

Disk pressure required removal of generated binary captures and superseded
partial XCTest results after retaining the XCTest summaries, accessibility text,
reports, and traces needed for this record. `tvos-complete` encountered ENOSPC
at remote showcase and its unusable trace was not accepted as evidence. The user
authorized removal of three old shut-down Lumina review simulators and old
DerivedData, while active simulators, current-task products/evidence, and
release archives were retained. The host later had more than 100 GiB free, but
other cleanup also occurred, so that capacity must not be attributed entirely to
these removals. Available disk space is variable and is not itself a performance
measurement.

## Next measurements

1. Retain the completed tvOS video-settings functional artifacts and profile
   those controls separately; functional passes do not substitute for profiling.
2. Re-run the repaired tvOS Requests unwind and paused-settings behavior,
   including the 1080p MP4 and client-demux MKV playback phase.
3. Profile the functionally validated tvOS music design and control-bar auto-hide.
4. A single uninterrupted green `make check` remains unrecorded; the final
   merge qualification below composes passing evidence for all selected contracts.
5. Repeat a representative playback path on hardware with an available
   hitch/SwiftUI-capable Instruments template before drawing responsiveness or
   decoder, refresh-matching, or HDR conclusions.

## Final merge qualification, 2026-10-23

Final merge qualification ran `make check` with four backend/browser workers.
Backend passed 5,301 tests with 11 skips; frontend passed 2,660 tests across
305 files, plus typecheck, production build, style, license and bundle checks.
The browser run passed 615 of 650 selected cases; two layout fixtures raced the
1.5-second episode's autoplay, preventing 33 dependent cases from running.
The fixture now disables autoplay for those layout assertions while retaining
separate autoplay coverage. Both repaired cases passed, and the exact remaining
33 cases plus setup passed (34/34), including WebKit playback and both movie-wall
performance budgets. Identity-based composition accounts for all 650 contracts
without gaps. This is composed passing evidence, not a single green `make check`
invocation. Logs: `/private/tmp/lumina-merge-make-check-final-r2-20261023.log`,
`/private/tmp/lumina-watch-quality-focused-20261023.log`, and
`/private/tmp/lumina-merge-browser-remainder-20261023/`; composition record:
`/private/tmp/lumina-composed-browser-qualification.json`.

These browser performance passes do not qualify native device performance.
