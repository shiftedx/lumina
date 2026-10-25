# ADR 0033: Optional external identity for local members

- Status: Accepted
- Date: 2026-10-24

## Context

Households may already use a self-hosted identity provider or a hosted social login. Lumina's default remains local passwords. External identity must not create an administrator, claim an existing account by matching an email address, or replace session revocation and two-step verification.

## Decision

Operators configure a JSON array in `LUMINA_OAUTH_PROVIDERS`; an empty array disables external login. OIDC discovery supports Authentik, Authelia, Keycloak, Pocket ID, GitLab, Google and compatible providers. GitHub uses its OAuth authorization-code flow and authenticated numeric user ID. Apple uses form-post callbacks and either an operator-managed client-secret JWT or a five-minute ES256 JWT generated from the configured signing key.

Each provider explicitly binds its stable subject to an existing immutable Lumina member ID. Email, display name, username and group claims do not grant access or roles. Bootstrap, invitations, deactivation and role management remain local. No schema migration is needed: mappings are deployment configuration, not new account records.

Authorization-code login uses short-lived single-use server-side state, a separate HttpOnly browser-binding cookie (`__Host-` prefixed on HTTPS to prevent sibling-domain cookie injection), nonce and S256 PKCE (except Apple, whose web flow does not advertise PKCE). ID tokens require asymmetric signatures, exact issuer and audience, nonce and valid timestamps. Metadata and keys are fetched over verified HTTPS, without redirects or environment proxy inheritance. Operator-configured identity endpoints can resolve to private addresses; public input cannot choose them.

The sole exception to the browser Origin gate is a configured Apple's exact POST callback. Its state and separate Secure SameSite=None binding cookie are required before token exchange. Every other mutation retains the existing origin rules. Provider failures return a generic message; tokens, codes and provider error descriptions never enter application logs or frontend URLs.

Verified identity passes through Lumina's existing two-step challenge when enabled, then the normal session issuance and optional device ring. Tokens are discarded after validation. Logout revokes the Lumina session; it does not sign the browser out of the identity provider. IdP deactivation or mapping removal stops future logins; already issued Lumina sessions retain their normal lifetime and can be revoked locally.

## Consequences

- Existing installations need no changes, credentials or identity-provider connectivity.
- Multiple providers may map to the same local member. Operators obtain subject IDs from their provider and member IDs from the authenticated member API.
- Local credentials remain available for recovery and existing native/third-party clients. This change adds browser login, not a native OAuth redirect or device-authorization protocol.
- Pending sign-ins expire after five minutes, are bounded in memory and disappear on restart, matching the single-process deployment contract.
- Authlib and its JOSE implementation handle protocol/JWT validation; Lumina owns only configuration, browser binding and local account authorization.
