# Backend test readiness

The audit starts at `b3c5ff7`: **5,290 expanded cases / 3,860 authored templates**.
After pruning: **5,284 cases / 3,854 templates**, with six deletions and no moves.
The [case ledger](agents/pytest-ledger.md) records every original case. The
[history and negative controls](agents/pytest-history.md) name retained coverage
and distinguish real regression catches from creation, timing and test drift.

Most pytest cases already check meaningful behavior at the cheapest correct
API, service, persistence or algorithm seam. There is no deletion quota. Preserve
security, data-integrity, migration, concurrency, real-media and release/operator
contracts. The existing scheduling catcher documented by issue #65 and PR #66
is protected. Six duplicate or source/signature recipe checks are removed only
after retained behavior contracts demonstrate selective RED → GREEN controls.
Application code is unchanged.

## Tiers and commands

Run through the pinned toolchain after `mise exec -- make bootstrap`:

| Tier | Command | Budget per run | Purpose |
| --- | --- | --- | --- |
| Smoke | `mise exec -- make backend-smoke` | <600 s | 50 critical authentication, authorization, storage, migration, playback, import and concurrency contracts; fast backend merge bar. |
| Full | `mise exec -- make backend-full` | <2,700 s | All retained pytest cases; required backend gate and pre-release qualification. |
| Repeated qualification | `mise exec -- make backend-smoke BACKEND_REPEAT=2` then `mise exec -- make backend-full BACKEND_REPEAT=2` | Each repetition below its tier budget | Two independent runs, four workers by default. |
| Repository gate | `mise exec -- make check` | Backend full budget applies to its backend phase | Bootstrap/runner tests, TypeScript, production bundle, backend, Vitest and full Playwright tier. |

`make backend-test` aliases full. Set `BACKEND_WORKERS` to change concurrency;
set `BACKEND_OUTPUT` to a fresh empty directory to retain reports at a known path.
The runner prints its report path. It uses standard pytest-xdist work stealing,
collects first, and checks exact collected/result identity, exit status, failures,
skips and total wall time. Collection, execution, validation and teardown count
against each budget. Retries do not turn a failed qualification green.

A new pytest test needs an observable contract and a distinct input domain or
regression reason. Extend existing coverage first. Logic and copy belong at the
cheapest correct seam; avoid new helper, source spelling, signature, or fixture
recipe checks when an existing behavior contract already catches the defect.
A new UI test needs a journey reason; logic and copy go lower.

## Isolation and prerequisites

Each invocation owns private reports and app data; each repetition gets fresh
app data and a base temp directory beneath the system temporary directory.
The autouse fixture gives each case its own filesystem/configuration and DB,
resets overrides and rate-limit state, and allows only loopback networking.
Real fixture servers bind port zero. xdist isolates process-global singletons
between workers. Tests own their subprocess handles and clean them up. The eight opt-in performance cases are serial exceptions: shared seed writes
and p95 measurement validity require `make perf-titles` without xdist. The
parallel runner rejects `PERF_TITLES_ROOT` rather than racing that fixture. The runner times out and cancels only its own detached
process group, including surviving children.

Full collection includes eleven prerequisite-dependent cases on this macOS host:
eight seeded-library performance measurements require `PERF_TITLES_ROOT`
(`mise exec -- make perf-titles`); one chromaprint measurement needs the
chromaprint muxer; one address-space enforcement case requires Linux; one HDR
case requires libx265 and zscale. Their exact node IDs and skip reasons are
allowlisted in `backend/tests/pytest-tiers.json`. They remain retained coverage,
but **a skip is not a successful execution**. Available media/platform prerequisites may make
their three cases run; seeded performance cases use the separate serial lane.
Any unrecognized skip fails qualification. Smoke permits none.
Use the production Linux/Jellyfin-FFmpeg toolchain and seeded perf lane to qualify
these prerequisites before a release that changes the relevant behavior.

## Measurements

Initial unchanged serial baseline: **584.990 s**, **5,275 passed, 11 prerequisite
skips, 4 failed**. The temp-directory guard correctly rejected the audit harness's
explicit `/tmp` base on macOS; the runner now uses `tempfile.gettempdir()`.
Three removed-route tests assumed a frontend build existed. Their exact fallback
assertions were repaired and verified both with and without a SPA mount; removed
route-table checks remain. These are environment/test drift, not app regressions.

Measured on the same shared host with four xdist workers; collection, validation
and successful fixture cleanup are included:

| Tier | Run 1 | Run 2 | Results each run |
| --- | --- | --- | --- |
| Smoke | 6.993 s | 7.157 s | 50 passed; zero skips/failures |
| Full | 158.742 s | 163.245 s | 5,273 passed; 11 declared prerequisite skips; zero failures/errors |

Commands: `mise exec -- make backend-smoke BACKEND_REPEAT=2` and
`mise exec -- make check BACKEND_REPEAT=2` (full backend phase). Private reports:
`/tmp/lumina-pytest-qualified-smoke-final/report.json` and
`/tmp/lumina-pytest-qualified-full-final/report.json`.
Parallel speedup is not attributed to six deletions. Complete repository-gate
and supplemental Linux-prerequisite results follow after verification.
