# Playwright migrations to cheaper public seams

## Connected-app one-time token

Original: `frontend/e2e/v1_admin_media.spec.ts`, “an agent token is shown once,
then revoked”. Replacement: `frontend/src/v1_admin_media.test.tsx`, “clears a
newly issued agent token when its app is revoked”. It drives the real
`ConnectedApps` component through token creation, focused display and revocation;
asserts token removal and absence from localStorage.

Green: `mise exec -- npm --prefix frontend test -- v1_admin_media.test.tsx`
passed 10/10. Red: in an isolated scratch worktree, disabled only
`if (created?.app.id === app.id) setCreated(null)` in
`frontend/src/features/settings/ConnectedApps.tsx`; the replacement failed
because the revoked token textbox stayed visible. Restored the behavior and
10/10 passed again. Main product source was never mutated. The companion contract “dismisses a newly issued agent token before its app is
revoked” preserves the original Done → hidden token → revoke flow. Disabling
only Done's `setCreated(null)` made that contract fail; restoring it returned
10/10 passes. The browser original was removed after its baseline passed and verdict was recorded.

## Initial storage-rules load failure

Original: `frontend/e2e/v1_admin_storage_ui.spec.ts`, “storage rules load failure
is actionable”. Replacement: `frontend/src/v1_admin_storage_ui.test.tsx`,
“shows an actionable initial routing-rules load failure and reloads”. Rejecting
initial `getStorageRules` produces its alert and Reload rules action, and the
retry renders Rule 1. This is distinct from an existing save-conflict test.
Green: focused file passed 13/13. In the scratch worktree, suppressing only the
production initial-load catch error state made this contract fail on missing
`role=alert`; restoring it returned 13/13 passes.

## Rate-limited login copy

Original: `frontend/e2e/auth.spec.ts`, “a rate-limited sign-in says to wait a
minute”. Replacement: `frontend/src/authLogin.test.tsx`, “tells a rate-limited
member to wait a minute”. It renders real `LuminaApp` login wiring, types and
submits credentials, then receives a real `ApiRequestError` with status 429.
Green: focused test 1/1 and typecheck passed. An isolated scratch mutation of
only the production 429 message to “Unable to sign in” made the new contract
fail on the missing expected copy; restoring it returned 1/1. Scratch removed.

## Empty moments timeline

Original: `frontend/e2e/v1_moments.spec.ts`, `test_moments_empty`.
Replacement: `frontend/src/v1_moments.test.tsx`, “test_moments_empty: an empty
timeline explains how to add its first moment”. It renders the public MomentsPanel
with no moments and pins the count and actionable empty-state copy. Removing
only “No moments yet. ” from production made the contract fail; restoration
passed. The companion existing public roundtrip contract covers create payload,
seek callback and visibility without a browser mock.

## Provenance request failure

Original: `frontend/e2e/v1_provenance.spec.ts`, `test_provenance_error_state`.
Replacement: `frontend/src/v1_provenance.test.tsx`, “test_provenance_error_state:
a failed source request shows the durable recovery state”. A rejected public
getProvenance request ends loading and renders the recovery message. Replacing
the production failure handler's `setData(null)` with a no-op made the contract
fail because loading never ended; restoration passed. Both Moments and Provenance
files passed together, 6/6. Mutations ran in a separate scratch worktree and
were restored; product source in the audit branch was unchanged.

## Read-only replay chat

Original: `frontend/e2e/v1_chat.spec.ts`, `test_replay_chat_is_read_only_source_chat`.
Existing `frontend/src/v1_chat.test.tsx :: test_chat_payload_xss_safe` already
pins raw-text handling and public read-only wording. Extended that public
component contract to require no textbox, preserving the original no-provider-
composer assertion (the search field remains a searchbox). In the scratch
worktree, inserting a provider input into production ChatReplayRail made only
that test fail (1 failed, 3 passed); restoring production passed 4/4. The
WatchChatSeam public test covers replay selection and the retained browser
page-error journey still visits both source-chat routes.

## Search with failed history persistence

Original: `frontend/e2e/v1_history.spec.ts`, `test_history_storage_failure_search_works`.
Replacement: `frontend/src/LuminaKeyboard.test.tsx`, “a failed search-history
write never blocks the search or adds a recent item”. The real LuminaApp command
palette submits a query, renders results and offers no false recent-history row
when recordSearchHistory rejects. Making the history write blocking before
navigation in scratch production code made the contract fail on missing Results;
restoration passed 1/1.

## Live recording action and retention payload

Originals: `frontend/e2e/v1_recording.spec.ts`, `test_recording_states_live_and_partial`
and `test_recording_retention_admin`. Existing partial-result contracts remain;
`frontend/src/v1_recording.test.tsx` now also renders LiveRecordingControls in a
live state and requires Stop & save. Forcing the production active state false
made the new contract fail; restoration passed 1/1.

Extended `frontend/src/v1_settings_server.test.tsx`'s existing cleanup form
contract to edit and submit age 14 and size 500, and assert the public API payload
and saved status. Mutating production to send days as max_gb failed (14 instead of
500); restoration passed 1/1. All three final main-branch migration contracts
passed together, 3/3. Main product source was never mutated.

## Explore coordinator and pending-member interests

The prior CommandPalette lookup test does not exercise LuminaApp's paired
sourceSearch/searchLibrary coordinator. The stale Explore browser original was
restored during review until `frontend/src/LuminaKeyboard.test.tsx :: a late
Explore response never replaces the newer query` was green. The new public app
contract resolves a newer source query before the older one, then requires the
fresh result to remain and the stale result to stay absent. Removing the actual
exploreRequestRef guard in scratch made the stale result replace the fresh one;
restoring the guard passed. Only then was the browser original removed.

The three browser cases in `onboarding_interests_loading.spec.ts` also move to
public LuminaApp contracts in the same file: pending interests keep Continue
guarded, a failed load retries and restores the selected Film interest, and a
late initial response cannot overwrite the canonical Skip result. Independent
production mutations forced loading false, disconnected retry and removed Skip
invalidation. Each new contract failed for its intended behavior, then passed
after restoration. Full LuminaKeyboard passed 87/87; the four focused contracts
passed 4/4 and frontend/E2E typecheck passed. Scratch was restored and removed.
