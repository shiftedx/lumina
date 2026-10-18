# ADR 0029: Check published releases without installing updates

- Status: Accepted
- Date: 2026-10-18

## Context

Server administrators need to discover published Lumina updates from Manage Server and receive an update notice. The existing release workflow publishes versioned container images; installing an image remains an operator action with data backups and retained rollback images.

## Decision

Use the fixed public GitHub latest-release endpoint for this repository. Accept only stable, published releases with a canonical `vMAJOR.MINOR.PATCH` tag and a matching repository release link. Compare integer version components with the installed `APP_VERSION`; source revisions and private deployments are not public releases. Bound response bytes and request duration, disable redirects and environment proxy credentials, and expose only validated release details and fixed failure messages.

Persist the last successful result, last attempt, check preferences and email enqueue marker in one additive `AppSettings.update_settings` JSON column (schema 30). Use the existing maintenance lifecycle to dispatch a single bounded worker. Default to daily automatic checks, retry failures after an hour, and share a one-minute manual cooldown. Network access and mail delivery happen outside database write transactions. A restart drops transient checking state while retaining cached results and notification deduplication.

Admin-only routes expose cached status, manual checking and preferences. A persistent Manage Server overview notice reports newer versions; it needs no per-member notification table. Email is optional and off by default. Reuse the existing SMTP transport and active administrators' notification addresses. Durably record the highest queued semantic version before best-effort email delivery, choosing at-most-once enqueue over duplicate mail after restarts or an upstream release withdrawal. A crash or SMTP failure can lose mail; the in-app notice remains.

## Consequences

No release, image download, package installation or server restart follows from checking. Provider outages retain the last usable result and cannot establish that a server is current. The checker never uploads household media or credentials. Operators can disable scheduled checks and still check manually. Rollback to schema 29 keeps the additive column and stamps the older schema version; older code ignores this state.
