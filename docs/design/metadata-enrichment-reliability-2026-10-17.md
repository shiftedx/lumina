# Metadata enrichment after incremental imports

## Confirmed failure

New episodes were registered and playable but retained filename-derived names without descriptions or stills. The example contained eight episodes: the first had metadata, while the seven imported after the series' last refresh did not. A read-only provider check confirmed that names, descriptions and stills existed for all eight. The series was already matched and its next refresh remained days away.

The scanner queued metadata only when creating a series or movie. Adding episodes or seasons to an existing series did not wake that job. Episodes are enriched through their parent series, rather than being independent metadata jobs. The regular seven-day returning-series or ninety-day ended-series cadence therefore left new children bare. This is separate from missing-file discovery.

Private media titles, paths, hostnames and credentials are excluded from this record.

## Working-source corrections

- Creating a season or episode now wakes its owning series' existing durable `metadata_due_at` job. Unchanged scans do not keep requeueing it. Item locks and deliberate owner no-match choices remain respected.
- A metadata fetch snapshots the queue timestamp. Successful completion, unmatched retry and provider-failure backoff only replace that timestamp if it is unchanged. An import or explicit refresh arriving during the network call therefore survives and gets another pass.
- Filename-only episode labels omit recognizable bracketed release/quality suffixes, including nested custom-format release tags. Ordinary bracketed title text and NFO/user values remain intact. There is no new dependency, schema or naming identity change.

Public import regression: an already existing series with a ninety-day due date gains episodes in its existing and new seasons. It was red before the correction and green afterward; locked/no-match controls and an unchanged rescan are checked in the same workflow. Provider-completion and failure races, plus filename fallback through the public library API, were also red before their corrections. The focused metadata, import, sidecar, incremental-reconciliation and episode-move suite passed **93 tests**.

## Production recovery

Normal durable metadata refreshes were queued for 22 already-matched series with 208 bare episodes. Existing matches were retained; fields still pass through the ordinary `user > nfo > tmdb > path` precedence.

The worker enriched 206 episodes. All eight example episodes now have provider names, descriptions and artwork references. All eight stills were subsequently loaded through the production `title_image_bytes` path with the configured artwork service: nonempty `image/jpeg` responses, approximately 24–48 KB each.

Two episodes remain without provider metadata because their local season is absent from the provider's season list and its season endpoint returns not-found. Their technical filename suffixes were cleaned using the tested fallback rule. The installed scanner later restored those path-sourced labels, so the final recovery uses undoable name-only editor overrides to keep them clean until the prevention code is deployed. Their numbering, provider identity and availability were retained. No artwork or descriptions were invented. A follow-up aggregate check found zero remaining filename-sourced episode names matching the observed release-tag patterns, and zero matched movies with the same entirely bare metadata pattern.

A broader source-aware inventory found 61 older NFO-sourced names containing release tags or technical quality labels. These predated the incremental-import failure. Their NFO priority correctly blocked automatic provider-name replacement. Canonical names were available for 59 under their existing provider identities and saved episode ordering. Two tag-only names had no usable canonical title and received the factual `Episode N` fallback using their existing episode numbers. These name-only corrections used `metadata_editor.save_edits`, optimistic base-value checks and undoable history batches. They are explicit user-sourced corrections, so stale NFO labels cannot overwrite them. Existing user-sourced names and locked items were excluded; no NFO file, provider identity, numbering, artwork or item lock changed. All 61 corrected NFO names and the two filename fallbacks were verified persisted, with zero conflicts. A final aggregate check found zero episode names matching the observed release/quality patterns across all field sources.

This recovery updated metadata and queue rows in the installed production version. No application image or source file was deployed and no restart was initiated by this work. A later read-only inspection observed one container restart with the same installed image; its cause was not established. Metadata corrections and their edit history remained present, and the user confirmed the app opened normally. Final management verification used the existing host control connection after direct management access timed out. The prevention changes above remain in the working source for deployment. The earlier TV watch/safety-scan configuration remains in place.

## Verification evidence

Red/focused logs, the source identity and full project check output are retained under `output/metadata-enrichment-2026-10-17/`. The full `mise exec -- make check` gate completed with exit status 0, serialized under the performance lock. Artwork verification uses the actual service boundary; it does not claim an authenticated browser visual check.

The full backend stage passed **5,068 tests / 11 existing skips** in 625.0 seconds, including the unchanged watch-baseline writer-duration assertion. Frontend source/e2e typechecks, the production build/styles and **2,531 frontend tests** also passed. The real-stack browser phase passed **47 tests / 4 existing skips** in 8.0 minutes. The mocked browser phase passed **601 tests / 2 existing skips** in 8.7 minutes. The source identity remained unchanged throughout the gate, and `git diff --check` passed. These are verification results on the shared Mac, not a claim of uncontended performance gains.
