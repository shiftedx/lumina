# Playwright regression-history audit

**Base:** `18b35c6ad163989244343f476ac390a4b543f292`  
**Scope:** every configuration-selected Playwright case in `/tmp/lumina-playwright-inventory.json`: 807 unique expanded case/project instances, derived from 497 authored test/setup templates. This includes mocked and optional capture variants, realstack (journeys, perf, WebKit, setup), playperf, audit-readers, audit-video, and local-network.

## Rule

A qualifying result requires evidence that an **existing** Playwright workflow failed, that the failure led to an **application-code** fix, and that the repaired workflow passed. Test creation, a changed assertion or fixture, flakes, timing, visual drift, and setup repairs do not qualify. “No qualifying evidence found” is not proof that a test never caught a regression; it is not a deletion verdict.

## Qualifying regression catcher — protect

| Exact test | Evidence | App-code fix | Result |
| --- | --- | --- | --- |
| [`frontend/e2e/realstack/watch.spec.ts`](../../frontend/e2e/realstack/watch.spec.ts) — `a timestamped note persists and seeks the player` (current line 4) | [Issue #67](https://github.com/shiftedx/lumina/issues/67) identifies the existing realstack workflow (then line 23) as reproducing the Notes-tab reset after its earlier assertion had persisted the note. [PR #68](https://github.com/shiftedx/lumina/pull/68) says that existing timestamped-note/seek workflow failed before the fix and passed after it. The test title predates the fix: [`caa0a302`](https://github.com/shiftedx/lumina/commit/caa0a302d52f0aff43e7de0972600d241d401b73), 2026-10-11; fix on 2026-10-17. | [`a7f2b414`](https://github.com/shiftedx/lumina/commit/a7f2b41434eb795ffa38ae6519d4f27a0947b2de), changing `frontend/src/features/watch/WatchSurface.tsx` and `WatchTools.tsx` | **Never delete** under the audit rule. |

## Sources and coverage method

1. Enumerated every `frontend/e2e/**/*.spec.ts` (121 files) and the three config-selected setup tests. The expanded inventory is the authority for the 807 case/project rows and is mirrored in `/tmp/lumina-playwright-history.json`.
2. For every authored title/template, ran:

   ```sh
   git log --all -S '<authored title or template>' --format='%H%x09%s' -- frontend/e2e
   ```

   Of 497 templates, 493 had a source-history hit. The four initial exact-title/template misses were resolved with the line-history investigation below; none is a qualifying catcher.
3. Inspected all 24 `fix`/`perf` source-title hits (22 templates). In each instance the title was absent from the parent revision and introduced in that patch, so these are creation-time evidence, not qualifying catches. The full SHA/title mapping is retained in the machine map.
4. Reviewed the history of all six Playwright configs. Their changes do not establish an existing-test-failed → app-code-fix chain.
5. Reviewed first-party GitHub issue/PR/commit material for Playwright/e2e references. Aside from #67/#68, it did not establish that strict chain. This was a global repository scan, **not** a separate GitHub title query for every expanded runtime case.

## Follow-up: line history, mixed commits, and deletion-priority titles

The four former exact `-S` misses are resolved rather than treated as absence of history:

| Test | Primary-source follow-up | Classification |
| --- | --- | --- |
| `realstack/member-switch.spec.ts:5` | `git log 18b35c6 -L 1,45:frontend/e2e/realstack/member-switch.spec.ts` shows introduction at [`caa0a302`](https://github.com/shiftedx/lumina/commit/caa0a302d52f0aff43e7de0972600d241d401b73). The apostrophe escaping caused the title query miss. | Creation only; no qualifying evidence. |
| `realstack/watch.spec.ts:71` | `git log 18b35c6 -L 60,90:frontend/e2e/realstack/watch.spec.ts` shows introduction at `caa0a302`; [`7ffff102`](https://github.com/shiftedx/lumina/commit/7ffff10232749cb941149b54a18879eb80681dd4) adds Pause steps. [PR #115](https://github.com/shiftedx/lumina/pull/115) calls this a test repair after a four-second fixture completed during queue editing. | Test/fixture repair; no app-code regression fix. |
| `reco.spec.ts:145` | `git log 18b35c6 -L 130,160:frontend/e2e/reco.spec.ts` shows introduction at `caa0a302`. | Creation only; no qualifying evidence. |
| `reco.spec.ts:241` | `git log 18b35c6 -L 225,255:frontend/e2e/reco.spec.ts` shows introduction at `caa0a302`; `7ffff102` changes its expected recommendation count. No issue/PR says an existing failure prompted an app-code correction. | Feature/test expectation maintenance; no qualifying evidence. |

I enumerated 50 commits reachable from the base that modify both `frontend/e2e` and `frontend/src`. I fully inspected the e2e patches and relevant PR/issue evidence for the 11 that touch the requested deletion-priority files; the other 39 were enumerated to establish scope, not fully patch-reviewed. The priority e2e changes are test adaptations or coverage added with product work; no primary source records an existing Playwright failure causing an app-code fix. This includes the custom-dropdown updates in [PR #82](https://github.com/shiftedx/lumina/pull/82), settings-navigation adaptations in [PR #101](https://github.com/shiftedx/lumina/pull/101), and the fixture repair in PR #115.

For the priority set (auth render/rate-limit copy; metadata People captures; all `v1_admin_*`; diagnostics; Home; Downloads; Collections; settings registry; visual catalogue), I issued 40 exact-title GitHub issue/PR searches. The first 30 returned no match; after the search bucket reset, the remaining ten also returned no match. The global first-party GitHub review found no qualifying chain beyond #67/#68.

## Use in the ledger

- The machine-readable mapping is `/tmp/lumina-playwright-history.json`; it has an exact row for each of the 807 unique expanded case/project instances, its source-template search hits, and one of: `qualifying_regression_evidence`, `no_qualifying_evidence_found`, `no_qualifying_evidence_found_template_search`, or `no_qualifying_evidence_found_after_line_history`.
- Parameterized tests expand into 399 inventory rows. Their authored template was searched exactly, but an individual runtime interpolation was not independently searchable; those rows retain that uncertainty.
- Only the timestamped Notes/seek workflow has qualifying regression evidence in this audit. Preserve it regardless of duplicate/capture-walk classification. All other rows need their own journey or lower-layer coverage assessment; history alone does not authorize deletion.
