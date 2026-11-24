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

Explicit Firefox launches on macOS also get a unique temporary home through
`CFFIXED_USER_HOME`. Firefox's `-profile` alone still reads shared application
data, which macOS 27 can deny before Playwright connects
([upstream diagnosis](https://github.com/microsoft/playwright/issues/42768)).
The same pinned Firefox 141.0 failed the minimal page-startup probe without this
isolation and opened a page with it; the retained transcode journey then passed.
The helper closes the browser and removes its private home on success or failure.
Standard directories are created before launch to avoid macOS adding deny-delete
ACLs during initialization. This does not change host permissions or the user's
Firefox data. Revisit the workaround when the pinned browser includes Firefox 158.

The subsequent full run caught a separate app regression: Back reached the earlier
wall URL, then a delayed native filter-dialog close reapplied its old draft.
`39b26d8` discards a draft when the wall kind or query has changed since it opened.
The retained browser journey failed 12/100 instrumented baseline repetitions and
passed 100/100 with the fix. Public drawer tests are red without the guard and
green with it; the ledger now protects this additional regression catcher.

Opt-in `playperf/` reader/video/network measurements and four `PERF_STRICT`
reference-host cases remain separate benchmarks, with their existing commands
and host prerequisites. They are inventoried in the ledger, but are not ordinary
UI journeys and are not included in either tier's count or timing. They must not
be described as qualified by a normal full run. See
[performance audit](performance/comprehensive-audit-2026-10-17.md) and the Playwright configs.

## Historical qualification

The Playwright audit retained 708 cases, moved 13 and deleted 86 across the
original 807 expanded cases. The [case ledger](agents/playwright-ledger.md)
maps removed or moved tests to retained contracts. [The history review](agents/playwright-history.md)
records the audit and behavior-revert proofs.

| Tier | Passed per run | First run | Second run | Failures, skips and flakes |
| --- | ---: | ---: | ---: | --- |
| Smoke | 50 | 128.370 s | 136.789 s | Zero |
| Full | 650 | 1,071.455 s | 1,060.199 s | Zero |

Both full runs included all 30 ordinary performance cases, three real WebKit
decode cases and both protected regression catchers. The 60 opt-in benchmarks
were outside this qualification. The full gate also passed 5,279 backend cases
with 11 declared prerequisite skips, plus 2,659 Vitest cases across 305 files.
These counts describe that audit. Use the current runner manifest for the
selection and the [verification matrix](VERIFICATION.md) for today's complete gate.
