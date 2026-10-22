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

Install all pinned engines once: `cd frontend && mise exec -- npx playwright install chromium firefox webkit`.
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
| Complete ordinary gate | `mise exec -- make check` | Every row above, plus the combined full browser tier (`make e2e-full`). Required before opening a pull request. |
| Real-browser remux/transcode decode | `cd frontend && npx playwright install webkit` once, then `mise exec -- make e2e-browsers` | WebKit actually decodes the backend's H.264/AAC HLS output for a remux and a transcode session (`currentTime` advances), and that a seek past the converted range restarts the conversion and lands near the target (#137). Included in the full tier and `make check`; install WebKit before running. Required for changes to local remux/transcode playback (`backend/app/services/local_playback_sessions.py`, `frontend/src/localPlayer.tsx`). |

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
