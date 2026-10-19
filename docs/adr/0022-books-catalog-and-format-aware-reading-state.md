# ADR 0022: Books use a separate catalog and format-aware reading state

- Status: Accepted
- Date: 2026-10-12

## Context

EPUB books and CBZ manga are archives, not audio/video files. Their durable reading locations are EPUB chapter/scroll positions or CBZ page positions, and neither belongs in the media scanner, Jellyfin surface, or second-based playback progress.

## Decision

Lumina stores managed EPUB and CBZ uploads below `app-data/books/` under server-generated UUID filenames. `book_items`, member `book_states`, and ordered member shelves are independent of `library_items`, `media_titles`, collections, and playback progress. A Book item is private when imported and may be shared by its owner. A central Books visibility predicate is applied to every catalog, reader, archive resource, state, and shelf operation; shelf membership never grants access. Restricted members must have the Books section explicitly enabled.

The archive reader treats every ZIP member as untrusted input. It validates archive names and bounds, returns only inert structured chapter nodes, and serves only approved raster members through book-id routes. It never extracts an archive or sends raw HTML, scripts, styles, or iframe content to a browser.

## Consequences

- Movie/Music scanning, title metadata, Jellyfin, and playback continue to have their existing semantics.
- Favorites and reading-list edits do not create a reading checkpoint; only a reader location or completion update enters Continue reading.
- The Books catalog has no external discovery dependency. Its deterministic Discover list comes from visible, unread household books.
- The schema upgrades are additive. Rollback before format bundling unlinks bundle parents, clears soft-trash timestamps, drops only the three additive columns and their indexes, and re-stamps schema 20; every BookItem, BookState, shelf membership, and managed upload remains. A rollback before Books can still drop the four Books tables while intentionally leaving stored uploads on disk.

### Format bundles, original filenames, and recoverable removal (2026-10-13)

Each imported file remains its own `book_items` asset, with its original safe basename, storage key, native manifest, UUID, and independent member checkpoint. A root row (`bundle_parent_id IS NULL`) is the catalog entry; flattened children remain valid reader/resource IDs. Catalog, overview, series, and shelf responses expose roots only, while live child IDs remain readable when their owner or visibility permits it. The root aggregates the current member's favorites, reading-list flags, completion, and latest checkpoint without copying a page or chapter index between formats.

Combining and automatic matching are owner-scoped and require compatible ownership, visibility, kind, language, and volume. Automatic matching requires distinct formats and mutual uniqueness; an explicit manual combine can retain multiple files of the same format. Alternate files can be separated back into catalog entries with their own metadata and checkpoints. Only whole entries enter recoverable Trash; a child must be separated first. An active reading checkpoint takes precedence over completion of another format. Automatic matching requires either normalized explicit filename stems or a nonempty normalized title-plus-known-author key; legacy rows without filenames are never auto-combined. Deletion is a soft-trash operation over rows and files, and restore returns the rows, states, and shelf memberships without removing or rewriting managed files.

### Native page formats (2026-10-13)

PDF ebooks and CBR comics retain their original managed format. PDF pages are rasterized by PyMuPDF;
RAR members are streamed by libarchive without extraction. Both use the existing authenticated image
routes and page checkpoints. No PDF scripts, links, forms, attachments, or raw documents reach the browser.
CBR uses the same ComicInfo metadata, natural page ordering, and series semantics as CBZ.

Native parsing runs in short-lived subprocesses with a 30-second wall timeout, 25-second CPU limit,
24 MiB output limit, and a 1 GiB address-space limit on Linux. At most two workers run concurrently.
CBR retains the existing member, path, image, and 512 MiB uncompressed limits and rejects links;
PDF is limited to 2,500 pages and renders at most 2,400 pixels on the longest edge. Encrypted files
are rejected. This is additive format support and does not alter database schema or existing checkpoints.

### Bounded reader work reuse (2026-10-18)

Encoded raster resources share an in-process LRU bounded by 64 MiB and 128 entries.
Concurrent identical reads share one result; at most four uncached raster reads run at once. Keys include device, inode, size, and nanosecond
modification/change times; replacement or edits invalidate reuse. Routes retain visibility
checks and `private, no-store`, including cache hits. No raw archive or active content is cached
for browser delivery. The cache stores encoded response bytes, not browser-decoded pixels.

A separate 128-entry native validation registry remembers exact file identities and formats.
An unchanged validated CBR may stop after reading its requested member rather than validating
the remainder again. The first read after restart still validates the complete archive, and
solid archives may still decode preceding members. Persisted manifests alone never authorize
skipping validation. Existing native worker counts and resource limits remain in force.
Archive inspection during asynchronous import runs in the thread pool so a native parser wait
does not block unrelated ASGI requests.

### Bounded catalog hydration (2026-10-18)

Catalog, series, and overview classification use scalar root and per-user state rows. The default
added-date catalog applies count, ordering, offset, and limit in SQLite; exact NFKC/casefold search
and title/author ordering continue to scan scalar visible rows in Python. Only the selected catalog
page and series detail volumes become full `BookItem` objects. Series pages read descriptions and
cover presence for their selected first volumes without loading manifests for every manga volume.

Overview scans visible scalar metadata to preserve deterministic totals, affinity, and ordering,
then serializes at most 36 unique cards across Continue reading, Recently added, and Discover.
Available Continue-reading checkpoints are tested newest first and stop after the complete
twelfth-place timestamp tie. When fewer than 12 roots have an available active member, all active
members must still be tested to preserve the existing unavailable-last fallback. Shelf responses
scan their visible memberships because they return every ordered book ID, but select only the
cover-member scalar rather than each book's full manifest. Visibility remains enforced before any
full row is returned.

### Bounded static covers and raster delivery (2026-10-18)

Book covers are disposable static JPEG renditions, at most 600 pixels on the longest edge and
1 MiB. Exact file-identity keys include device, inode, size, nanosecond modification/change times,
format, and archive member, then are hashed before disk storage. One background cover worker admits
at most 32 identities and enters the two-slot native parser pool only after owning its cover lane,
leaving one parser available for reader pages. Pending requests return `202` with `Retry-After`;
the client keeps a placeholder and retries only while mounted. The private disk cache is bounded by
256 MiB and 2,048 files. It is derived data and uses least-recently-used eviction.

Raster delivery validates encoded structure without decoding pixels: canvas size is capped at
40 million pixels, animation at 256 frames and 80 million aggregate canvas pixels, and GIF, APNG,
and WebP frame extents must fit their canvas. Compressed PNG color/text metadata must be valid zlib
and expands to at most 1 MiB in total. Animated covers become a JPEG of their first frame; reader
resources retain a bounded animation. The shared parser uses only the Python standard library so
short-lived PDF/CBR workers do not load the database or title-metadata service graph.
