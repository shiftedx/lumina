# Local conversion under shaped loopback network

Status: provisional measurements on 2026-10-17 in the implementation working tree based on revision `a4e01da`. All nine cold trials passed, but a later process audit found an unrelated checkout's backend test suite running throughout this window. Repeat in a verified quiet window before treating these timings as acceptance evidence.

This is an evidence collection experiment for the existing local H.264/AAC remux path. It does not change encoder settings, playback decisions, quality selection, or adaptive behavior. The synthetic `Perf Remux` fixture is the existing 1080p H.264/AAC Matroska fixture produced by `scripts/perf/playperf_server.py`, whose video encoder target is 8 Mbit/s. The ordinary verification boundary remains [the local verification matrix](../VERIFICATION.md); this opt-in experiment stays outside it.

## Controlled cases

Each case runs three independent browser contexts. A context starts with the saved owner cookie but an empty browser cache. Before every trial, the harness clears the fixture's saved position and writes the member's existing `ui_prefs.playback_max_height` preference through `/api/settings/me`.

| Case | CDP network | Member starting ceiling | Trials | Result |
| --- | --- | --- | --- | --- |
| Unshaped Best available reference | no latency or throughput cap | `null` (Best available) | 3 | Passed; 1080p remux |
| Shaped Best available baseline | 50 ms latency; 2.5 Mbit/s download (312,500 bytes/s); no upload cap; zero packet loss/reordering | `null` | 3 | Passed; 1080p remux |
| Shaped starting 480p | 50 ms latency; 2.5 Mbit/s download (312,500 bytes/s); no upload cap; zero packet loss/reordering | `480` | 3 | Passed; 480p software-video transcode |

Chrome DevTools Protocol's `Network.emulateNetworkConditions` receives `packetLoss: 0`, `packetQueueLength: 0`, and `packetReordering: false`. The packet-loss setting is CDP's WebRTC loss knob; this harness otherwise injects no loss or packet reordering.

## Recorded per trial

- Cold-session click-to-first-decoded-frame time: page-clock time from the existing real UI Play action to `requestVideoFrameCallback`.
- A 20-second wall-clock observation beginning at that decoded frame: media-clock advance, stall count, and total stall duration. A stall begins on the first `waiting` or `stalled` event and ends at the next `playing` or `timeupdate`; a still-open stall ends with the observation.
- Decoded height at first frame and observation end.
- The persisted ceiling, server playback mode, source height, session mode/start, requested start position, and default/requested audio selection. The fixture has one default audio stream, so this records preservation rather than comparing alternate tracks.

The harness asserts the expected 1080p or 480p decoded height and a zero requested start after clearing progress. It deliberately asserts no latency threshold.

## Provisional results under concurrent host load

Every trial used a fresh Chrome context, the fixture's default audio stream (index `1`), and a requested position of zero. The server reported the source as 1080p. The 480p case selected `transcode` with `video_sw`; the two Best-available cases selected `remux`. No CPU or encoder-throughput telemetry was collected. An unrelated test suite and `make check` ran during this window, so these timing values are exploratory observations only.

| Case | Cold click to first decoded frame (ms), trials 1/2/3 | Media-clock advance in 20 s (s), trials 1/2/3 | Stalls: count / total duration (ms), trials 1/2/3 | Decoded height |
| --- | ---: | ---: | ---: | --- |
| Unshaped Best available | 612 / 245 / 692 | 20.005 / 20.021 / 20.022 | 0 / 0; 0 / 0; 0 / 0 | 1080p throughout |
| Shaped Best available | 16,393 / 16,414 / 18,854 | 7.541 / 7.661 / 7.520 | 2 / 12,466; 2 / 12,372; 2 / 12,485 | 1080p throughout |
| Shaped starting 480p | 3,178 / 2,563 / 5,249 | 17.890 / 19.188 / 20.096 | 1 / 264; 1 / 263; 0 / 0 | 480p throughout |

For this fixture and loopback host, the shaped 480p case had a median 3,178 ms first decoded frame, 19.188 s media-clock advance, and 263 ms stalled time. The shaped Best-available baseline had medians of 16,414 ms, 7.541 s, and 12,466 ms respectively. The quiet-window repeat must replace these values before they are used for a quality-selection decision. Even then, the experiment will not establish a rule for automatic quality switching: it has one source, one link model, three trials per condition, loopback transport, and no hardware or encoder-capacity measurement.

## Running the experiment

Run only after a verified quiet-host window. The command takes the existing machine-wide performance lock, uses loopback port 5913 and a temporary app root, and enables the otherwise inert config. The lock coordinates this benchmark command but does not by itself prove that no unrelated host work is active:

```sh
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 sh -c 'cd frontend && LUMINA_RUN_LOCAL_NETWORK_MEASUREMENTS=1 npx playwright test -c playwright.local-network.config.ts --project=local-network'
```

The run emits one `LOCAL-CONVERSION-NETWORK` JSON line for each case. It uses Chrome (`channel: 'chrome'`) because the bundled Playwright Chromium lacks the H.264 decoder needed for this fixture.

This measures browser-to-loopback transfer under CDP conditions. It does not qualify household Wi-Fi, external providers, hardware decode, or private media. Results should be compared by scenario and trial distribution, then used to decide whether starting-quality or adaptive behavior merits a separate change.


## Comprehensive-audit repeat

The user subsequently authorized continuing under concurrent workloads without another review. The nine trials were repeated under the shared lock with one-second process-tree/CPU sampling; all correctness checks passed, and external contention was observed. The repeat's detailed results and limitations are in [the comprehensive audit](../performance/comprehensive-audit-2026-10-17.md#starting-video-quality-on-constrained-bandwidth-completed-contended), with raw evidence under `output/performance-audit/network-native/`. Median first-frame times were 279 ms unshaped, 16,589 ms shaped at 1080p, and 1,974 ms shaped at 480p. The latter had no stalls in any 20-second observation; the shaped 1080p trials stalled for 12.1–13.1 seconds each. This strengthens the case for a separate starting-quality investigation while remaining a contended, single-source experiment; it does not qualify an automatic adaptation rule.
