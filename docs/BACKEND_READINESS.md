# Backend test readiness

The audit starts at `b3c5ff7`: **5,290 expanded cases / 3,860 authored templates**.
After pruning: **5,285 cases / 3,855 templates**, with six deletions, one new cover-art/HDR contract, and no moves.

**Qualification remains incomplete:** ordinary smoke/full and the Linux cohort
pass twice, but two retained serial performance checks still exceed their
unchanged timing budgets. The same failures reproduce on the pre-audit base;
this audit does not claim all retained tests are green. See measurements below.

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
are now protected.
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
| Smoke | 6.412 s | 6.218 s | 50 passed; zero skips/failures |
| Full | 147.217 s | 145.115 s | 5,273 passed; 11 declared prerequisite skips; zero failures/errors |

Commands: `mise exec -- make backend-smoke BACKEND_REPEAT=2` and
`mise exec -- make backend-full BACKEND_REPEAT=2`, at code commit
`5512fe1` after all media/cache repairs. Private reports:
`/tmp/lumina-pytest-final-head-smoke/report.json` and
`/tmp/lumina-pytest-final-head-full/report.json`.
Parallel speedup is not attributed to six deletions.

The production Linux/Jellyfin-FFmpeg 8.1.2-5 arm64 prerequisite cohort also passed
twice at `5512fe1`: **3 passed, zero skips/failures/errors**, **17.121 s** and
**17.270 s** including container/venv setup. It covers chromaprint, Linux
address-space enforcement and HDR software conversion. The diagnostic image uses
the repository's exact Python image digest and Jellyfin .deb checksum; no app was
deployed. Reproduce on a Linux test environment with the pinned Jellyfin `ffmpeg` and
`ffprobe` on PATH and these installed prerequisites:

```sh
cd backend
mise exec -- .venv/bin/python -m pytest -q -p no:cacheprovider \
  'tests/test_media_segments.py::test_fingerprint_finds_a_shared_intro_at_different_offsets[chromaprint]' \
  tests/test_title_image_upload.py::test_an_encoder_past_its_address_space_fails_cleanly \
  tests/test_v1_transcode.py::test_hdr10_tone_maps_in_software
```

Pinned image Dockerfile, exact commands and JUnit evidence:
`/tmp/lumina-chromaprint-pinned-8014/final-head-5512fe1-20261022T151109/`
and its parent `Dockerfile`. Owned containers/processes were removed; the
diagnostic image remains available for review.

`mise exec -- make check BACKEND_REPEAT=2` completed with exit 0: bootstrap/runner
checks, TypeScript, production build, backend, **2,659 Vitest tests**, and
**650 Playwright cases in 1,116.555 s**. Its log is
`/tmp/lumina-pytest-make-check-final.log`. After subsequent media/cache repairs,
the final backend head was independently qualified twice as above; the final
bootstrap/runner lane also passed **15 tests**. Standards/spec and targeted media
reviews found no remaining actionable issues. Exact set reconciliation confirms
all 5,290 original cases map to 5,284 retained cases plus six deletions; the new cover-art/HDR contract brings collection to 5,285 cases, allowing
only the seven caption labels and two documented cache-test renames.

Serial seeded performance (`mise exec -- make perf-titles`, fresh seed each time,
no benchmark plugin, no other test jobs from this audit) remained **6 passed,
2 timing failures**, **zero skips or functional failures**, on two runs:

| Run | Pytest / total wall | Timing failures |
| --- | --- | --- |
| 1 | 62.87 / 71 s | Reco similar p95 128.0/120 ms; diagnostics 108.7/80 ms; 10k-title similar median 58/72 ms (pass), p95 143.770/120 ms. |
| 2 | 65.87 / 74 s | Reco similar p95 126.0/120 ms; 10k-title similar median 60/72 ms (pass), p95 153.730/120 ms. Diagnostics passed at 39.1/80 ms. |

Load was 11.30→10.33 and 10.33→13.93 on 16 cores (the second assertion recorded
8.8). Thus these failures are not dismissed solely for load exceeding core
count. A fresh-seed replay of only the two failing cases at unchanged base
`b3c5ff7` also failed: reco similar 129.2/120 ms, diagnostics 154.5/80 ms,
10k-title similar median 57/72 ms (pass), p95 133.854/120 ms. That baseline
assertion recorded load 17.0/16, limiting direct timing comparison. The relevant
recommendation/diagnostics/perf source is unchanged by this audit; profiling
found expected ranking/query work and no audit-added hotspot. The existing 10k
test explicitly documents GC-sensitive p95 tails. This establishes a pre-existing
qualification problem, not a justified app optimization or threshold change.

Logs: `/tmp/lumina-pytest-perf-final-{1,2}.log` and
`/tmp/lumina-pytest-perf-baseline-replay.log`. No budgets were relaxed, retries
were not counted as qualification, and the two failing checks are retained.
The PR stays draft until valid serial performance qualification or a supported
fix resolves these failures. The requested all-kept-tests completion criterion
is **not met**. No merge is requested by this audit.
