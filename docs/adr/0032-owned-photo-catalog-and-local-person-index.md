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
