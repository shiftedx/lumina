# Photo library validation

Date: 2026-10-24. Measurements use synthetic photographs and catalog records, not
household media. The local host is macOS arm64, Apple M4 Max, 64 GiB RAM. These
measurements are not Linux/arm64 release qualification or a promise of NAS throughput.

## Full gate

`mise exec -- make check` passed against the final implementation:

- Backend: 5,346 passed, 11 documented environment/performance-fixture skips.
- Frontend: 309 files, 2,679 tests passed.
- Browser full tier: 655 passed, zero skipped, unexpected, or flaky cases; 1,182.518 s
  against a 2,700 s budget. This includes real-server photo uploads/NAS indexing,
  the existing media performance budgets, and WebKit remux/transcode/seek playback.
- Typecheck, build, dependency-license and stylesheet-budget checks passed.

The repeat used one test attempt. Earlier investigation exposed NAS scheduling and
viewer-restoration bugs that were fixed before this run. Existing Home focus and
music-dock tests now wait for their observable controls; their production behavior
and assertions remain intact.

## Catalog

Run from the repository root:

```sh
mise exec -- backend/.venv/bin/python scripts/perf/photo_catalog.py --count 100000
```

The fixture contains 100,000 private photo records. Each measured request includes
the exact visible count, a 100-photo page, and batched personal state, albums and
people serialization. Five trials use the same SQLite database and session.

| Sort and position | Median | Maximum | SQL statements |
| --- | ---: | ---: | ---: |
| Capture date, first page | 19.12 ms | 47.41 ms | 5 |
| Capture date, 90% deep | 15.69 ms | 24.43 ms | 5 |
| Added date, first page | 13.66 ms | 15.01 ms | 5 |
| Added date, 90% deep | 20.53 ms | 31.03 ms | 5 |

The recorded plans use the capture/added catalog indexes and a range predicate
for deep pages. They do not use `OFFSET` or a temporary sort. These results include
an exact count that still scales with visible catalog size; they do not measure
cold disk, network delivery, free-text search, or face-clustering throughput.

[Machine-readable results](measurements/photo-catalog-100k-2026-10-24.json).

## Browser and storage contracts

`frontend/e2e/photos.spec.ts` exercises a 600-photo cursor-paged gallery on desktop
and phone. It checks bounded photo elements (fewer than 70), decoded thumbnails,
pagination, viewer navigation and focus return, favorites, trash and restoration,
People consent/naming/filtering, and light-theme phone details accessibility.
The row-layout unit regression packs 100,000 varied-aspect photographs.

`frontend/e2e/realstack/photos.spec.ts` uploads generated image bytes through the
actual API, verifies the original's SHA-256 and smaller decoded derivatives,
checks persistent favorites and restoration, and adds/scans a registered external
photo folder through the user interface.

Backend photo suites cover authorization, source confinement and symlink races,
immutable hash-checked snapshots, cache repair/eviction, partial/offline scans,
independent source lanes, cancellation/restart, and owner-scoped biometric jobs
and corrections. Source replacement invalidates old face assignments.

The production build measures aggregate compressed CSS at 75.2 KiB against a
75.5 KiB budget; the shared token/UI core remains within its existing 7 KiB budget.
The photo surface and viewer dependencies are lazy-loaded.

## Face inference

A production-path smoke test on this host loaded the catalog-pinned YuNet/SFace
artifacts, analyzed the upstream OpenCV sample converted to a bounded preview,
and returned 61 detections with valid 128-float embeddings. This checks model
integrity and runtime integration, not identity accuracy. Conservative grouping
remains a suggestion with manual naming, merge, split and ungroup controls.
See the [model research](../research/image-face-recognition-2026-10-24.md) for
provenance, licenses and the remaining household accuracy qualification.
