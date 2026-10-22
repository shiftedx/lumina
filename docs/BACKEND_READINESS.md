# Backend test readiness

The audit starts at `b3c5ff7`: **5,290 expanded cases / 3,860 authored templates**.
After pruning: **5,285 cases / 3,855 templates**, with six deletions, one new cover-art/HDR contract, and no moves.

**Qualification is complete.** Every retained backend case passed twice in its
appropriate cohort, and the full repository gate passed at the same app source.

The [case ledger](agents/pytest-ledger.md) records every original case. The
[history and negative controls](agents/pytest-history.md) name retained coverage
and distinguish real regression catches from creation, timing and test drift.

Most pytest cases already check meaningful behavior at the cheapest correct
API, service, persistence or algorithm seam. There is no deletion quota. Preserve
security, data-integrity, migration, concurrency, real-media and release/operator
contracts. The existing scheduling catcher documented by issue #65 and PR #66
is protected. Six duplicate or source/signature recipe checks are removed only
after retained behavior contracts demonstrate selective RED → GREEN controls.
The audit also exposed and repaired Chromaprint detection and HDR-probe
regressions on the production media toolchain; both existing real-media tests
are now protected. Existing timing cases also caught unbounded ORM-result
allocation and redundant recommendation scoring; their measured app fixes and
revert controls are recorded in history.
An autouse test fixture now resets rate limits for direct API clients as well.
Successful legacy probe caches refresh lazily once; same-file loudness and
scanner acknowledgement survive, and the version marker stays out of API facts.
Cached-error behavior is preserved.

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

Final code commit: `b8d52f7`. Two independent repetitions on the shared host:

| Cohort | Run 1 | Run 2 | Results each run |
| --- | --- | --- | --- |
| Smoke, four workers | 5.860 s | 5.814 s | 50 passed; zero skips/failures |
| Ordinary full, four workers | 139.950 s | 138.549 s | 5,274 passed; 11 declared prerequisite skips; zero failures/errors |
| Linux/production FFmpeg, three workers | 16.670 s | 16.816 s | 3 passed; zero skips/failures/errors |
| Seeded performance, serial | 63.441 s | 63.386 s | 8 passed; zero skips/failures/errors |

Smoke/full wall times include collection, execution, validation and cleanup.
Supplemental wall times include seed/container/venv setup and cleanup. The
11 ordinary-full skips are exactly the three Linux/media prerequisites and
eight serial measurements; every retained case has therefore executed and
passed twice across its appropriate cohort. Only the eight seeded measurements
are serial exceptions. Smoke is below 600 s and full below 2,700 s; adding each
full run's supplemental cohorts also remains below 2,700 s.

Commands: `mise exec -- make backend-smoke BACKEND_REPEAT=2` and
`mise exec -- make check BACKEND_REPEAT=2`. The latter invokes full twice.
The timing lane runs `mise exec -- make perf-titles` twice, each with a fresh
seed, GC enabled, and no other audit test jobs competing. Its two final runs
recorded load1 3.97→4.22→4.05 on 16 cores. No threshold, sample count or timing
assertion was relaxed; failed diagnostic attempts remain in history.

Private reports:

- `/tmp/lumina-pytest-merge-smoke/report.json`
- `/tmp/lumina-pytest-merge-check-backend/report.json`
- `/tmp/lumina-pytest-merge-perf-final/report.json` and `run-{1,2}.xml`
- `/tmp/lumina-chromaprint-pinned-8014/merge-b8d52f7-parallel/run-{1,2}/summary.json`

The Linux cohort uses Python 3.12.14 and Jellyfin FFmpeg 8.1.2-5 arm64 from the
repository's exact Python image digest and Jellyfin .deb checksum. Its source
mount is read-only, bytecode/app data are private, and containers use `--rm`.
Pinned Dockerfile and executable commands are in
`/tmp/lumina-chromaprint-pinned-8014/`; no app was deployed. Reproduce with
those tools on PATH:

```sh
cd backend
mise exec -- .venv/bin/python -m pytest -q -n 3 --dist worksteal -p no:cacheprovider \
  'tests/test_media_segments.py::test_fingerprint_finds_a_shared_intro_at_different_offsets[chromaprint]' \
  tests/test_title_image_upload.py::test_an_encoder_past_its_address_space_fails_cleanly \
  tests/test_v1_transcode.py::test_hdr10_tone_maps_in_software
```

Initial unchanged serial baseline: **584.990 s**, **5,275 passed, 11 prerequisite
skips, four explained test/harness failures**. The runner now uses the actual
system temporary directory; three removed-route contracts handle both built
and unbuilt SPA mounts. The baseline had 5,290 cases / 3,860 templates; six
proven redundant tests were removed and one cover-art/HDR contract was added,
leaving 5,285 cases / 3,855 templates. All original rows reconcile exactly,
including seven caption labels and two documented cache-test renames.
The wall-time improvement includes parallel execution and readable parameter
IDs; it is not attributed to deleting six tests.

`mise exec -- make check BACKEND_REPEAT=2` exited 0 at app source `b8d52f7`:
bootstrap/runner checks (15), tier-driver checks (3), TypeScript, production
build, both backend repetitions, **2,659 Vitest tests / 305 files**, and
**650 Playwright cases in 1,031.952 s**. Browser skips, unexpected failures and
flaky results were all zero; its 2,700 s budget was met. Later commits only
record this evidence in documentation. Log: `/tmp/lumina-pytest-merge-check.log`;
browser report: `/tmp/lumina-playwright-tier.ytF4v1/full-report.json`.
Standards and Spec source/documentation reviews found no remaining actionable
issues.
