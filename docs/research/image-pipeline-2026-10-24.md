# Durable photo ingestion and derivatives — 2026-10-24

## Decision

Ship uploads and registered external folders as equal Photos ingestion sources using the existing authenticated FastAPI/SQLite boundaries. Preserve exact uploaded bytes, index external files in place, catalog only allowlisted metadata, and create metadata-free sRGB WebP renditions at 400, 1200, and 2400 pixels. Decode in a fresh, resource-limited child with at most two concurrent decoders. Keep derivative rows durable and repair pending, failed, or evicted files on demand.

Use Pillow 12.2 plus pillow-heif 1.8 for this release. Do not add a system libvips dependency until measured ingestion throughput makes it necessary. Do not claim camera RAW support: a safe RAW implementation needs a separately bounded LibRaw worker, camera-coverage policy, and a product decision about embedded previews versus demosaicing.

## Existing architecture inventory

The reusable boundaries already present in Lumina are:

- `backend/app/http_boundaries.py`: route-specific streaming body limits, timeout/disconnect handling, and access logging. Photos adds a 128 MiB upload cap rather than buffering request bodies.
- `backend/app/persistence.py`: bounded regular-file reads and coordinated write transactions.
- `backend/app/services/storage_roots.py`: registered-root identity and online checks. Photos adds descriptor-relative `O_NOFOLLOW` traversal and immutable private snapshots in `photo_files.py`; raw filesystem paths never cross the API.
- `backend/app/services/member_access.py`: section grants and download permission. As in Books, owned media remains visible; the Photos grant controls household-shared media.
- `backend/app/services/resource_policy.py` and `background_work.py`: established household resource controls. The current photo pipeline does not start an unbounded background queue; it has a process-wide two-decoder admission gate and per-child CPU/address-space/file-size/wall limits.
- Books (`backend/app/book_models.py` plus its service/router boundaries): precedent for managed uploads, owner-scoped deduplication, personal state, shared visibility, soft deletion, and collections.
- ADR 0006 (subprocesses), 0009 (SQLite), 0017 (persistence), 0019 (member access), 0020 (resource policy), 0021 (storage roots), 0024 (cache cleanup), and 0030 (native external streaming) constrain the implementation. In particular, media decoding cannot consume unbounded web-process memory and permissions must be rechecked before serving bytes.

The prior codebase had image use for title art and book covers, but no durable general photo catalog, personal photo state, album membership, original-byte API, or bounded HEIF-aware ingestion pipeline.

## Implemented subsystem contract

### Storage and catalog

- `photo_items` owns immutable source identity: owner, SHA-256, exact byte count, type, dimensions, capture time, camera make/model, storage kind/key, visibility, and soft-delete time.
- `(owner_user_id, sha256)` is unique. Re-upload by the same owner is idempotent; another owner receives a separate item and source path.
- Managed originals live below `app-data/photos/originals/<hash-prefix>/<photo-id>.<ext>`. Upload bytes are fsynced, atomically renamed, then the parent directory is fsynced. A failed database publication removes only its random losing target.
- `storage_kind=external`, `root_id`, and `relative_path` index source bytes in place. Every original/decode read opens each path component relative to an already-open root descriptor without following symlinks, then copies bounded bytes into a private immutable snapshot. Admins select an already-registered external root and an optional confined relative folder. The API returns root IDs, labels, and online state but never host paths. Sorted-DFS scans commit every 200 files, persist a cursor, resume after restart, and hash/decode only changed size/mtime fingerprints. Automatic polling runs only during the configured backfill window and yields to interactive activity; explicit Add/Scan requests bypass ordinary HTTP quiet time while remaining low-priority, bounded, cancelable, and shutdown-owned. File-local corrupt, unsupported, changing, or oversized entries are counted and skipped without starving later files; a partial run publishes valid entries but never reconciles absence. Missing rows are marked only after a complete error-free online scan; an offline root leaves the last good catalog intact. Rescans preserve title, visibility, favorites, albums, trash choice, and person corrections.
- Soft deletion preserves originals, derivative records, albums, and personal state. Trash is owner-only. Restore clears `deleted_at` without changing identity.

### Metadata

The child reads only the primary still frame, applies EXIF orientation, and records an allowlist: capture timestamp (plus the unparsed source value when necessary), camera make/model, lens, ISO, focal length, aperture, exposure time, and whether an ICC profile existed. GPS, XMP, comments, thumbnails, maker notes, and arbitrary EXIF are not persisted or returned.

Pillow documents that `ImageOps.exif_transpose` applies the EXIF orientation and removes the orientation from the transformed result ([Pillow ImageOps](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html)). Embedded ICC input is transformed to sRGB with LittleCMS when possible. A bad profile falls back to a normal RGB conversion rather than failing the original import.

### Decode and derivative safety

Accepted containers are JPEG, PNG, WebP, TIFF, AVIF, and HEIC/HEIF. Type is first sniffed from bounded header bytes and then must agree with the decoder (HEIC and generic HEIF are the only alias pair). Extensions and request content types are not trusted.

Pillow's security guidance calls out decompression bombs, parser complexity, metadata disclosure, temporary-file cleanup, CPU limits, and OS/container memory limits ([Pillow security](https://pillow.readthedocs.io/en/stable/handbook/security.html)). The implementation therefore:

- caps the request at 128 MiB and decoded geometry at 80 megapixels;
- treats `DecompressionBombWarning` as an error and retains Pillow's harder bomb error;
- restricts decoder formats and reads only frame zero;
- runs inspection/rendering outside the web process in a fresh child;
- applies a 30-second wall timeout, 25-second CPU limit, 1.5 GiB address-space ceiling, and 8 MiB output-file ceiling where the host exposes those POSIX limits;
- admits no more than two decoder children per Lumina process;
- writes derivatives into a private staging directory, fsyncs each file, then publishes with `os.replace`.

Python documents a successful same-filesystem `os.replace` as atomic on POSIX ([Python `os.replace`](https://docs.python.org/3/library/os.html#os.replace)). The database key is a SHA-256 of source hash, pipeline version, target width, orientation/color policy, and output format. This makes renditions immutable by content policy, while the authenticated byte route uses `private, max-age=0, must-revalidate` plus ETag so a browser must recheck current visibility after sign-out, member switching, trash, or grant revocation.

`pillow-heif` is a Pillow plugin backed by libheif and supports HEIF reading/writing ([pillow-heif overview](https://pillow-heif.readthedocs.io/en/stable/); [plugin behavior](https://pillow-heif.readthedocs.io/en/stable/pillow-plugin.html)). Its wheels make one-image Docker distribution practical without adding and maintaining distribution-specific `apt` packages. The dependency and its hashes are locked in both backend lock files.

### Catalog and permissions

- `GET /api/photos` uses stable descending keyset order: `(coalesce(captured_at, created_at), created_at, id)` or `(created_at, id)`. It never materializes the catalog in Python.
- Global catalog indexes cover both orderings and owner/shared indexes retain selective paths. SQLite documents that one multi-column index can satisfy filtering and ordering without a separate sort ([SQLite query planner](https://www.sqlite.org/queryplanner.html#searching_and_sorting_with_a_multi_column_index)).
- Filters are `q`, personal `favorite`, visible `album_id`, current-member `person_id`, camera, capture date range, active/trash, and captured/added sort.
- List serialization is set-based: one page query, one exact count, then batched favorites, visible albums, and current-member people. It does not issue queries per photo.
- Photos are private by default. Shared photo bytes require the Photos section grant. The owner always retains access to owned rows. Only owners can change title, visibility, and trash state; favorites are per member.
- Original bytes additionally require `can_download`. Renditions do not reveal source metadata. Deleted renditions remain visible only to their owner in Trash; deleted originals remain unavailable until restore.
- A private album identifier cannot leak through a shared photo. Album filtering first validates that the album is visible and active. Person identifiers are restricted to the current member's nonhidden, nonredirected identity records.

### Albums and derivatives

Albums have owner, private/shared visibility, soft deletion, stable membership position, and visibility-safe counts/covers. Membership may contain any photo the album owner can currently see, but every reader's count, cover, and serialized IDs are recomputed through that reader's photo and album visibility predicates.

Every import creates durable `pending` rows for 400/1200/2400. Successful rendering changes them to `ready`; failures record a short error. A request for a pending/failed rendition, or for a ready rendition whose disposable file was evicted, performs one bounded atomic rebuild and updates all three rows. This also recovers after a crash between original publication and derivative completion.

The derivative directory is a disposable LRU cache with a configurable 20 GiB default ceiling (`0` explicitly means unlimited; bounded values below 32 MiB are rejected as unservable). Successful reads touch cache recency, completed publications account toward the ceiling, and startup plus hourly maintenance trims oldest files to 90% headroom after crossing the cap, avoiding a full directory walk on every subsequent photo. Source-hash replacements remove unreferenced superseded keys. Managed and external originals are outside the cache and are never evicted; startup cleanup is limited to private `.upload`, `.snapshot`, and rendition-staging files.

## Performance evidence

`scripts/perf/photo_catalog.py` seeds 100,000 cheap metadata rows, reads 100-item first and 90%-deep pages for both sorts, serializes the page, records SQL count, and runs `EXPLAIN QUERY PLAN`. On the development host (five trials, warm SQLite), the measured medians after the final indexes and row-value seeks were:

| Query | Median | Plan |
| --- | ---: | --- |
| Captured first | 19.12 ms | catalog captured index |
| Captured 90%-deep | 15.69 ms | catalog captured expression range |
| Added first | 13.66 ms | catalog added index |
| Added 90%-deep | 20.53 ms | catalog added tuple range |

Each case issued five SQL statements including the exact total and the three batched association reads. No plan used a temporary B-tree, and deep pages did not use offset or materialize preceding rows.

## Alternatives evaluated

### libvips / pyvips

libvips remains the strongest upgrade if concurrent large-image throughput becomes a demonstrated bottleneck. Its `thumbnail_buffer` supports loader-aware shrinking, autorotation, input/output profiles, failure policy, and HEIF loading; it also exposes `icc_import`, `icc_transform`, and `icc_export` ([pyvips Image API](https://libvips.github.io/pyvips/vimage.html)). Its demand-driven/sequential pipeline normally has better peak memory behavior than full Pillow materialization.

It was not selected now because it introduces a native `libvips` plus codec build/runtime matrix into the single Docker image, while HEIF availability depends on how libvips/libheif were compiled. The bounded Pillow child contains decoder failure and already meets measured catalog—not decode-throughput—requirements. Reconsider libvips when a benchmark of representative 20–80 MP uploads shows unacceptable latency or decoder admission becomes a sustained bottleneck.

### RAW / rawpy / LibRaw

rawpy exposes both embedded preview extraction and full `postprocess`, with the latter returning a full NumPy image array ([rawpy `RawPy`](https://letmaik.github.io/rawpy/api/rawpy.RawPy.html)). LibRaw's supported-camera list is broad but explicitly contains model/mode exceptions ([LibRaw supported cameras](https://www.libraw.org/supported-cameras)). Therefore “RAW support” is not a single file-format checkbox.

A later RAW slice should preserve the source bytes, prefer a sufficiently large embedded JPEG preview, run LibRaw in its own stricter child when demosaicing is necessary, record the decoder/pipeline signature, and publish the same WebP derivative contract. Acceptance must include an explicit camera corpus, unsupported-camera UX, memory measurements, and proof that no raw NumPy pixels enter the API process.

## Acceptance criteria for this slice

1. Exact uploaded original bytes round-trip byte-for-byte; same-owner duplicate import returns the existing ID and different owners do not share authorization identity.
2. JPEG/PNG/WebP/TIFF/AVIF/HEIC/HEIF header and decoder type agree; malformed data, over-limit bodies, bombs, excessive dimensions, timeouts, and child failures create no catalog row or staging leak.
3. EXIF orientation is reflected in dimensions and pixels; capture/camera allowlist is available; GPS and arbitrary metadata are absent from the database/API; derivative EXIF/XMP is empty.
4. Renditions are 400/1200/2400 maxima, atomic, version-keyed, ETagged, access-revalidated, and repair after pending state, failed state, cache eviction, or process interruption.
5. Private/shared, section grants, `can_download`, owner edits, personal favorites, trash/restore, private/shared albums, album counts, person IDs, and original/rendition bytes pass an owner/member visibility matrix.
6. Catalog pagination is stable under equal timestamps, has no overlap, rejects malformed cursors, uses keyset seeks at 100,000 rows, and keeps page serialization to a fixed query count.
7. Schema 35 upgrades additively, the schema snapshot includes Photos and face-index tables, and the operator rollback drops catalog/index tables while preserving managed originals for recovery.
8. External roots remain inaccessible through arbitrary paths and symlink swaps. Source create/list/scan/cancel/delete accepts only a registered external root ID and confined relative folder; source deletion soft-deletes index rows but never changes source bytes. A completed error-free online scan marks missing files, while offline, traversal-error, canceled, or partial scans do not. One malformed file does not starve later valid files. Changed files retain their item ID and user state while invalidating/rebuilding derived work.
9. Derivative disk use has a configurable hard ceiling, least-recently-used renditions repair on demand, superseded source-hash files are reclaimed, crash staging is cleaned at startup, and neither managed nor external originals can be selected for eviction.
