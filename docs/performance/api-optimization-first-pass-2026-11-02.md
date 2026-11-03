# API performance: first implementation pass

Implements the first three priorities from the [API audit](api-performance-audit-2026-11-02.md): native Home, library queries, and redundant web polling. No database migration, new index, runtime dependency, or global response cache.

## Changes and preserved contracts

- **Library recent pages:** bound each disjoint visibility branch to the requested page before the final merge/sort. Preserve caller-owned shared items, admin ownerless items, section/rating permissions, filters, cursor order and null ordering. At most three bounded branches feed the final sort.
- **Native Recent:** for explicit `DateCreated` sorting, fetch a bounded page and a separate exact filtered count. Keep the existing window-count path for ordinary alphabetical catalogs; a general count split regressed that path in the first experiment and was rejected. Exact totals, empty pages and zero limits remain supported.
- **Music cards:** batch artist-parent resolution across album cards, preserving consolidated artist aliases. Home music shelves use the same bulk mapper.
- **Native Home:** adopt fresh, explicitly completed rich Resume/Next Up shelves and selectively fetch missing legacy shelves. The additive `completed_shelf_ids` response field includes intentionally empty local shelves; old servers retain safe fallback. A failed refresh keeps cached content visible but cannot use stale completion facts. Legacy Recent remains authoritative for hero content. Concurrent restore/admission work is joined with per-shelf coverage rather than cancelled and repeated. Account/session cancellation remains enforced across cache awaits.
- **Web reads:** one active snapshot load and at most one trailing reconciliation. Abort actual HTTP fetches on member/key changes and unmount. Awaited manual actions settle after their relevant fresh read, including Activity Stop racing an existing poll. Navigation is independent of route changes; settings mutations, periodic visible refresh and foreground reconciliation remain supported.

## Measured request counts

These are deterministic mocked client workflows, not production network timings.

| Workflow | Before | After |
| --- | ---: | ---: |
| Navigation: startup → Movies → Streaming → Home | 5 | 1 |
| Pending snapshot plus foreground bursts: peak active reads | 3 | 1 |
| Same snapshot burst: total reads after settlement | 3 | 2 |
| iOS explicit pull refresh, complete rich response | 6 | 4 |
| tvOS foreground refresh, complete rich response | 6 | 4 |
| iOS initial Home with cached Recent / ordinary foreground | 3 | 3 |
| Cold native Home, empty initial compatibility state | 6 | 6 |

Incomplete or failed rich Home responses selectively return to required legacy reads (up to the original six). Cold first-shelf parallel loading is preserved. The request reduction is deliberately narrower than a blanket six-to-three claim.

## HTTP and CPU measurements

Baseline: `69e7ca0f97a814cd99b9acaa1e248918fea509`. Identical independently copied synthetic audit databases; canned external providers, loopback-only network, lifecycle/background workers disabled, pinned Python 3.12.15. Two before/after rounds in ABBA order; each endpoint has 40 sequential and 40 six-worker warm samples per variant, plus first requests. Six workers share one synthetic admin. No filesystem-cache flush. Separate ABBA sequential runs recorded server process CPU to response headers (40 samples per variant); concurrent process CPU would not isolate requests.

**The host was heavily contended throughout (load averages in the hundreds).** Unchanged controls also slowed in after windows. Wall times are local observations, not promised production improvements; CPU and request/query counts are additional evidence.

| Endpoint / mode | Sequential median ms, before → after | Six-worker median ms, before → after | Sequential server CPU ms, before → after | SQL before → after |
| --- | ---: | ---: | ---: | ---: |
| Web Library recent, 50 | 113.230 → 74.840 | 680.798 → 224.348 | 99.437 → 61.214 | 6 → 6 |
| Native Recent, 50 | 167.267 → 106.700 | 948.866 → 456.260 | 166.953 → 89.610 | 7 → 8 |
| Web title sort, unchanged control | 69.032 → 81.524 | 160.024 → 197.452 | 55.841 → 56.743 | 6 → 6 |
| Native alphabetic catalog, unchanged control | 19.914 → 23.566 | 96.590 → 96.029 | 18.576 → 19.378 | 6 → 6 |
| Rich native Home | 163.661 → 205.431 | 1023.732 → 1066.807 | 159.902 → 192.832 | 72 → 53 |
| Navigation, unchanged server control | 13.764 → 16.418 | 47.245 → 62.121 | 11.107 → 13.123 | 11 → 11 |

Library recent used about 38% less sequential server CPU; native Recent about 46% less. Rich Home uses 19 fewer SQL statements but this run does **not** establish an endpoint latency improvement. Its client benefit is avoiding redundant legacy shelf requests. A separate after-then-before 12-sample Home check, at lower host load (24/21 one-minute average), measured 136.915 vs 166.577 ms wall and 136.094 vs 170.842 ms CPU. That reverses the earlier result; retained measurements show substantial run variability rather than a stable regression or a guaranteed Home latency gain. No special-case Home code was added to chase the noisy result. Index experiments did not justify a new schema/index and were rejected.

All measured GETs returned 200. Library, catalog, Recent and navigation response hashes match before/after. Rich Home differs only in the additive completion field and generated recommendation `LuminaReco.list_id`; item ordering/content otherwise match. Response payload sizes for the unchanged contracts are retained in the [machine-readable evidence](measurements/api-optimization-first-pass-2026-11-02.json).

## Validation and limits

The final focused backend run passed 143 tests covering Library permissions/pagination/query plans and native API/count/DTO/Home contracts. The final 26 polling/navigation/Activity tests and the route workflow regression cover coalescing, stale sessions, teardown, awaited refresh and navigation route changes. Ten selected Core tests cover completed-empty/partial/failed Home snapshots and full-versus-subset in-flight coalescing. Both iOS and tvOS simulator builds pass; the production frontend build passes.

The pre-existing six broader `LuminaApp.test.tsx` Home/navigation fixture failures were not folded into this change. No full repository/Core/UI suite was run, per the focused-test workflow. No physical-device timing, WAN reliability, production load test, native archive or TestFlight upload is claimed. Native changes take effect in a subsequent app build; the backend completion field is compatible with existing apps.
