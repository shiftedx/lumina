# ADR 0036: Use explicit ComicInfo bookmarks for illustrated-book chapters

- Status: Accepted
- Date: 2026-11-01

EPUB has an ordered spine and navigation document, so its chapter contract remains unchanged. CBZ and CBR files have ordered image pages, but page order, file names and reader checkpoints do not establish chapter boundaries. Lumina therefore exposes illustrated-book chapters only when an archive's `ComicInfo.xml` has an explicit `<Pages><Page Image="N" Bookmark="Label"/></Pages>` marker.

`Image` addresses the archive's validated natural page order, beginning at zero. A reader response serializes a valid marker as `{ label, start_page }`; choosing it opens that page, while the saved reading position remains the existing page index. Browsing a title, its volume, or its chapter list never writes a checkpoint. Starting the reader preserves its established checkpoint behavior.

The importer accepts only nonempty, normalized bookmark labels up to 160 characters, valid in-range integer page indexes, and one marker per page. It bounds parsing to 500 valid markers, then orders those markers by `start_page` so adjacent boundaries define contiguous reading ranges. Invalid, duplicate, oversized, missing, or out-of-range markers are omitted. An archive without valid markers remains page-only; Lumina does not synthesize chapters from pages, filenames, volume numbers, or EPUB concepts.

This adds no database schema or migration. The bounded archive manifest already stores format-specific inspected metadata, and the member-owned `BookState.unit_index` remains the durable, format-specific checkpoint. PDF files remain page-only because their current inspected format has no comparable validated bookmark source. Future support for publisher or owner-authored chapter boundaries must define its source, ownership, validation, and migration separately.
