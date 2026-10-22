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
