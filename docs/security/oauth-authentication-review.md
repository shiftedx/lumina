# OAuth and authentication validation

Validated on the `codex/oauth-support` branch. External login is opt-in: `LUMINA_OAUTH_PROVIDERS` defaults to an empty array. Production configuration and deployment were not changed.

## Scope

Optional browser login supports Google, Apple, GitLab, GitHub, Authentik, Authelia, Keycloak, Pocket ID and compatible OIDC providers. Operators explicitly bind stable provider subjects to existing immutable member IDs. Local two-step verification and normal session controls remain authoritative.

An independent Astra review at medium reasoning covered the new provider flow and the existing authentication pipeline. Credential-management and request-origin boundaries were strengthened and regression checks were added. Detailed security findings are retained privately in accordance with [SECURITY.md](../../SECURITY.md).

## Verification

`mise exec -- make check` completed successfully with exit code 0:

- Backend: 5,395 passed, 11 retained prerequisite skips.
- Frontend: 2,691 passed across 311 files.
- Browser full tier: all 656 passed, with no skipped or flaky cases; 1,087.894 seconds.
- Bootstrap/runner checks, TypeScript/e2e typechecking, production build and style budgets passed.
- `git diff --check` passed. The patched Python lock audit and full npm audit each reported no known vulnerabilities.

A pre-existing Photos test sampled viewport overflow before responsive reflow completed. A targeted observation measured 43 pixels immediately after resize and zero after two animation frames. The existing test now polls the same zero-overflow requirement. No Photos application code or CSS changed; the corrected journey passed in the final full gate.

## Limits

Validation used synthetic identities, signed test tokens, intercepted provider HTTP and real browser redirects to a synthetic provider. No real provider credentials or live provider registrations were configured. This is a bounded authentication review, not a claim that the entire application is vulnerability-free.

OAuth authenticates browser sessions. Native and Jellyfin clients retain their existing credential/device-token flows. Lumina logout ends its local session; provider logout and backchannel revocation are not implemented. Removing a provider mapping blocks future logins; revoke existing local sessions separately when immediate removal is needed.

The [operator guide](../../docker/README.md#external-login-with-oauth--openid-connect-optional) documents provider registration, exact callbacks, member bindings, Apple key handling and recovery through local passwords.
