# Remote streaming qualification — 2026-10-11

## Cause and change

DASH SegmentBase fetches index and media ranges at the same URL. In dash.js 5.2.0, its Resource Timing lookup reused the first matching URL entry. Later media transfers therefore inherited the tiny index byte count and its timing. Index probes also appeared as MediaSegment requests without a media duration. The resulting estimate could remain around 0.2 Mbps despite substantial buffered video and recovered bandwidth.

The player now installs a narrow ThroughputModel extension. Successful media transfers use their own progress byte count and response/finish timestamps; durationless index probes, failed requests and invalid measurements are excluded. Original HTTP metrics remain intact. Normal throughput adaptation replaces the bespoke climb loop. Existing buffer, abandonment, dropped-frame and switch-history rules stay active; Auto starts at the highest compatible rendition and explicit DASH pins remain explicit.

HLS quality pins now retain the selected height across a local retry or same-session playlist refresh, even when indexes change. A missing height fails visibly instead of selecting a different quality, with an explicit Switch to Auto action. Returning to Auto clears the pin.

## Controlled recovery

[Raw-sample summaries and hashes](measurements/remote-recovery-2026-10-11.json) describe a balanced C/A/A/C replay using real Chrome, real dash.js and the real backend with a synthetic account. Both arms received identical H.264/AAC bytes, a 75 ms index-body delay, and an upstream path shaped to 3 Mbps for 15 seconds then 100 Mbps. Each trial used a fresh server process. The authenticated application shell was warmed by sign-in, with player libraries cold; these are source-open measurements, not cold full-page navigation measurements. No correctness suites or builds ran alongside timings.

| Trial | First decoded picture | Quality at 45 seconds | First decoded 1080p | Stalls |
| --- | ---: | ---: | ---: | ---: |
| Control 1 | 2,207 ms | 240p | Never observed | 0 |
| Candidate 1 | 2,217 ms | 1080p | 16,249 ms | 0 |
| Candidate 2 | 2,246 ms | 1080p | 16,312 ms | 0 |
| Control 2 | 2,318 ms | 240p | Never observed | 0 |

The recovery assertion failed on the old player and passed on the candidate. The candidate returned to 1080p roughly 1.3 seconds after nominal bandwidth restoration. These two samples establish the recovery defect and its correction, not a p95 claim or a provider-wide startup speedup.

Native macOS Zen 1.23.2b reproduced the quality result: control stayed at 240p and candidate reached 1080p. Its frame callbacks were sparse and delayed, so they are not used for precise startup or transition comparisons. Windows Zen and Dia were not measured directly; this change needs no extension.

## Provider evidence and rejected experiments

Isolated native amd64 server trials used the household network path with synthetic state. The final 60-second trials began at 2160p in 2.29 seconds on YouTube, 1080p in 1.57 seconds on Twitch, and 1080p in 0.81 seconds on Kick. All three retained those heights without stalls. Video counters recorded 4/3,600 dropped frames on YouTube, 49/3,601 on Twitch, and 0/3,602 on Kick. An earlier YouTube trial legitimately adapted down with zero stalls or dropped frames. YouTube transfer rates remained variable enough to require legitimate quality reductions; preserving original bytes cannot guarantee 4K over every upstream path.

Smaller index reads, query-based ranges, parallel range splitting, early player-library loading, TLS-context reuse alone, constructing the guarded index handler without the full extractor client, and an alternate YouTube HLS route did not show a qualified end-to-end gain. They were rejected. Raw trials, including slow outliers and setup failures, are retained privately; signed URLs, authentication and household addresses are excluded from this report.

Coverage includes YouTube VOD, Twitch live/VOD, and Kick live. The selected YouTube live and Kick VOD examples returned preview errors; the selected Kick clip resolved but its HLS master failed closed after a refresh. Those failed examples remain in the private inventory and are not classified as generally unsupported or claimed as fixed/playable by this performance change.

There is no backend policy or media-byte change in this fix. Existing public-destination checks, owner-bound sessions, resource limits, capability selection and durable resume behavior remain covered by the correctness gate.

## Correctness gate

`mise exec -- make check` passed on the final application changes: 4,516 backend tests (11 skipped), 2,257 frontend tests across 272 files, 46 real-stack browser tests (4 skipped), and 485 mocked browser tests (1 skipped). Type checking, production build, asset budgets, packaging/version alignment and bootstrap checks passed. Regression tests were observed failing before the estimator, normal adaptation and HLS pin/recovery changes. The controlled recovery assertion failed for control and passed for candidate.
