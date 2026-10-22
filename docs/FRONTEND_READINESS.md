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

## Qualification record

Before pruning, the mocked run selected 659 cases: 657 passed, one production
entry was intentionally skipped and Firefox failed during browser launch; wall
962.432 s. The ordinary realstack run selected 57: 51 passed, five skipped and
the timestamped-note journey failed; wall about 14 minutes. These are observed
failing baselines, not green performance claims. A configuration edit triggered
one Vite reload during the mocked baseline, so these runs do not support a
controlled before/after speedup ratio.

The audit reduced the mocked suite **659 → 592**; the subsequent main merge of
PR #143 adds two retained onboarding journeys, making the current selection
**594 cases**. Across the original 807
expanded instances: **708 keep, 13 move, 86 delete**. Two kept cases were renamed
and trimmed to their routing journey; their original ledger rows name the new
contracts. The current full tier selects **650** cases (648 audited cases plus those two
new journeys), including realstack and WebKit;
60 retained benchmark instances stay opt-in as described above.

Final qualification on the integrated branch passed twice with four workers:

| Tier | Selected and passed per run | Run 1 | Run 2 | Budget | Failures / skips / flakes |
| --- | ---: | ---: | ---: | ---: | --- |
| Smoke | 50 | 128.370 s | 136.789 s | <600 s | 0 / 0 / 0 in both |
| Full | 650 | 1,071.455 s | 1,060.199 s | <2700 s | 0 / 0 / 0 in both |

Build time is separate: smoke 14.315 s; full 10.512 s. Browser wall times include
selection, fixture/server startup, tests and teardown. All 30 ordinary performance
cases and all three real WebKit decode cases passed in both full runs. Both
Firefox journeys and both protected regression catchers passed twice. The 60
opt-in benchmark instances remain outside this qualification.

The complete gate passed in phases: backend **5,279 passed**, 11 existing skips,
598.78 s; final Vitest **2,659 passed in 305 files**, 41.88 s; bootstrap, the three
Node runner checks, frontend/E2E typecheck and production/style/bundle gates passed.
After the frontend-only draft fix, the gate resumed with unchanged backend checks
preserved and all frontend checks plus two full browser runs repeated:

```sh
mise exec -- make check E2E_REPEAT=2 E2E_OUTPUT=<fresh-private-directory>
# After repairing the frontend regression found by that run:
mise exec -- make check -o bootstrap-test -o e2e-tier-test -o backend-test E2E_REPEAT=2 E2E_OUTPUT=<another-fresh-private-directory>
```

Earlier pre-isolation full attempts selected 648 cases: each passed 613, failed
both Firefox launches and left 33 dependent cases unrun, in 905.665 s and
993.213 s. Those incomplete attempts are not passing budget measurements. A later
integrated attempt caught the filter Back regression and was stopped for repair.
The final complete runs above supersede those failed/interrupted attempts; no
application assertion or budget was weakened to obtain green results.

All owned browser, fixture and Vite processes exited. Successful fixture roots
were removed; diagnostic failure evidence remains in private artifact directories.
