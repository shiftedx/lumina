# Household resources implementation record

Acceptance contract: [handoff](household-resources-handoff.md). Shared API and policy semantics: [ADR 0024](../adr/0024-household-resource-budgets.md).

## Baseline

- Revision `7bf92eaf31a2f2e864b5879130ecce4f41e7ca20`, branch `codex/household-resources`, clean worktree.
- Baseline `mise exec -- make check` started before edits; output captured in `/tmp/lumina-household-baseline.log`. Bootstrap, typecheck and build passed; backend result was 4,951 passed / 11 skipped and one source-inspection failure because ongoing edits moved `main.py` lines after import. This is a baseline-run artifact, not classified as a pre-existing repository failure. Final qualification uses a fresh process after integration.
- Existing capacities: download `AppSettings.concurrency`, AI `ai_max_concurrency`, video `max_playback_sessions`; preserved.
- Foundation contract checks: `test_resource_policy.py` and existing `test_rate_limit.py`, 16 passed before adding shared-IP regression.

## Exclusive packet ownership

| Owner | Files/responsibility | Status |
| --- | --- | --- |
| PM | models, db, schemas, main, security, connected_apps identity, resource_policy, resources router, ADR/operator docs, integration | Complete; final qualification passed |
| Playback | playback_resources, local_playback router, playback_decision, local_playback_sessions, jellyfin_playback/integration/router, activity, playback tests | Implemented; focused suites passed |
| Delivery | bandwidth, http_bandwidth, media_response, bandwidth tests/measurement | Implemented; focused suites passed |
| Work | Download/outbox/live-recording admission and dispatch, AI inventory | Implemented; 133 focused and 5 new concurrency/workflow tests passed |
| UI | Settings/member controls, api/types, frontend journeys | Implemented; typecheck, 2,477 frontend tests and desktop/320 px browser journeys passed |

## Route coverage inventory

Every authenticated remote response body shares household/member pacing, including attachments and cached responses. Authentication and capability-only music casting attach a verified member before response headers. Browser/device session ownership uses verified credential provenance; retries preserve the logical allocation.

| Surface | Identity | Quality/admission | Body |
| --- | --- | --- | --- |
| Web library media GET/HEAD | authenticated snapshot | protected direct admission; forced-conversion refusal | MediaFileResponse, ranges/multipart |
| Web options/conversion/HLS | current member | shared policy/reservations | pinned files + manifest Response |
| Jellyfin direct/download/HLS | live token or verified PlaybackInfo grant | shared policy; downloads paced only | MediaFileResponse + pinned HLS |
| Remote content/renditions/HLS/DASH | current member + owner-bound stream id | capped provider paths refuse | ClosingStreamingResponse |
| HLS/live relays | current member + owner-bound stream id | capped relay/live paths refuse | ClosingStreamingResponse |
| Music files | current member | actual outgoing-file bitrate guard | MediaFileResponse |
| Sonos capability cast | verified ticket owner | verified ticket owner + outgoing-file guard | MediaFileResponse |
| Books files/pages, profile export, backups | authenticated member | attachment/body pacing | Response/FileResponse |
| Public artwork/showcase | public signature/no member identity | outside member upload policy | existing response |

## Qualification evidence

- Foundation/auth/classification/ranges: focused policy/legacy regressions passed (27 in the combined latest run).
- AI: cross-kind queue/running limits, FIFO/round-robin, cancellation, restart, stale-result replacement and synchronous draft admission covered; 92-test focused audit passed before the extra stale-output regression (7 focused tests passed afterward).
- Playback: final focused suite **134 passed**, including stable preview allocation, direct refresh, both overlapping-failure orderings and bounded retry/seek history.
- Acquisitions: 133-test focused suite, then 120-test focused concurrency slice with five new dedicated workflow tests, passed.
- `mise exec -- make e2e-browsers`: **4 passed** on the final playback source, including real WebKit remux/transcode decode and a seek beyond the converted range (`/tmp/lumina-resource-browsers-final.log`).
- `mise exec -- make check` exercised every gate: bootstrap checks, typecheck/build, backend **5,010 passed / 11 skipped**, frontend **2,477 passed**, real-stack browser/performance **47 passed / 4 skipped**. The first complete invocation stopped in the mocked stage on three stale member-page assertions (page count, owner copy and registry count).
- Those browser contracts and shared resource fixtures were repaired; the member resource panel now participates in the existing help-row registry without nesting forms. Fresh `mise exec -- make frontend-typecheck frontend-build frontend-test` passed, including all **2,477** frontend tests (`/tmp/lumina-frontend-gate-final.log`).
- Fresh `mise exec -- make e2e-mocked` passed **592 / 2 skipped** (`/tmp/lumina-household-browser-final.log`). The three repaired checks, owner/member resource journeys, accessibility/registry/TV targets and full visual catalogue all passed. All gate stages are green; backend and real-stack results were retained because the final remediation changed frontend help-row presentation, test fixtures and assertions only.
- `git diff --check`: clean. No unresolved implementation or qualification blocker.


### Actual TLS proxy delivery

`mise exec -- backend/.venv/bin/python scripts/qualify_household_resources.py` launched an isolated Caddy TLS proxy (`flush_interval -1`, no cache), one loopback Lumina process with Uvicorn proxy rewriting disabled, invented member credentials and synthetic files. Settings: household 512,000 bit/s, each member 384,000 bit/s, persistent burst 32,768 bytes. The qualifier checked every client sample against household/member envelopes and independent ASGI server-send samples against the household envelope. TLS/IP overhead is excluded. No observed excess required a tolerance (the harness permits 1,024 bytes for observation ordering).

| Window | Transport/parallelism | Bytes (server and client) | Duration | Maximum envelope excess |
| --- | --- | ---: | ---: | ---: |
| Short | four concurrent Jellyfin attachment ranges, two members | 262,144 | 3.614 s | 0 bytes |
| Sustained | four concurrent ranges, two members | 2,097,152 | 33.195 s | 0 bytes |
| Full bodies | two concurrent full attachments, two members | 4,194,304 | 65.707 s | 0 bytes |

The same real stack then enabled a 20 Mbps household / 8 Mbps stream policy. A compatible synthetic H.264/AAC file probed at 99,242,474 bit/s advertised `transcode`, refused a forged original GET with 409, started conversion, served a real 108,403-byte HLS segment and stopped cleanly. Byte pacing and quality selection are separate verified paths.

Measurement artifacts: `/tmp/lumina-proxy-qualification-final.log`; isolated qualifier result and raw send log are under the temporary directory printed by that command. The repeatable harness retains artifacts for diagnosis; it never touches an existing deployment. GUI qualification: `frontend/e2e/household-resources.spec.ts` passed both owner/member journeys, including fractional decimal Mbps and reset-to-inherit. Visually reviewed desktop and 320 px screenshots are `output/playwright/household-resources/owner-desktop.png` and `output/playwright/household-resources/member-phone-320.png`. Final check results follow below.

### Reviewed boundaries

The supported topology remains one Lumina server process. Public signed artwork/showcase bodies have no member identity and remain outside member pacing. Remote provider/live relay playback is explicitly refused under a cap; supported local-library conversion is the bandwidth-aware alternative. Zero-copy response extensions are removed before authentication to ensure response bodies cannot bypass accounting, including requests overlapping policy changes. The supported Uvicorn topology does not advertise these extensions.
