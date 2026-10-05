# ADR 0006: Keep single-process SQLite as the default persistence engine

- Status: Accepted
- Date: 2026-07-18

## Context

Lumina persists metadata, operational history, search text, and concurrency state: Library items,
Download jobs, Acquisition batches and their reservations, Source automations and Automation runs,
Household collections, playback progress, sessions, and settings. Acquired
media bytes live on the filesystem under `app-data/library/<member-id>/`, not in the database, so
the persistence workload is metadata, history, search, and write concurrency rather than bulk
storage. Database sizing therefore follows one household's activity, and the engine question is a
topology and contention question, not a row-count question.

The supported topology is a single process on a single host. [ADR 0001](0001-loopback-first-deployment.md)
fixes the deployment at "exactly one TLS-terminating reverse proxy in front of exactly one Lumina
process," published by default only on loopback. Lumina provides no high availability, SQLite and
local files provide no built-in replication or failover, and concurrent processes must not share the
same application data. The multi-process and high-availability conditions that would motivate a client/server database are
therefore explicitly declined, not merely unimplemented.

The current engine is SQLite in WAL mode with a single in-process writer. `backend/app/db.py`
serializes writes through one process-wide lock and a bounded acquire timeout. The schema is created
from the models and stamped with a `PRAGMA user_version`; a database stamped with any other version
is refused at startup (there is no upgrade path). Recovery is file-level: the single-file property
underpins the recovery generation defined in the Docker operator guide: a stopped-service database
plus media and cache filesystem, the separate external key, the recorded shared-cookie state, the
immutable image, and the exact release revision, all restored together as one generation.

The workload had known large-library hot spots addressed by single-process hardening
rather than by an engine change: `backend/app/services/library.py` lists and searches items by
loading and scoring rows in the application; `backend/app/services/job_manager.py` commits Download
job progress under the single writer; and `backend/app/services/source_automation.py` selects and
advances due Source automations. Concurrency correctness is a mix of database-enforced invariants
and single-writer serialization. Acquisition reservations use database uniqueness constraints and
the Acquisition outbox claims a staged Download job with an atomic conditional update
(`backend/app/services/acquisition_job_manager.py`), both of which are already engine-portable. In
contrast, the per-member remote playback retention cap in `backend/app/services/remote_playback.py`
relies on a row lock that is a no-op under SQLite; it is correct today only because a single writer
serializes it.

That same `remote_playback.py` already imports both the SQLite and PostgreSQL insert dialects and
branches its upsert accordingly. This demonstrates that a portable write path is achievable, but it
is one portable island in an otherwise SQLite-shaped system, and it is not itself portable yet: its
`source_identity` column is an indexed `String(4096)` used as the conflict target, whose long values
exceed PostgreSQL's B-tree index-tuple limit and would fail to index. Reducing that column to a
fixed-width hash key, matching the existing 64-character `source_identity` on Acquisition batch
entries, is required for correctness independent of engine.

## Decision

Lumina keeps single-process, single-writer SQLite as the default and only supported persistence
engine for its declared loopback-first, one-process topology. The engine decision follows topology,
not database size. The single write-transaction seam that owns short write transactions with bounded
admission is designated the durable database-adapter boundary, so a future engine change is scoped to
that seam rather than diffused across services.

This decision holds on the precondition that, after the single-process hardening ( the
write-transaction seam with short transactions and bounded admission, keyset pagination with
composite indexes for bounded reads, indexed full-text search, acquisition-progress write
coalescing, and Automation run leases), the supported single-process mixed workload meets the
declared latency and throughput budgets at the declared library tiers.

Measured (2026-07-19, Apple Silicon reference hardware, file-backed WAL database; the performance
rig and its evidence artifacts were removed from the repository after this decision recorded):
a 30-minute mixed-workload soak at the 10,000-item tier (42,775
requests: concurrent member list/search traffic, playback checkpoint autosaves, settings writes,
session refresh, and artwork requests; the acquisition lane exercised request handling only, because
fixture-only mode rejects its sources at the public-source policy, so download-progress writes are
covered by unit-level regression tests and by deployed-instance stress rather than this soak)
produced zero database-lock errors, zero 503s, and zero writer-admission timeouts across all nine
named write-transaction sites. Interactive write endpoints held p99 between 35 ms and 118 ms (login
61 ms) against the ≤250 ms budget; the worst writer-slot wait observed anywhere was 46 ms against
the 5-second admission bound; maintenance sweep batches peaked at 131 ms of self-time while imposing
~0 ms wait on interactive writers; the WAL held steady near 34 MB under checkpointing. Warm
sequential read probes recorded p95 of 10.6 ms (10,000 items) and 24.2 ms (50,000 items) for the
first Library page. The precondition holds.

### Revisit triggers

Reopen this decision, and evaluate PostgreSQL against the migration scope in the alternatives below,
when any of the following holds:

- The product explicitly commits to more than one Lumina process, more than one host, or high
  availability. This condition is currently declined by [ADR 0001](0001-loopback-first-deployment.md).
- Measured write-transaction admission or read latency stays outside the declared budget at the
  declared library tiers after the single-process hardening lands.
  Measured: no write or paged-read metric exceeded budget at any tier. The one accepted miss is
  application-level, not engine-level: combined lexical-plus-semantic search p95 reached 154 ms at
  the 50,000-item stress tier against a 120 ms budget (the lexical path alone met budget at 69 ms
  p50; the delta is bounded semantic re-ranking CPU, which an engine change would not remove). It
  was accepted with recorded rationale and does not trigger this clause.
- A required sustained write rate exceeds what one serialized writer can admit within budget.
- A replication, failover, or continuous-availability requirement lands that a filesystem plus
  single-file backup cannot satisfy.

## Consequences

- The supported deployment stays a single container with no additional database service, port,
  credential, health dependency, or application-versus-database image skew, preserving the
  loopback-first boundary and the read-only-root, capability-dropped runtime.
- The recovery generation keeps depending on SQLite's single-file, stoppable, atomically replaceable,
  integrity-checkable properties. The backup and restore contract in the Docker operator guide
  remains valid unchanged.
- The single write-transaction seam is the adapter boundary. New write paths must route through it so
  that a future engine port is a bounded change, and so that any invariant defended by a row lock is
  written to remain correct under both a real lock and single-writer serialization.
- Concurrency correctness continues to combine database-enforced constraints with single-writer
  serialization. Invariants that lack a database constraint (notably the remote playback retention
  cap) remain correct only under one writer, and this is an accepted, documented property of the
  supported topology rather than an engine-agnostic guarantee.
- The indexed `String(4096)` `source_identity` on remote playback progress was reduced to a
  fixed-width hash key (`source_identity_key`) for correctness regardless of this decision, removing the practical portability blocker in the dual-dialect `remote_playback.py`
  upsert.
- Large-library scaling is delivered by bounded reads, indexed search, write coalescing, and run
  leases within SQLite. Measured at the declared tiers (warm probes recorded at decision time): paged Library reads p95 10.6 ms and combined search p95 100.4 ms at 10,000
  items; at the 50,000-item stress tier, paged Library reads p95 24.2 ms and combined search p95
  154 ms (accepted with rationale, above). Interactive writes held p99 35–118 ms under a 30-minute
  mixed-workload soak with zero lock errors or 503s. The precondition holds; the engine stays.

## Alternatives considered

### Adopt PostgreSQL now

The strongest case for adopting a client/server database:

- **Write concurrency ceiling.** Every write serializes through one in-process writer; a
  write-heavy mixed load of Download job progress, Automation runs, and playback checkpoints contends
  for a single slot, whereas multi-version concurrency lets writers to different rows proceed
  together.
- **Only path to multiple processes or hosts.** SQLite cannot safely share one data directory across
  processes, so any future multi-process, multi-host, or high-availability topology requires a
  client/server engine.
- **Server-side search and indexing at scale.** A full-text index with server-side ranking and richer
  index types can outscale application-side scoring and, at very large libraries, SQLite full-text
  search, while serving concurrent readers without checkpoint stalls.
- **Real schema evolution.** Transactional column alteration, partial and expression indexes, and
  versioned migrations would replace the current `PRAGMA user_version` stamp.
- **Native types and enforceable constraints.** Binary JSON with operators and indexes, real
  booleans, timezone-aware timestamps, and functional-unique and check constraints could move
  invariants that today live only in application code into the database.
- **Operational maturity.** Online backup, point-in-time recovery, replication, pooling, and
  first-class observability provide a genuine high-availability story if one is ever required.

Rejected for now because the single decisive precondition: an explicit move to multiple
processes, hosts, or high availability, is declined by the supported topology, and because adopting
a client/server engine is not a connection-string change but a program of work:

- **Versioned migrations.** Replace the `PRAGMA user_version` stamp in `backend/app/db.py` with a
  versioned migration tool and history.
- **Deployment.** Add a database service and profile, a database credential lifecycle, and startup
  health ordering, all while preserving the loopback-first boundary
  so the database is never independently exposed.
- **Dialect correctness.** Reduce the indexed `String(4096)` `source_identity` to a fixed-width hash
  before it can be an index or conflict target; move JSON-equality comparisons in migration and
  settings-normalization logic to binary JSON or text casts because the plain JSON type has no
  equality operator; replace SQLite integer-boolean literals in data-definition statements; verify
  naive-timestamp and null-ordering semantics; and validate every conflict target against a
  buildable unique index.
- **Durable claims under multi-version concurrency.** Re-establish, with functional unique indexes or
  explicit locking, the invariants currently held by the single writer (such as the retention cap)
  and confirm that Automation run leases and the outbox claim,
  which already use atomic conditional updates, serialize correctly. Note that process-local state
  such as login rate limits and the in-memory Download-job queue is not made multi-process-safe by a
  database change and would need separate coordination.
- **Recovery redesign.** Replace the single-file backup and atomic-replace recovery with a
  dump-and-restore or base-backup flow and rebuild the recovery-generation contract so a stopped
  database dump, the media and cache filesystem, the external key, the recorded shared-cookie state, the immutable image, and the exact revision remain one restorable
  generation. A disposable test instance is rebuilt clean; no converter migrates existing installed
  data.

### Keep single-process SQLite

The strongest case for retaining the current engine:

- **Topology fit.** Exactly one process on one host is the deliberately supported design, and the
  decisive PostgreSQL condition is explicitly declined rather than merely absent; a client/server
  database to serve a single process adds a moving part with no matching requirement.
- **Workload shape.** Media bytes live on the filesystem, so the database carries metadata, history,
  and search, and the real question is write-admission latency under mixed load, exactly what the
  single-process hardening targets, to be judged on measured budgets rather than engine preference.
- **Recovery contract.** The single-file, stoppable, atomically replaceable, integrity-checkable
  database is a tested reliability asset; a client/server engine would force a full recovery redesign
  with new partial-failure modes for no topology benefit today.
- **Operational simplicity and attack surface.** No extra service, port, credential, or health
  dependency, and no application-versus-database image skew keeps the self-hosted deployment simple
  and the loopback-first boundary intact for operators who restore a stopped-service backup.
- **Concurrency correctness is already well placed.** The invariants that must be portable are
  already database-enforced or use atomic conditional updates, and the remainder are cheaply and
  correctly serialized by the single writer; a client/server engine would require re-earning those
  invariants in new code.
- **Preserved optionality.** WAL mode serves concurrent readers alongside the single writer, and the
  write-transaction seam keeps a clean adapter boundary, so retaining SQLite now forfeits no future
  ability to change engines if a real trigger lands.
