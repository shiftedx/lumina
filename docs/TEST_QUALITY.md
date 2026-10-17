# Meaningful tests and suite reduction

Test a product or service contract, not each implementation step. A test should name an important failure it detects and exercise the boundary where that failure occurs. Prefer extending an existing workflow/contract test. Small pure tests remain appropriate for consequential algorithms and trust-boundary behavior; their size alone does not make them low quality.

## Initial audited reduction

The 0.3.4 frontend collected 2,528 cases. This pass removes 70 cases from 11 source-inspection files (627 lines), leaving 2,458. It removes tests that parse CSS spelling, require particular selector organization or declaration values, and grep for retired identifiers. The application is unchanged.

| Removed files under frontend/src | Retained observable contracts |
| --- | --- |
| styles/base.test.ts, app/shellCss.test.ts | e2e/v1_a11y_sweep.spec.ts: focus rings, keyboard traversal, reduced motion, forced colors, shell across themes/widths |
| ui/ui.css.test.ts | UI component behavior tests; e2e/v1_settings_tv.spec.ts target sizes; v1_a11y_sweep.spec.ts; borderless-surfaces.spec.ts computed borders/radii |
| features/settings/settingsCss.test.ts, features/settings/outlinedBoxesCss.test.ts | e2e/settings_visual_polish.spec.ts layout and reachable controls; borderless-surfaces.spec.ts; v1_settings_tv.spec.ts |
| features/gallery/mediaSurfacesCss.test.ts, features/gallery/liveHeroContrast.test.ts | e2e/borderless-surfaces.spec.ts, live-surface.spec.ts and v1_a11y_sweep.spec.ts rendered layout/contrast |
| features/watch/player.css.test.ts | e2e/watch_quality.spec.ts, media_quality.spec.ts, v1_watch_layout.spec.ts and v1_a11y_sweep.spec.ts player layout/controls |
| features/downloads/downloadsCss.test.ts, features/collections/collectionsCss.test.ts | e2e/vault.spec.ts journeys and v1_a11y_sweep.spec.ts rendered controls/layout |
| retired.test.ts | Frontend build resolves imports; current route/component and browser journeys verify the current product. Retired-name spelling is not a user contract. |

Exact declaration placement, layout recipes, and retired identifiers intentionally cease to be test requirements. Global token contrast checks, image/asset contracts, bundle/style budgets, release packaging, functional component tests, and browser accessibility remain. No security, migration, data-integrity, media-range, or concurrency case is removed. This is a 2.8% case-count reduction and a maintenance cleanup, not a claim of a dramatically faster full gate.

## Larger reductions

1. **Measure cost and uniqueness.** Use pytest durations and browser timings. The backend currently has 3,506 test functions expanding to 4,827 cases; 1,823 functions are under 12 lines. These counts identify review candidates, not deletions: a short visibility or cancellation regression can be indispensable.
2. **Give each contract a primary layer.** Keep API tests for observable authorization/status/data contracts; keep focused service tests for state machines, transactional failures and concurrency; use browser journeys for interaction, layout and accessibility. Remove lower-level copies only after recording which retained case detects the same consequential failure.
3. **Audit overlapping modules first.** The Jellyfin API, clients and integration suites overlap in route probes, status bodies and projections. Member-access behavior also appears at multiple layers. Map common setup, assertions and regressions before deleting any authorization check. Shared request setup can reduce runtime without deleting coverage.
4. **Review repeated browser matrices.** Broad accessibility, feature quality and layout suites repeat theme/viewport combinations. Retain combinations that reveal different failure classes (contrast, responsive navigation, touch size, keyboard/focus). Remove repeated journeys only after the broad contract and targeted historical regression cover their distinct behavior. Test with deliberate layout/contrast/focus faults before reducing a matrix.
5. **Require a quality check for substantial deletions.** Introduce representative faults in the affected implementation and show retained tests fail. Line coverage alone cannot prove equivalent protection. Reject a reduction that only loops the same cases inside one test, drops assertions, changes thresholds, or moves tests out of the default gate while leaving the same maintenance burden.

Do not choose a global percentage target before this mapping. The present evidence supports removing 70 recipe checks; it does not yet support deleting hundreds of behavioral cases. Future work should report deleted contracts, retained regression protection, runtime saved, and maintenance lines removed alongside case counts.

## Measured runtime priorities

The backend gate passed 4,827 cases in 530.25 seconds. Its duration report identifies concrete follow-up work:

- `test_v1_transcode.py::test_a_playlist_replaced_while_it_is_served_is_answered_whole`: 16.01 seconds. Preserve the atomic-playlist contract; investigate replacing repeated race attempts with a deterministic overlap that demonstrates the failure without the fix.
- Four recommendation replay cases account for roughly 25 seconds in the slowest-call list. Review repeated synthetic seeding/replays for reusable setup while retaining determinism, comparison-policy coverage, privacy and target-quality contracts. These are distinct assertions, so their cost alone does not justify deleting them.
- Production-scale query-plan setup takes 6.67 seconds. Reuse immutable seeded data where isolation can be preserved; keep plan assertions that exercise the real data shape.
- Media fingerprinting and HLS cases take several seconds each. Preserve representative real-media/FFmpeg integration and regressions; reserve cheap boundary permutations for the pure planning layer where they can detect the same faults.

The frontend reduction passed all 2,458 retained cases across 287 files. Its value is fewer brittle requirements and less maintenance; the removed cases were cheap, so the next runtime gains should come from the costly setup and duplicate workload identified above.

## Verification of this reduction

`mise exec -- make check` passed: 4,827 backend cases (11 skipped), 2,458 frontend cases across 287 files, 47 real-backend browser cases (4 skipped), and 588 mocked browser cases (2 skipped), plus bootstrap, typecheck, build and style/bundle gates.

A standalone browser negative control replayed the retained rendered-focus and full-border predicates. Both passed on the normal Movies page, failed after deliberately injecting a missing focus ring or an unwanted full input border, and passed after removing the injected stylesheet. This verifies these representative predicates, not mutation coverage of every deleted recipe.

## Python optimization follow-up

The next pass reduces repeated *work*, retaining the consequential assertions:

| Change | Retained failure detector |
| --- | --- |
| Remove the separate legacy-only replay | The deterministic legacy household replay now also checks legacy report shape and absent pool recall. |
| Remove the separate recommendation-target replay | The deterministic three-policy comparison now checks all 24 synthetic quality targets, unresolved keys, coverage denominators and privacy on its existing report. |
| Remove the second title-seed subprocess | The pinned-shape seed now also checks category-backfill agreement, music key shapes, track numbers, folder/embedded cover distribution and real FLAC fingerprints. The secondary seed size is no longer a separate requirement. |
| Replace 300 playlist requests and a free-running writer | One HTTP request forces a real atomic rename after every manifest stat. Headers must match the complete current playlist. Restoring the historical `FileResponse` implementation makes this test fail (52-byte header, 74-byte body). |
| Remove LiveBadge animation/color source recipes | `e2e/live-surface.spec.ts` checks rendered animation, reduced motion and color; lifecycle labels and accessibility remain in the component test. |
| Remove the import dialog CSS recipe | Import, password clearing, cancellation and focus remain in the component workflow. The existing invitation browser journey now checks an opaque computed Dialog background. |
| Remove the repository-wide “Administration” grep | Current Settings component and browser journeys remain; spelling of retired identifiers anywhere in source is no longer a contract. |

This removes three duplicate backend workloads and four additional frontend recipe cases; extending the search materialization contract adds one typed/untyped case. Relative to 0.3.4, the total is 74 fewer frontend cases and two fewer backend cases. The playlist overlap retains its regression and removes 299 HTTP requests; its focused call fell from 15.21 seconds to about 0.25 seconds. The removed replay and seed runs accounted for roughly another 10 seconds in the prior duration report. These are slice measurements, not a guaranteed whole-gate percentage improvement.

An exact-AST-body duplicate audit also found identical test bodies in import-scope validation and music/video metadata validation. Those tests stay: their parameter sets exercise different invalid inputs and domain schemas. Short code or a common assertion is insufficient evidence of redundant coverage. Authentication, member access, migrations, ranges, state machines, FFmpeg integration and existing browser matrices stay in the gate.

Full verification: `mise exec -- make check` passed with 4,825 backend cases (11 skipped), 2,454 frontend cases across 286 files, 47 real-backend browser cases (4 skipped), and 588 mocked browser cases (2 skipped), plus bootstrap, typecheck, build and style/bundle gates. The backend gate took 505.99 seconds; the prior run took 530.25 seconds under different host conditions.
