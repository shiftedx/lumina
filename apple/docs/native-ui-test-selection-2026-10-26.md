# Native UI test selection, 2026-10-26

The native UI target retired fourteen platform cases after an evidence review
found no recorded significant user-visible regression catch for them. The
review covered source history, saved native run evidence, and the validation
records associated with changes #147, #150, and #157. This is **moderate
confidence** only: absence of recorded evidence does not prove that an
unrecorded regression was never caught.

Retired cases covered Account performance sampling; Home hero motion; album
artwork color; Watchlist episode hero; Requests detail rail layout; Library,
Home carousel, and empty-Watchlist edges; Home settings editor layout;
title-logo fallback layout; and media-details layout. Their latest passing
durations summed to 1,142.925 seconds of test time, not a wall-time forecast.

The retained public contracts are intentionally narrower:

- `NativeMusicAdvancedControlsPausedRestoreAndQueueConflictTests` retains the
  tvOS queue remote-Select regression caught in #139 / `156f33b`, along with
  paused restore and queue-conflict coverage.
- `NativeNativePlaybackReportsResumeAndConversionProgressTests` retains the
  playback-resume regression caught from #69 through #75 / `0bab243`.
- `NativePerformanceTourTests` retains the full Requests → Account → Books
  prose/comic reader-and-restore workflow, which caught the Books-load
  cancellation regression in the R71 run.

The performance tour also removed its unproven five-cycle
`destination_detail_back` suffix. Its history showed no product catch, and
the earlier measurements did not prove a leak. The expected native inventory
changes from 84 to 70 platform cases: iPad 43 to 33, tvOS 30 to 27, iPhone 7
unchanged, and Duo 4 to 3. A fresh compiled discovery is required to establish
the new count; historical records are retained rather than rewritten.
