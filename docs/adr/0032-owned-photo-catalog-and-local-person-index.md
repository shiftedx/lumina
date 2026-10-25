# ADR 0032: Owned photo originals, bounded presentation, and a local person index

- Status: Accepted
- Date: 2026-10-24

## Context

Household photographs need their own catalog. Movie artwork is disposable title metadata;
photographs are original user assets, with independent ownership and privacy. A large
library must browse without decoding originals or rendering every photo. Face embeddings
are derived biometric data and model output remains untrusted (ADR 0013).

## Decision

- Store photographs separately from LibraryItem and MediaTitle, following the independent
  Books catalog boundary. Keep immutable uploaded originals in managed storage, derive
  presentation images, and retain assets when an owner moves a photograph to Trash.
- Centralize photo visibility in one SQL predicate, including ownership, explicit sharing,
  the member's Photos grant, and deletion state. Apply it to catalog results, counts,
  albums, person associations, derivatives, and originals. Identifiers confer no access.
- Index registered external roots in place through durable source/run records and bounded
  resumable scans. Partial, canceled, or offline scans never reconcile missing files. Secure
  descriptor opens and hash-checked snapshots separate mutable NAS files from processing.
  No source operation deletes or edits external originals.
- Use stable indexed keyset catalog pages. Generate orientation-correct, bounded
  presentation images with atomic cache publication. Browser thumbnails and viewer images
  never require original-size downloads. Preserve original bytes for explicit downloads.
- Use TanStack Virtual for justified photo rows and Yet Another React Lightbox for the
  immersive viewer. Keep photo presentation and its dependencies lazy-loaded. Preserve
  keyboard access, reduced motion, responsive layouts, focus return, and the established
  Lumina theme.
- Face analysis is explicitly enabled and local. Reuse the installed OpenCV runtime and
  its YuNet/SFace APIs with verified model artifacts. Do not infer that a code license
  grants rights to model weights. In particular, do not redistribute InsightFace's
  research-restricted pretrained packages.
- Treat face boxes, landmarks, embeddings, and suggested identity matches as untrusted:
  validate bounds, dimensions, and finite values before persistence. Suggested identity
  matches are editable; names and manual corrections belong to the household catalog,
  never an outside identity provider. Persist analysis state so interruption can be
  retried; limit inference concurrency and subordinate it to foreground media activity.

## Consequences

Photo originals and the SQLite catalog must be backed up together; the built-in database
backup excludes photo files. Derivatives can be
rebuilt. Trash is recoverable, and does not promise disk reclamation. Model availability
and opt-in state are visible; missing model artifacts must not be reported as completed
face recognition. Photo and face tables are additive; the operator guide records rollback.

The research notes in `docs/research/image-*-2026-10-24.md` record evaluated alternatives,
upstream licenses, integration costs, and limitations. Large-library performance claims
require measured browser evidence, separately from passing functional tests.

## Bulk-processing implementation follow-up (2026-10-24)

Initial import returns metadata and every configured rendition from one bounded
decoder invocation. Registered sources use two-file ordered lookahead under a global
four-preparation/two-decoder budget. Preparation and source revalidation remain outside
short serialized database writes; lifecycle retirement is linearized with publication.
Cursors advance only through ordered committed results. Uninterruptible filesystem
reads can outlive cancellation, but remain globally bounded and cannot publish after
retirement.

Face inference reuses one supervised YuNet/SFace child, following ADR 0014's resident
model pattern. Versioned length-prefixed requests/responses have byte, pixel, CPU,
active-runtime, output, and memory bounds. Models are verified before loading,
responses are validated before persistence, idle children are reaped, and repeated
crashes stop admission until explicit retry or an artifact-generation change. Resident
processes register with background-work admission only while serving a request.

This does not change the existing derivative-cache retention policy or qualify
million-face grouping. The opt-in full-pipeline benchmark and measurements record
completion counts, output equivalence, and remaining scale limits separately.

## Durable previews and scalable matching (2026-10-24)

Presentation images are durable catalog assets. The historical derivative-cache
limit becomes an admission quota, with an unlimited default and a separate free
space floor. Processing reserves its bounded output budget before decoding.
Catalog references and outstanding publication holds prevent reclamation;
completed, unreferenced outputs are reclaimed through indexed batches. Storage
pressure leaves folder scans queued at their committed cursor and uploads
retryable. Existing catalog promises are adopted during the additive upgrade.
Originals remain the recovery source for missing or damaged previews.

Local face matching uses FAISS CPU HNSW as a candidate accelerator over two
owner/pipeline-scoped populations: one current prototype per person, and
unassigned automatic faces. This replaces recency limits that silently omitted
older identities. SQLite retains authoritative embeddings, stable numeric vector
versions and transactional change capture. Checksummed, private graph checkpoints
accelerate restart; failed validation rebuilds from SQLite. Journal reconciliation
and graph work happen before acquiring the catalog writer. Every suggested match
is revalidated for current ownership, live photo state and assignment, then
rescored using float32 embeddings before the existing threshold/margin rules.
Approximate retrieval does not establish an identity or guarantee recognition
accuracy; manual corrections remain authoritative.

The opt-in scale benchmarks distinguish synthetic index performance, preview
retention under pressure, and the real HEIC processing pipeline. None substitutes
for a measured million-original ingestion on the operator's storage and hardware.

Short-lived photo decoder children receive a literal minimal environment rather than
the server's application, proxy, Python-path or dynamic-loader settings. This preserves
the existing codec/plugin installation while reducing ambient configuration exposure;
environment minimization alone does not confine filesystem or direct socket access.
The Linux decoder policy below adds those controls for supported kernels; equivalent
macOS confinement remains a separate follow-up.

### Linux decoder boundary (2026-10-25)

The one-operation photo decoder and native PDF/CBR workers now use the shared
`parser_boundary` launch policy. The image's util-linux `setpriv` applies a filter
generated by libseccomp before starting Python. Filesystem isolation uses either
Landlock rules or an explicitly selected Bubblewrap namespace sandbox.
The child can read its input and trusted runtime/code, and write only its private
photo staging directory. Socket creation, unrelated content access, metadata
mutation, signaling other processes, and new processes are denied; codec threads
remain permitted. Existing deadlines, output/resource limits and cancellation
cleanup remain in force. Resident face/model servers retain their existing
supervision and are outside this one-operation parser policy.

This avoids a Docker-socket dependency or privileged sandbox service. The default
backend requires Linux 6.2/Landlock ABI 3 and the shipped helpers. Missing kernel/helper
support fails closed, leaving affected photo and PDF/CBR operations unavailable.
Native macOS retains portable limits without equivalent filesystem/socket
confinement. AMD64 emulation can return Landlock ENOSYS; supported production
hosts need the kernel capabilities described in the [operator requirements](../../docker/README.md).


### Namespace backend for hosts without Landlock (2026-10-25)

Some Linux hosts expose a recent kernel but do not enable the Landlock LSM.
`LUMINA_PARSER_SANDBOX=namespace` instead uses maintained Bubblewrap with required
user, mount, PID, network, IPC and UTS namespaces. Its empty root is read-only;
only trusted runtime/code, the selected read-only input and the private writable
photo staging directory are mounted. The sandbox has an empty `/proc` and minimal
devices. Existing resource, environment and supervision limits remain enforced.
The inner seccomp filter additionally denies namespace and mount reconfiguration
before importing codecs. No automatic fallback or filesystem opt-out is provided.

Docker's default seccomp policy blocks namespace bootstrap. An optional Compose
overlay selects a pinned default policy with narrowly constrained additions for
Bubblewrap, preserving capability drops, no-new-privileges and Docker's masked
and read-only paths. This expands the application container's available kernel
surface during trusted bootstrap, so operators must qualify the exact profile
and kernel. It requires no privileged container, Docker socket or host reboot.
See the [operator requirements](../../docker/README.md) and
[profile provenance](../../docker/parser-namespace-seccomp.md).
