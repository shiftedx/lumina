# ADR 0025: Observe arr inventory independently of Requests

- Status: Accepted
- Date: 2026-10-17

## Context

Sonarr and Radarr can manage titles added outside Lumina. Request rows describe household requests, so neither inventing request rows for upstream inventory nor stopping observation with the Requests switch can describe those libraries correctly.

## Decision

Keep the existing write-only connection configuration and request dispatch contract (ADR 0018). A separate polling observer reads every enabled connection even while Requests is disabled. Its additive tables hold upstream title identities, allowlisted observation records, and the last complete successful synchronization. A failed or incomplete read preserves that snapshot and marks it stale. Reconciliation replaces the complete per-server snapshot in one short write transaction after external calls finish; a configuration change invalidates an in-flight result.

Admin endpoints expose paginated sanitized inventory and operational observations. Upstream identities never establish availability. Member explanations require a currently visible registered Media title under the existing visibility predicate, and omit upstream identity, paths and diagnostics. Play continues through registered Library items. Mapped external folders can use the existing scoped import helper only after an owner has imported the root once.

Observation does not grant operational write permissions or change acquisition ownership. Sonarr/Radarr still select, download, import, rename and upgrade releases; new controls need their own durable operation/reconciliation contract.

## Consequences

Schema 23 adds disposable `arr_observation_states`, `arr_observed_items` and `arr_observed_records` tables. Rollback can preserve these tables and stamp schema 22; older releases ignore them. Existing credentials and request rows are retained. Mocks establish the API contract; installed-service versions require separate qualification.
