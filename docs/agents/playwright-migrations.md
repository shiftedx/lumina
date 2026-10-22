# Playwright migrations to cheaper public seams

## Connected-app one-time token

Original: `frontend/e2e/v1_admin_media.spec.ts`, “an agent token is shown once,
then revoked”. Replacement: `frontend/src/v1_admin_media.test.tsx`, “clears a
newly issued agent token when its app is revoked”. It drives the real
`ConnectedApps` component through token creation, focused display and revocation;
asserts token removal and absence from localStorage.

Green: `mise exec -- npm --prefix frontend test -- v1_admin_media.test.tsx`
passed 9/9. Red: in an isolated scratch worktree, disabled only
`if (created?.app.id === app.id) setCreated(null)` in
`frontend/src/features/settings/ConnectedApps.tsx`; the replacement failed
because the revoked token textbox stayed visible. Restored the behavior and
9/9 passed again. Main product source was never mutated. The browser original
remains until its baseline and history verdict are complete.
