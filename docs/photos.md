# Photo library

Open **Photos** from Lumina's navigation menu. On phones it is under **More**.
The browser gallery uses a chronological, adjustable-size photo wall; opening a photo
provides zoom, keyboard/touch navigation, fullscreen, a slideshow, and **Photo details**.

## Add images

**Import → Upload files** accepts individual files or a folder. Uploads are private to
the importing member until shared. Failed files stay in the import dialog for retry.
Supported formats are JPEG, PNG, WebP, TIFF, AVIF, and HEIC/HEIF. Originals retain their
exact bytes; browsing uses orientation-correct, metadata-free sRGB WebP derivatives.
RAW and animated GIF import are not supported.

Vault owners can also use **Import → Synced folders**. Register a read-only external
storage root in **Manage Server → Library**, then choose it and an optional folder
inside it. Lumina indexes these files in place and never changes or deletes the NAS
originals. Sources offer manual scans, cancellation, status, and automatic checks.
Automatic checks run every 15 minutes during the configured background backfill window
and yield to foreground media activity. They are disabled until that window is configured
in the [operator guide](../docker/README.md#optional-loudness-and-caption-backfills);
**Add and scan** and **Scan now** work on demand. A failed or offline scan does not
establish that previously indexed photographs have disappeared.

An upload or source rescan preserves a photo's title, personal favorites, and organization.
Changed external bytes invalidate derived images and face-analysis work. Moving a photo
to Trash is a catalog action; a rescan does not restore a photograph deliberately trashed.
Removing a synced source moves its photo records to Trash and leaves its files intact.

## Find and organize

Search filenames and edited titles, filter by camera or dates, switch between capture
and added order, or show personal favorites. Filter addresses can be bookmarked. Albums
can be private or shared with the household. Sharing an album does not change a private
photo's visibility: other members see only album photographs they can already access.

Use **Photo details** to edit a photo's title or visibility, add it to albums, or explicitly
download the unchanged original. Original downloads follow the member's download grant.
Favorites belong to each member. Trash can be restored; it does not reclaim original-file
storage. Household sharing also follows the member's Photos library grant.

## People

**People → Find people in photos** enables optional local analysis of your own photographs.
A vault owner downloads the pinned, verified face model from this view. Model-download,
queued, running, and failed states remain visible. No model is silently downloaded when
someone opens the gallery.

Automatic groups are suggestions. Add names, merge duplicate groups, hide/unhide groups,
or open a photo's details to assign, ungroup, or separate a mistaken face. A single detected
face can also be named manually. Names and corrections are owner-scoped; other members
viewing a shared photograph do not receive your face measurements or private identity
labels. Choosing a person opens the matching photo filter.

Matching searches local owner-specific indexes covering older photographs as well
as recent additions. SQLite retains the measurements and transactional changes;
checksummed graph files under `app-data/face-index/` accelerate restart and can be
rebuilt from the database. `LUMINA_PHOTO_FACE_INDEX_CACHE_MAX_BYTES` sets a soft
resident-cache budget across owners (2 GiB by default). Construction and checkpoint
buffers require additional memory, and one active owner may exceed that budget.

Turning analysis off stops new work. **Delete face data** removes your measurements,
groups, names, and analysis history while keeping the photographs. Face recognition is
probabilistic; matching thresholds have not been qualified against a household accuracy
corpus. The implementation uses OpenCV YuNet/SFace locally; model provenance and licensing
are recorded in the [research note](research/image-face-recognition-2026-10-24.md).

## Storage and backup

Uploaded originals live under `app-data/photos/originals/`; NAS originals remain in their
registered roots. Catalog-backed previews remain in durable storage across restarts.
`LUMINA_PHOTO_DERIVATIVE_CACHE_MAX_BYTES` retains its historical name but now sets
an admission quota (unlimited by default). Reaching or lowering it never evicts
referenced previews: uploads return a retryable storage error and folder scans
wait at their committed cursor until capacity returns. `LUMINA_PHOTO_PREVIEW_MIN_FREE_BYTES`
reserves disk headroom before decoding; its default is 1 GiB.

Lumina's built-in database backup does **not** include photo files. Back up the database
and managed originals together, and protect the external photo roots separately. A stopped
server/full-volume snapshot is the simplest consistent backup. For an online backup, take
the database snapshot before copying immutable managed originals; keep the database's
existing server-key/restore requirements from the [operator guide](../docker/README.md).
Include `photos/derivatives/` in the same backup to avoid rebuilding previews after
restore. Previews retain less detail than originals and cannot replace their backup.

The implementation and its local 100,000-row catalog measurements are documented in
[ADR 0032](adr/0032-owned-photo-catalog-and-local-person-index.md) and the
[pipeline research](research/image-pipeline-2026-10-24.md). The
[scale measurements](performance/photo-scale-2026-10-24.md) separately cover
three-million-row preview accounting and synthetic million-face lookup/restart.
