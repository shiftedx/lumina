# Music Home fixture and baseline

[#343](https://github.com/shiftedx/lumina/issues/343) · [Workflow contract](music-home-and-queue-workflows.md)

## Run the shared server

From the repository root, install dependencies and build the production web UI. Run the proof server in its own terminal. Use a private fixture path. The server binds to loopback, provisions a disposable database and removes the file when it stops.

```sh
mise exec -- make bootstrap frontend-build
export LUMINA_NATIVE_FIXTURE="$(mktemp -d)/Fixture.json"
LUMINA_MUSIC_HOME_FIXTURE=1 backend/.venv/bin/python -u apple/scripts/proof_server.py
```

- Shared timing album **Signals from a Quiet Observatory**, with two 90-second synthetic AAC tracks. Browser timings use the second member so the verification queue stays available under the owner.
- Owner queue contains two occurrences of the same track, an unresolved **Wait** entry, and a playable track past the Wait boundary. The proof server checks that the resolved projection stops at the boundary.
- The fixture includes **Two Signal Discs**, an untagged MP3, and a member-private album. The proof server checks disc order, fallback metadata and both members’ visibility.
- Clients create offline copies with the normal Download or Save Album action. The fixture contains source media and authorization. Client storage starts empty.
- The generated JSON contains disposable credentials and opaque item IDs. Keep it local. The timing report includes sample data and environment metadata only.

## Web run

```sh
mise exec -- node frontend/scripts/benchmark-music-home.mjs \
  "$LUMINA_NATIVE_FIXTURE" output/music-home-web-baseline.json 5
```

Use installed Google Chrome and a fresh browser context per sample, with the same running server throughout. Browse timing starts with the Albums document navigation and ends when the fixture album is actionable. Playback timings start on the Play or manual Next click and end at immediate Web Audio scheduling or an HTML media playing event with the expected track visible. The last phone run saves the album, reloads the standalone listening page offline and starts a saved track. Scheduling and media events are client proxies for audio onset.

## Recorded results

Source `60a5763`, November 19, 2026. Loopback fixture, Apple M4 Max host, macOS 27.2.1. Values are medians in milliseconds.

| Surface | Samples | Browse ready | First audio | Manual Next |
| --- | ---: | ---: | ---: | ---: |
| Chrome 157 desktop, 1440 × 900 | 5 | 877.9 | 169.3 | 96.3 |
| Chrome 157 phone viewport, 390 × 844 | 5 | 827.4 | 153.9 | 84.6 |

[Raw web samples](evidence/music-home/web.json). All Chrome playback markers used Web Audio scheduling. Keyboard Enter opened the fixture album at both viewport sizes. Save Album → standalone offline reload → playback passed on Chrome. The bundled Chromium build saved the AAC album but its offline decoder rejected the source. Use Chrome for this AAC benchmark and retain codec-specific offline qualification for the release.

## Regression budgets

Use the same host profile, route, fixture and metric definition for each before/after comparison. Rerun each surface after implementation. Use the median for the first checkpoint and capture at least 20 samples per cohort when qualifying p95 for beta. Preserve raw samples and separate buffered and streaming paths.

| Metric | Maximum median regression |
| --- | --- |
| Browse or Home ready | Greater of 15% or 150 ms |
| First audio | Greater of 15% or 100 ms for web scheduling. Greater of 15% or 250 ms for native UI clock proxies. |
| Next track | Greater of 15% or 100 ms for web scheduling. Greater of 15% or 250 ms for native UI clock proxies. |

A matched rerun that exceeds a budget requires a breakdown before beta. Natural audio continuity and physical route behavior have separate device gates.
