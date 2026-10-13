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
- The schema upgrade is additive. Rollback drops the four Books tables and re-stamps schema 18; stored uploads are intentionally left for an operator to decide about.

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
