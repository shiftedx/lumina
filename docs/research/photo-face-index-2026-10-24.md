# Face-vector index decision: SQLite-authoritative FAISS HNSW

Status: implementation-backed primary-source review, 2026-10-24.

## Decision

Lumina pins **`faiss-cpu==1.15.1`** and uses CPU `IndexHNSWFlat` wrapped by
`IndexIDMap2`. SQLite remains authoritative for embeddings, ownership,
assignments, corrections, deletion, and a monotonic change journal. FAISS only
produces a bounded candidate set; Lumina rechecks current owner/pipeline/live-row
eligibility and reranks the returned vectors with exact float32 dot products before
the existing threshold and margin rules run.

This replaces the earlier conditional USearch recommendation. USearch 2.26.5 has
the smallest artifacts, but its open reachability report describes inserted nodes
that membership lookup can find while HNSW search cannot; the issue remains open
and is labelled for v3. Its open deletion report also documents retained
tombstone memory. Those are poor failure modes for a household identity index.
[USearch #735](https://github.com/unum-cloud/usearch/issues/735),
[USearch #726](https://github.com/unum-cloud/usearch/issues/726).

The choice is not a speedup promise. The repository benchmark must measure recall,
build/update/restart cost, p50/p95 candidate latency, and RSS with Lumina's own
128-float workload. ANN cannot prove a global nearest neighbor; measured
recall-at-candidate-count is still a release gate.

## Version, license, and deployment envelope

| Runtime | Version reviewed | Packaging and license | Decision |
| --- | --- | --- | --- |
| **FAISS CPU** | `faiss-cpu==1.15.1` | MIT. PyPI metadata requires Python >=3.10 and NumPy >=1.25. Released `abi3` wheels cover Linux x86-64/aarch64 and macOS arm64; the macOS arm64 wheel requires macOS 14. [PyPI release JSON](https://pypi.org/pypi/faiss-cpu/1.15.1/json), [license](https://github.com/facebookresearch/faiss/blob/v1.15.1/LICENSE) | Selected and locked. Local qualification succeeded on Python 3.12.14, NumPy 2.2.6, macOS arm64: import, HNSW construction, ID add, and search. Linux wheel presence is verified, but both Linux architectures still need locked-image smoke tests. |
| USearch | `usearch==2.26.5` | Apache-2.0. CPython 3.12 wheels cover the three target architectures and are materially smaller. [PyPI release JSON](https://pypi.org/pypi/usearch/2.26.5/json), [license](https://github.com/unum-cloud/usearch/blob/v2.26.5/LICENSE) | Rejected for this release because the open reachability and tombstone issues would require an additional correctness qualification beyond Lumina's normal ANN recall gate. |
| hnswlib | `hnswlib==0.8.0` | Apache-2.0. The latest stable PyPI release is source-only; no target wheels are published. [PyPI release JSON](https://pypi.org/pypi/hnswlib/0.8.0/json), [released API](https://github.com/nmslib/hnswlib/tree/v0.8.0) | Rejected: compiler, NumPy ABI, and multi-architecture build support would become Lumina deployment work without eliminating HNSW tombstone/rebuild concerns. |

The FAISS pin is deliberate. A later release must rerun platform, recall, snapshot,
and resource qualification rather than accepting an unbounded native dependency
upgrade.

## Why this FAISS shape

`IndexHNSWFlat(128, M, METRIC_INNER_PRODUCT)` stores normalized float32 vectors;
`IndexIDMap2` attaches Lumina's never-reused SQLite `INTEGER PRIMARY KEY
AUTOINCREMENT` vector-version IDs. FAISS documents HNSW as a graph index with
construction/search depth controls and notes that HNSW does not support vector
removal. [FAISS index guide](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes),
[special operations](https://github.com/facebookresearch/faiss/wiki/Special-operations-on-indexes).

Consequently, Lumina never treats mutation as in-place graph truth:

- an embedding or owner/pipeline change deactivates the old vector version and
  inserts a new integer-keyed version;
- assignment, photo live-state, face deletion, and vector changes append a SQL
  journal row through database triggers;
- replay adds newly eligible versions and treats invalidated graph members as
  tombstones; search temporarily overfetches every retained tombstone and raises
  HNSW search depth to match, then a measured stale fraction triggers a fresh
  graph generation. This avoids false no-match/margin results when stale vectors
  rank first, at the cost of increasingly expensive queries before compaction;
- immutable SQL vector versions and bounded journal pruning prevent indefinite
  retention of deleted biometric derivatives;
- only one latest live face per person (`updated_at DESC, id ASC`) enters the
  prototype graph, while automatic unassigned faces use a separate graph. A
  prolific person therefore cannot consume the whole candidate budget. The
  selector uses one indexed correlated `LIMIT 1` face probe per paged person,
  rather than ranking/scanning that person's entire face history;
- the in-process scope cache is physically separated by owner and pipeline
  signature. It retains up to 32 least-recently-used scopes under a configurable
  2 GiB soft estimate (16 KiB per scope plus 1,024 bytes per native entry), while
  always retaining the active scope if that scope alone is oversized. Eviction
  discards only a derived graph: SQLite plus the journal rebuilds it. This avoids
  a fixed two-owner cache turning an interleaved three-owner queue into a graph
  reload per photo. There is no post-search filtering of a global household graph.

FAISS states that concurrent CPU searches are supported but concurrent writes
require caller synchronization. Lumina serializes graph build/replay/search behind
the service lock and bounds OpenMP use rather than allowing an indexing burst to
claim every CPU. [FAISS thread-safety guidance](https://github.com/facebookresearch/faiss/wiki/Threads-and-asynchronous-calls).

## Durable restart and native-file safety

Rebuilding the HNSW graph for a million embeddings on every restart would move a
large part of bulk-settle cost into every process start, so the service writes
generation-specific checkpoints. Each checkpoint is written with restrictive
permissions, flushed, atomically renamed, and registered in SQLite with byte size,
SHA-256, owner, pipeline, journal watermark, and generation. Restart verifies the
resolved private path, regular-file type, maximum size, exact size, and digest
before deserialization, then reconstructs membership from current SQLite rows and
replays journal entries newer than the checkpoint.

Missing, corrupt, unsafe, or mismatched checkpoints are disposable: Lumina rebuilds
from SQLite and never repairs database truth from FAISS. Orphan temporary and
unregistered generation files are removed during bounded startup recovery.

When the face queue becomes idle, Lumina replays the resident scopes' final journal
tail and checkpoints them, so the last analyzed photo is not permanently one job
ahead of the restart artifact. Checkpoint storage is still an optional accelerator:
if writing both new graphs would cross the configured preview free-space floor, or
the file write fails, Lumina retains the prior checkpoint and SQL journal without
failing the already-durable face analysis. A later idle settle can retry. SQL or
manifest-transaction errors still surface; only derived-file pressure is degraded.
Long builds/replays check the face worker's shutdown event between bounded SQL and
native-add pages; an interrupted replacement is never published over the prior
valid resident generation.

This validation is mandatory because FAISS explicitly warns that `read_index`
does no file validation and a malicious file can cause out-of-memory behavior or
code execution. [FAISS index-I/O warning](https://github.com/facebookresearch/faiss/wiki/Index-IO,-cloning-and-hyper-parameter-tuning).
Lumina's checksum makes the expected generated bytes tamper-evident; it does not
make an arbitrary native file safe. The artifact directory therefore must remain
private to the service. A supervised parser process remains the stronger future
hardening if the data directory is placed in a less trusted boundary.

## Query and correction contract

For every new embedding Lumina:

1. normalizes and validates exactly 128 finite float32 values;
2. searches prototype and automatic-unassigned graphs independently with bounded
   candidate counts;
3. looks up only those returned integer vector IDs in SQLite, requiring the same
   owner and pipeline, a live photo, an active vector version, and current category
   membership;
4. exactly reranks the surviving vectors with normalized float32 dot products and
   deterministic ID tie-breaking; and
5. passes face IDs—not embeddings or ANN scores—into the existing short writer,
   which rechecks current rows before applying calibrated threshold/margin rules.

Manual reassignment, photo deletion/missing state, reanalysis, and owner erasure
all enter the same trigger journal. Owner erasure deletes vector, journal, and
snapshot registration rows in the transaction, then drops the resident owner
scope and checkpoint files after commit. A rollback therefore cannot leave the
database claiming erasure while a serving graph was prematurely removed.
The erasure path acquires the index mutation lock before the database writer, so
a racing checkpoint cannot republish a deleted owner's graph after commit.

Unlike the former `updated_at`-limited SQL candidate query, prototype construction
pages through every live person by indexed person ID. This removes recency as a
correctness boundary. It does not guarantee HNSW recall; the exact benchmark and
fallback policy remain necessary.

At one million synthetic rows, SQLite initially chose the broad owner/pipeline
index before the bounded candidate IDs. The service now fixes join order so exact
rerank separates integer vector-key and face primary-key probes from an indexed
latest-live-prototype lookup; mutation replay likewise starts with face primary-key
probes followed by the `(face_id, active, id)` index. Keeping the prototype lookup
outside the candidate-row query matters for a person with a long face history:
SQLite could otherwise turn the correlated validation into a history scan even
when `EXPLAIN QUERY PLAN` named the composite index. Plan regressions plus a
2,000-versus-20,000-face VM-step regression protect these shapes; `IN (...)` or a
reported index name alone was not a sufficient planner guarantee on the measured
database.

## Capacity and qualification caveats

One million 128-dimensional float32 embeddings contain about 512 MB of vector
payload before HNSW links, ID maps, Python membership maps, allocator overhead,
SQLite cache, and checkpoint/build copies. One million photos may contain more or
fewer than one million faces. These are capacity facts, not an RSS prediction.

Before enabling the million-photo target by default, retain these gates:

- locked-image import/add/search/serialize/deserialize smoke tests on Linux x86-64
  and Linux aarch64; macOS older than 14 is outside the selected wheel envelope;
- fixed-seed 1M-vector measurement of build time, resident memory, checkpoint size,
  reload/replay time, update cost, p50/p95 query latency, and recall against exact
  float32 search, including deliberately old people and worst-case tombstone
  overfetch near the configured rebuild threshold;
- interruption tests around checkpoint write/register, checksum mismatch, stale
  journal replay, correction, deletion, owner erase, and stale-graph rebuild;
- a documented fail-closed behavior when native initialization or recall
  qualification fails. The old recent-only candidate cap must not silently return
  as if it represented complete history.

No part of this choice adopts or recommends restricted InsightFace weights. The
index consumes Lumina's separately approved embedding output; model licensing and
model provenance remain independent trust decisions.
