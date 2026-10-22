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

## 2026-10-22 current failure diagnosis — Notes seek workflow

The protected Notes workflow failed in the pre-pruning realstack baseline, but this did **not** identify a new product regression. Its retained historical evidence remains [Issue #67](https://github.com/shiftedx/lumina/issues/67) / [PR #68](https://github.com/shiftedx/lumina/pull/68).

The failed artifact showed the persisted note and its `Seek to 0:02` control after reload. Its trace records Playwright resolving and clicking that control; the player stayed at `0:03` after the test had directly assigned `video.currentTime = 3.5`. The test issued its note click immediately after that native seek assignment. Since `currentTime` seeks complete asynchronously, the test had two overlapping seeks and no completion boundary. This is test drift, not evidence that the app lost the persisted note or that the app's note callback failed.

A repeat run also found a separate test-isolation defect: the generic `getByRole('button', { name: /^Seek to / })` matched the previous repeat's persisted note as well as the new note. It failed strict locator resolution before testing player behavior. This is likewise a test defect, not an app regression.

The retained workflow now gives its note a repeat-indexed body, scopes the seek control to that note's list item, waits for the deliberate off-note seek's native `seeked` event, and confirms playback is above three seconds before invoking the persisted note. It keeps the original stored-timestamp bounds unchanged. No application source was changed.

Evidence commands (each uses a unique physical `REALSTACK_ROOT` under `/private/tmp`, own port `53931`, one worker, and separate output):

```sh
cd frontend
E2E_PORT=53931 REALSTACK_ROOT=/private/tmp/lumina-notes-diagnosis-<id> \
  npx playwright test --config=playwright.realstack.config.ts \
  --project=setup --project=journeys \
  --grep='a timestamped note persists and seeks the player' \
  --workers=1 --repeat-each=2 --output=/tmp/lumina-notes-diagnosis-results-<id>/artifacts
```

- An unmodified, physical-root run passed (the prior `/tmp/...` spelling was invalid because realstack setup rejects symlinked storage roots; that setup failure did not execute the workflow).
- The unmodified `--repeat-each=2` run passed once, then exposed the ambiguous generic locator before its behavior assertion.
- The repaired isolated run passed setup plus both repetitions: **3 passed in 2.7 minutes**; the two journey iterations took **6.134 s** and **4.743 s**. The owned runner and fixture process exited after completion.

This current-run diagnosis does not add a historical regression catcher or change the rule that the Notes workflow is never deleted because of #67/#68.

## Follow-up: direct GitHub title/body search for every move/delete template

At the final audit head, the ledger has **99** expanded platform-case rows marked `move` or `delete`. They map to **68** distinct authored templates in `/tmp/lumina-playwright-history.json`. I searched GitHub issues and pull requests together with:

```sh
gh api -X GET search/issues -f 'q=repo:shiftedx/lumina "<authored title>"' -f per_page=100
```

For interpolated test titles, the query used the static authored prefix before `${...}`. The API searches issue and PR titles and bodies. Twenty-five exact queries had already been recorded in `/tmp/playwright-priority-github-search.json`; this follow-up added 43. All **68/68 authored-template** queries returned **zero** matching issues or PRs. I also ran seven dynamic runtime-title queries: six returned zero; `catalogue dark desktop` found PR #61 only as a verification-log line. That PR neither modifies the catalogue test nor describes its failure; its changed regression test is `v1_twitch_playback`, so it is not qualifying evidence. Runtime queries: `capture People editor` (0); `catalogue dark desktop` (PR #61 verification-only); `catalogue light phone`, `catalogue light wide`, `catalogue light desktop`, `catalogue dark wide`, and `catalogue dark phone` (0 each). This is no qualifying regression evidence, not proof that a test never failed.

| Source template | Query title/prefix | Issues/PRs |
| --- | --- | ---: |
| `frontend/e2e/auth.spec.ts:4` | `login renders its title, fields and primary button` | 0 |
| `frontend/e2e/auth.spec.ts:17` | `setup renders when the vault has no owner yet` | 0 |
| `frontend/e2e/auth.spec.ts:48` | `a rate-limited sign-in says to wait a minute` | 0 |
| `frontend/e2e/home-live.spec.ts:24` | `followed channels live now come first, then popular live, each stream once` | 0 |
| `frontend/e2e/integrations.spec.ts:103` | `admin sends fresh revisioned monitor and profile operations, while rejected releases have no grab` | 0 |
| `frontend/e2e/integrations.spec.ts:108` | `a stale result asks for review, while an uncertain result blocks a write until reconciliation` | 0 |
| `frontend/e2e/media_quality.spec.ts:256` | `capture all metadata editor tabs at` | 0 |
| `frontend/e2e/media_quality.spec.ts:292` | `capture the uncatalogued Library lenses at` | 0 |
| `frontend/e2e/media_quality.spec.ts:308` | `capture every uncatalogued channel tab at` | 0 |
| `frontend/e2e/metadata-editor.spec.ts:151` | `capture: Artwork autosave at` | 0 |
| `frontend/e2e/metadata_people_quality.spec.ts:82` | `capture People editor ${variant.name}` | 0 |
| `frontend/e2e/onboarding_interests_loading.spec.ts:16` | `interest loading keeps Continue guarded while Skip remains available` | 0 |
| `frontend/e2e/onboarding_interests_loading.spec.ts:34` | `a failed interest load can retry without turning unknown interests into an empty choice` | 0 |
| `frontend/e2e/onboarding_interests_loading.spec.ts:56` | `a late initial interest response cannot replace the canonical Skip result` | 0 |
| `frontend/e2e/reco.spec.ts:26` | `shows with no interests chosen, says why under each card, and marks exploration only by its words` | 0 |
| `frontend/e2e/reco.spec.ts:37` | `Not interested replaces the card with Hidden. Undo, and Undo restores it` | 0 |
| `frontend/e2e/reco.spec.ts:50` | `when the eight seconds pass the card is gone and Home re-fetches` | 0 |
| `frontend/e2e/reco.spec.ts:63` | `keeps the menu and drops the reasons when the server sends no annotation (the switch is off)` | 0 |
| `frontend/e2e/reco.spec.ts:87` | `Clear recommendation history asks first, then clears` | 0 |
| `frontend/e2e/reco.spec.ts:97` | `lists the four kinds of feedback in Settings` | 0 |
| `frontend/e2e/reco.spec.ts:112` | `a recommended poster keeps its reason in the menu as a quiet header, on a phone too` | 0 |
| `frontend/e2e/reco.spec.ts:126` | `Not interested on a More like this poster hides it and sends the title` | 0 |
| `frontend/e2e/reco.spec.ts:241` | `rows say why, the request names the channel, and Don't recommend hides the row with Undo` | 0 |
| `frontend/e2e/reco.spec.ts:283` | `a channel with no suppression gets the plain confirmation` | 0 |
| `frontend/e2e/settings_visual_polish.spec.ts:13` | `capture every settings page in` | 0 |
| `frontend/e2e/settings_visual_views.spec.ts:64` | `capture bespoke settings views at` | 0 |
| `frontend/e2e/v1_admin_import_ui.spec.ts:35` | `import wizard, running and partial` | 0 |
| `frontend/e2e/v1_admin_import_ui.spec.ts:67` | `import partial with entries` | 0 |
| `frontend/e2e/v1_admin_import_ui.spec.ts:85` | `a scan that would mark many files missing waits for confirmation` | 0 |
| `frontend/e2e/v1_admin_media.spec.ts:43` | `TMDB key, Jellyfin toggle and Infuse instructions` | 0 |
| `frontend/e2e/v1_admin_media.spec.ts:71` | `an agent token is shown once, then revoked` | 0 |
| `frontend/e2e/v1_admin_members_ui.spec.ts:7` | `members list opens a member page whose edits save in one PUT` | 0 |
| `frontend/e2e/v1_admin_members_ui.spec.ts:48` | `invite dialog sends, and shows the link when email is not set up` | 0 |
| `frontend/e2e/v1_admin_metrics.spec.ts:32` | `test_admin_overview` | 0 |
| `frontend/e2e/v1_admin_storage_ui.spec.ts:33` | `test_storage_mobile_forms` | 0 |
| `frontend/e2e/v1_admin_storage_ui.spec.ts:66` | `storage rules load failure is actionable` | 0 |
| `frontend/e2e/v1_admin_storage_ui.spec.ts:77` | `adding an external root offers Import now, shared with the household by default` | 0 |
| `frontend/e2e/v1_admin_tasks_ai.spec.ts:46` | `test_admin_tasks_and_ai` | 0 |
| `frontend/e2e/v1_admin_tasks_ai.spec.ts:76` | `test_generate_transcript` | 0 |
| `frontend/e2e/v1_chat.spec.ts:33` | `test_replay_chat_is_read_only_source_chat` | 0 |
| `frontend/e2e/v1_chat.spec.ts:42` | `test_twitch_live_chat_explained_without_sign_in` | 0 |
| `frontend/e2e/v1_collections_ui.spec.ts:113` | `test_collection_save_to_vault_is_explicit_and_separate_from_watch` | 0 |
| `frontend/e2e/v1_collections_ui.spec.ts:135` | `test_collection_play_all_pushes_to_watch_queue` | 0 |
| `frontend/e2e/v1_collections_ui.spec.ts:144` | `test_collection_visual_mixed_source` | 0 |
| `frontend/e2e/v1_diagnostics.spec.ts:45` | `test_admin_diagnostics_recommendations` | 0 |
| `frontend/e2e/v1_discover_ui.spec.ts:38` | `test_discover_stale_response_discarded: a slow earlier query never replaces the newer one` | 0 |
| `frontend/e2e/v1_discover_ui.spec.ts:48` | `test_discover_visual_focus` | 0 |
| `frontend/e2e/v1_downloads_ui.spec.ts:54` | `test_downloads_visual` | 0 |
| `frontend/e2e/v1_history.spec.ts:31` | `test_history_storage_failure_search_works: a failed history write never blocks the search` | 0 |
| `frontend/e2e/v1_home.spec.ts:42` | `a fresh member sees one start card and no empty shelves` | 0 |
| `frontend/e2e/v1_visual_catalogue.spec.ts:325` | `the catalogue lists every 13.1 state` | 0 |
| `frontend/e2e/v1_visual_catalogue.spec.ts:337` | `catalogue ${scheme} ${viewport.name}`; runtime `catalogue dark desktop` | 0 template; 1 verification-only PR #61 |
| `frontend/e2e/v1_history.spec.ts:96` | `test_history_suggestions_visual` | 0 |
| `frontend/e2e/v1_household_notes.spec.ts:68` | `test_notes_visual` | 0 |
| `frontend/e2e/v1_idea_graph.spec.ts:44` | `test_idea_graph_empty` | 0 |
| `frontend/e2e/v1_library_ui.spec.ts:86` | `test_library_views_delete_and_undo` | 0 |
| `frontend/e2e/v1_moments.spec.ts:45` | `test_moment_create_seek_roundtrip` | 0 |
| `frontend/e2e/v1_moments.spec.ts:59` | `test_moments_themes` | 0 |
| `frontend/e2e/v1_moments.spec.ts:67` | `test_moments_empty` | 0 |
| `frontend/e2e/v1_provenance.spec.ts:30` | `test_provenance_panel` | 0 |
| `frontend/e2e/v1_provenance.spec.ts:39` | `test_provenance_error_state` | 0 |
| `frontend/e2e/v1_recording.spec.ts:48` | `test_recording_states_live_and_partial` | 0 |
| `frontend/e2e/v1_recording.spec.ts:79` | `test_recording_retention_admin` | 0 |
| `frontend/e2e/v1_settings_ai.spec.ts:61` | `turning on Semantic search with no model downloads it, and it works after Save (success criterion 1)` | 0 |
| `frontend/e2e/v1_settings_ai.spec.ts:99` | `a corrupted download is never used: the row says why and Retry downloads again (success criterion 5)` | 0 |
| `frontend/e2e/v1_summary_ui.spec.ts:46` | `test_summary_generating` | 0 |
| `frontend/e2e/v1_transcript_ui.spec.ts:44` | `test_transcript_search_state` | 0 |
| `frontend/e2e/watch_quality.spec.ts:158` | `capture episode` | 0 |

The machine-readable search result artifact is `/tmp/lumina-playwright-move-delete-github-search.json`; the history map now records the scope, counts, methodology, and artifact location. No result supplied an existing-test-failure → application-code-fix chain, so no newly discovered catcher changes any move/delete verdict.
