# Local verification matrix

This matrix maps a change's risk to the verification that covers it. All checks run locally.

## Before opening a pull request

Run `mise exec -- make check` before opening a pull request. It covers bootstrap,
backend and Vitest contracts, TypeScript checking (including `frontend/e2e`),
the production build, then the full parallel browser tier. For UI changes,
`mise exec -- make e2e-smoke` is the fast merge bar. Before release, qualify
both browser tiers twice; commands, budgets, isolation and measured evidence are
in [frontend readiness](FRONTEND_READINESS.md).

Backend changes have a 50-contract smoke tier and a full parallel pytest tier.
`mise exec -- make backend-smoke` is the fast backend merge bar; `make backend-test`
runs full with four isolated workers. Qualify both twice before release; commands,
budgets, retained prerequisite skips and audit evidence are in
[backend readiness](BACKEND_READINESS.md).

The full backend tier also requires the generated M2TS corpus. Generate it
outside Git using FFmpeg 8.0.1 with x265 4.1 and `dovi_tool` 2.3.4. This
combination passed the exact MEL packet-offset checks in the
[Release 1 corpus preflight](https://github.com/shiftedx/lumina/actions/runs/44818190895/job/136571887799).

```bash
mise exec -- python3 apple/scripts/generate_dv_m2ts_fixtures.py \
  --dovi-tool /path/to/dovi_tool --output /tmp/lumina-m2ts-fixtures \
  --positives-only --cold-seek-duration-seconds 96 \
  --cold-seek-variable-gop-pattern 288,24,72
export LUMINA_DV_M2TS_FIXTURES=/tmp/lumina-m2ts-fixtures
mise exec -- make check
```

The full tier rejects skips caused by an unconfigured corpus; do not add them
to its allowed skips. Regenerate the corpus if its temporary directory is removed.

For Linux, use the [reference test environment](#linux-test-environment) below.
On macOS, install all pinned engines once with
`cd frontend && mise exec -- npx playwright install chromium firefox webkit`.
The tier runner allocates fresh loopback ports and private fixture/output roots;
see its printed artifact path and pass/fail times. Legacy `make e2e-mocked` and
`make e2e-realstack` remain available for focused work.

The accessibility checks run axe with the WCAG 2.0 to 2.2 A and AA rule tags and fail on moderate, serious or critical violations. They audit success, error and loading states, and the desktop and 320px journeys also cover keyboard order, visible focus, Escape and focus containment in dialogs and drawers. They are regression checks, not a WCAG conformance claim; screen readers, cognitive usability and color and motion still need manual review.

| Risk or behavior | Command | What it proves |
| --- | --- | --- |
| Bootstrap behavior | `mise exec -- make bootstrap-test` | Platform-specific virtual-environment paths and the complete locked dependency command set. |
| Backend/API/domain behavior | `mise exec -- make backend-test` | The Python unit, service, persistence, API, security, and contract suites. |
| Frontend state and components | `mise exec -- make frontend-test` | Deterministic Vitest module and component behavior; browser end-to-end tests are deliberately excluded. |
| Type contracts | `mise exec -- make frontend-typecheck` | TypeScript compilation of `frontend/src` and `frontend/e2e` without emitting files. |
| Web production output | `mise exec -- make frontend-build` | Type checking plus a Vite production bundle in `frontend/dist/`. |
| Mocked-API browser specs, including responsive and accessibility resilience | `mise exec -- make e2e-mocked` | Every `frontend/e2e` spec outside `realstack/` against a mocked API. The resilience specs cover mocked desktop and native 320px responsive behavior; keyboard order, activation, focus indication and containment; plus tagged axe checks on success, error, and loading states at a moderate-or-higher threshold. Not a conformance certification. |
| Repository hygiene | `mise exec -- make repo-hygiene` | Tracked Markdown links, portable paths, likely secrets, and source line limits. |
| Complete repository gate | `mise exec -- make check` | Every row above, plus the combined full browser tier (`make e2e-full`). Required before opening a pull request. |
| Real-browser remux/transcode decode | `cd frontend && npx playwright install webkit` once, then `mise exec -- make e2e-browsers` | WebKit actually decodes the backend's H.264/AAC HLS output for a remux and a transcode session (`currentTime` advances), and that a seek past the converted range restarts the conversion and lands near the target (#137). Included in the full tier and `make check`; install WebKit before running. Required for changes to local remux/transcode playback (`backend/app/services/local_playback_sessions.py`, `frontend/src/localPlayer.tsx`). |

New source files have a 1,500-line limit. Two existing composition roots have fixed ceilings of 3,681 lines in [the backend app](../backend/app/main.py) and 1,895 lines in [the web app](../frontend/src/LuminaApp.tsx).

## Linux test environment

Use [the test-gate Dockerfile](../docker/test-gate.Dockerfile) to reproduce the
Linux parser, media and browser checks in one disposable environment. Build
and run on the host's native architecture with Docker BuildKit. The container needs a Linux kernel
with Landlock ABI 3 or later (Linux 6.2 or later with Landlock enabled). Run
with Docker's default security profile and the image's unprivileged user.
The repository's default parser sandbox is Landlock.

The image combines the tools pinned in `.tool-versions` with the Noble
Playwright 1.55.1 browsers, libseccomp 2.6.0, util-linux 2.41.2 `setpriv`,
libarchive 3.8.10 and Jellyfin FFmpeg 8.1.3 for Noble.
Python bundles SQLite 3.53.1. The base images and direct tool downloads are
checksum-pinned, and OS dependencies come from Noble's APT repositories.
Stock Ubuntu 24.04 libseccomp 2.5.5 cannot resolve both `setxattrat` and
`removexattrat`. The parser fails closed before decoding PDF, CBR or photo
input. The supplied `setpriv` supports both `--seccomp-filter` and
`--landlock-rule`. The installed libarchive supplies RAR5 decoding, and
Jellyfin FFmpeg provides the software encoders and metadata used in media
assertions. Use the matching FFprobe bundled with it.
[Original Linux run](https://github.com/shiftedx/lumina/issues/339).

From the repository root, export the exact committed source to the container's
Linux filesystem. This also works from a Git worktree and avoids using
host-built dependencies or host-mount permission semantics. The container
already supplies the pinned Python and Node, so run `make` directly there.
Set `LUMINA_DV_M2TS_FIXTURES` to the absolute path of the corpus generated
above and mount it read-only. Use Jellyfin FFmpeg 8.1.3 for the Linux tests.
The same binary produces different legacy MEL packet offsets during corpus
generation.

```bash
docker build -f docker/test-gate.Dockerfile -t lumina-test-gate .
git archive HEAD | docker run --name lumina-linux-test-gate --init -i \
  --shm-size=1g --tmpfs /tmp:rw,exec \
  --mount "type=bind,src=${LUMINA_DV_M2TS_FIXTURES:?Set the generated corpus path},dst=/fixtures/m2ts,readonly" \
  -e LUMINA_DV_M2TS_FIXTURES=/fixtures/m2ts lumina-test-gate bash -euc '
    tar -xf -
    git init -q
    git add --force --all
    make bootstrap
    make -j1 check \
      BACKEND_OUTPUT=/work/output/linux-backend E2E_OUTPUT=/work/output/linux-browser
  '
mkdir -p output
reports=$(mktemp -d output/linux-test-gate.XXXXXX)
docker cp lumina-linux-test-gate:/work/output/. "$reports" &&
  docker rm lumina-linux-test-gate
```

For parser and archive diagnosis after bootstrap, run
`(cd backend && .venv/bin/python -m pytest -q tests/test_parser_boundary.py tests/test_book_native.py tests/test_photos.py)`.
For real browser playback, run `make e2e-browsers`. For the real-server Books
and Photos browser journeys, build with `make frontend-build` and run
`(cd frontend && npx playwright test -c playwright.realstack.config.ts --project=journeys e2e/realstack/books-browsers.spec.ts e2e/realstack/photos.spec.ts)`.
To isolate Firefox and WebKit decoding of synthetic FFmpeg HLS segments, run
`(cd frontend && npx playwright test e2e/v1_transcode.spec.ts --workers=1)`.
The browser commands start their own local servers.

### Linux ARM64 qualification

At `1a482be`, Docker Desktop's Linux 7.0.14 ARM64 kernel exposed Landlock
ABI 8. Bootstrap and browser-runner self-tests, repository hygiene and the
frontend production build passed. The focused backend replay of the parser,
book, photo, query-plan and original #339 test files passed 188 tests and 23
subtests, with the namespace-only and x32-only parser tests skipped. The
separately generated M2TS corpus passed all three packet-offset checks.
Firefox and WebKit decoded the synthetic HLS transcode. The real-server
Books and Photos journeys also passed.

Real-server WebKit playback passed legacy-codec negotiation and remux. Both
HEVC-source transcode cases stalled at video time 0 before the first-frame
assertion, including the case that would then seek. This reproduces the
[Release 1 Linux WebKit results](design/music-rework-release-1.md#repository-checks).
[Issue #402](https://github.com/shiftedx/lumina/issues/402) records the
captured stream replay and tracks the fix. The full Linux aggregate and the
amd64 runtime still need qualification.

## Manual and provider-dependent gaps

The automated checks do not cover real provider availability, third-party sign-in, DRM, production TLS and DNS, signed native installers, or cross-version migration and downgrade. Check those by hand, using accounts and media you are authorized to use. The Docker, backup and restore procedures are in [`../docker/README.md`](../docker/README.md).

## Household-scale performance

`make perf` (not part of `make check`) seeds a temp data dir with `scripts/perf/seed_household.py` (6 members, 20k library items across videos/movies/episodes/tracks with an external root, 300 follows with feeds, 5k notes, 500 transcripts × 1k cues, 200 collections, queues, search history, 50k playback rows, 5k jobs with attempts), runs the real backend with provider extraction stubbed (`serve.py`), drives 6 members with 0–0.5 s think time for 60 s (`load.py`) and then measures the built UI in Chromium (`ui.mjs`). Reference host: Apple M1 Pro, 10 cores, 16 GB, SQLite 3.50, shared with other workloads (load average 7–14), so single-digit differences are noise.

| Measure (p95 unless noted) | Before | After |
| --- | --- | --- |
| `GET /api/collections` | 1403 ms | 51 ms |
| `GET /api/library?search=` (FTS page) | 474 ms | 96 ms |
| `GET /api/search` (local typeahead) | 487 ms | 114 ms |
| `GET /api/discovery/home` | 158 ms | 39 ms |
| `GET /api/admin/diagnostics` p50 | 50 ms | 10 ms |
| Library list views / groups / title sort | ≤ 77 ms | ≤ 66 ms |
| Session bootstrap (`session/me`, `settings/me`) | ≤ 25 ms | ≤ 11 ms |
| Every other hot endpoint (queue, follows, notes, cues, jobs, admin, SSE connect) | ≤ 72 ms | ≤ 44 ms |
| Cold Home FCP / shelves ready | 140 / 151 ms | 64 / 123 ms |
| Open Watch / Admin / Settings (client route) | 322 / 307 / 314 ms | 55 / 56 / 58 ms |
| Library scrolled 30 pages: DOM nodes, JS heap | 63k, 44 MB | 13k, 13 MB |
| Library page append / main-thread for 30 pages | 1452 ms / 9.0 s | 372 ms / 2.8 s |
| Watch chunk (gzip), hls.js loaded only for HLS | 189 KB | 28 KB |

Fixes: id-driven visibility checks no longer let SQLite start from a MULTI-INDEX OR over every visible item (`LibraryService.visible_predicate`, plan-guarded in `test_query_plans.py`); FTS first pass over-fetches 4× with a rowid tiebreak so other members' private hits cost no second pass; `webpage_url` and `(user_id, last_watched_at)` indexes; Home reads the newest 500 plays and only follow labels; ffmpeg version probed once per process; Library collections fetch entries only for open rows (N+1 detail fetches and a per-collection `<select>` of every loaded title were the UI's biggest cost); no per-card backdrop blur; idle preload of Watch/Settings/Admin so React never holds a Suspense fallback on navigation. Known ceilings: dense FTS queries rank every match (~20 ms per 17k matches); the Library grid page is O(visible items) under the unanalyzed planner.
