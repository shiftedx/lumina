# Frontend browser readiness

A new UI test needs a journey reason; logic and copy go lower. Prefer a public
Vitest component or service contract for state, validation, payloads and wording.
Keep browser tests for navigation/history, native media and browser APIs, rendered
accessibility, responsive containment and keyboard/pointer interaction. A test
with demonstrated regression evidence stays protected.

The complete historical inventory and retained-contract mapping are in
[the Playwright ledger](agents/playwright-ledger.md). The history review and
behavior-revert proofs are linked there. Capture walks without a golden or
behavioral assertion are removed; rendered geometry, focus and contrast checks
remain useful browser contracts through a UI rework.

| Tier | Purpose | Budget | Command |
| --- | --- | --- | --- |
| Smoke | 49 critical journeys plus real-backend setup; merge bar for UI changes | <600 s per run | `mise exec -- make e2e-smoke E2E_REPEAT=2` |
| Full | All retained ordinary browser journeys, accessibility and performance budgets, production offline entry and real WebKit decode; pre-release gate | <2700 s per run | `mise exec -- make e2e-full E2E_REPEAT=2` |
| Complete repository | Bootstrap, types, build, backend and Vitest, then one full browser run | Browser phase <2700 s | `mise exec -- make check` |

Smoke selection is explicit in `frontend/e2e/smoke-cases.json`: every entry names
its journey. Both tiers default to four workers. Qualification requires two
passes with no failures, flaky retries, skips or missing cases. The driver
records a private selection manifest, JSON results and wall time for each run;
empty/incomplete reports cannot qualify. Set `E2E_OUTPUT` to a fresh empty
directory to choose the artifact destination. The driver prints each result.
The production build is recorded separately; the timed browser phase includes
fixture/web-server startup, tests and teardown. Waiting for the shared performance
lock is outside the browser timer and is not a measured speed improvement.

Each invocation reserves four independent loopback ports and uses fresh fixture
and artifact directories. Mocked tests get Playwright's fresh browser context,
cookies, localStorage, service-worker scope and route fixtures for each test;
the Vite server is read-only. No native app group or defaults suite exists here.
Real-backend journeys explicitly run serially because they intentionally mutate
one household/database and its owner session. Performance measurements run after
this invocation's mocked workers and real journeys finish, and WebKit decode
follows the measurements. The ledger records these serial exceptions. The full
Make target retains the repository's machine-wide performance lock; it is not an
Xcode restriction. Only this driver's own children receive cancellation signals.
Successful fixture roots are removed; failure evidence is retained privately.

Opt-in `playperf/` reader/video/network measurements and four `PERF_STRICT`
reference-host cases remain separate benchmarks, with their existing commands
and host prerequisites. They are inventoried in the ledger, but are not ordinary
UI journeys and are not included in either tier's count or timing. They must not
be described as qualified by a normal full run. See
[performance audit](performance/comprehensive-audit-2026-10-17.md) and the Playwright configs.

## Qualification record

Before pruning, the mocked run selected 659 cases: 657 passed, one production
entry was intentionally skipped and Firefox failed during browser launch; wall
962.432 s. The ordinary realstack run selected 57: 51 passed, five skipped and
the timestamped-note journey failed; wall about 14 minutes. These are observed
failing baselines, not green performance claims. A configuration edit triggered
one Vite reload during the mocked baseline, so these runs do not support a
controlled before/after speedup ratio.

The mocked suite is now **592 cases** (659 → 592). Across the original 807
expanded instances: **708 keep, 13 move, 86 delete**. Two kept cases were renamed
and trimmed to their routing journey; their original ledger rows name the new
contracts. The full tier selects **648** cases, including realstack and WebKit;
60 retained benchmark instances stay opt-in as described above.

Smoke passed twice with four workers: **50/50 in 191.898 s**, then **50/50 in
199.451 s**, with zero skips, failures or flakes (build separately 12.513 s).
The runner was subsequently hardened for bounded cancellation and startup bind
conflicts; its process-level cancellation/report checks pass. Full qualification
and the complete repository gate are still in progress. Firefox's pinned runtime
times out before creating a page on this host; a clean reinstall and an isolated
newer runtime also failed before app code. The Firefox tests remain. A failing
or skipped browser cannot satisfy readiness.
