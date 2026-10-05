# ADR 0004: Use a durable idempotent acquisition outbox

- Status: Accepted
- Date: 2026-07-16

## Context

Acquisition dispatch crosses a database and an external job queue. A process can stop after creating a job but before recording its identifier, and two processes can select the same source concurrently.

## Decision

Each Acquisition batch entry is a durable outbox item and its identifier is the dispatcher idempotency key. Before dispatch, Lumina transactionally claims a database-unique Household-member and source-identity reservation. The dispatcher must return the same Download job for repeated keys. Definite, explicitly redacted failures release the reservation; uncertain failures retain it and are reconciled by repeating the idempotent dispatch. Completed reservations remain as member-scoped duplicate history, while failed or cancelled acquisitions require an explicit retry that creates a new batch entry.

Job and Library-item links use uniqueness constraints and service validation rather than new foreign keys in this phase. Existing persistence models do not yet define deletion cascades, and introducing partial foreign-key policy would make recovery and later lifecycle design less predictable.

The JobManager adapter derives a deterministic job identifier from the entry key and first persists the job in an inert `staged` state. Only after the Acquisition job-output link commits may the adapter atomically claim `staged` to `queued`; only the claim winner publishes and enqueues the job. Startup reconciliation repairs dispatching entries and linked staged jobs before ordinary pending-job recovery, which supplies the single in-memory enqueue after a crash. Linked-job identity is cached so ordinary Download jobs do not poll acquisition storage during progress callbacks.

## Consequences

- Restarting reconciliation cannot intentionally create a second external job for the same entry.
- Concurrent sessions cannot both acquire the same member/source reservation.
- Dispatcher adapters must support durable idempotency-key lookup.
- A staged job cannot run before its durable Acquisition job-output link exists.
- Orphan cleanup and foreign-key cascade policy remain explicit future integration work.
