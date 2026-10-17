---
target: standalone Books views
total_score: 36
p0_count: 0
p1_count: 0
timestamp: 2026-10-13T04-00-30Z
slug: frontend-src-features-books-bookssurface-tsx
---
# Standalone Books design review

Target: `frontend/src/features/books/BooksSurface.tsx`, `/books` and its collection, series, detail, and reader views.

The views pass the product slop review. They retain Lumina's shell, serif content hierarchy, sans controls, ivory/dark/gold tokens, and restrained surfaces. The original sparse overview did waste space; that finding resulted in code changes and browser regressions, not a palette replacement.

## Design health

| Heuristic | Score /4 | Evidence |
| --- | ---: | --- |
| Visibility of system status | 3 | Per-file import states, progress, save failure and retry; background saves remain quiet. |
| Match to real world | 4 | Ebooks, manga series, ordered volumes, shelves, chapters, and reading positions use familiar language. |
| User control and freedom | 4 | Reader exit, contents, appearance, fullscreen, read-again, and reversible shelf operations. |
| Consistency and standards | 4 | Existing Lumina shell, controls, colors, and typography; focused reader preserves navigation semantics. |
| Error prevention | 3 | Format/bounds checks, delete confirmation, account guards, and checkpoint-before-navigation. |
| Recognition rather than recall | 4 | Named collection tabs, visible series/volume hierarchy, titles and tooltips for reader tools. |
| Flexibility and efficiency | 4 | Search, sorting, bulk shelf actions, custom ordering, keyboard reading, single/spread layouts. |
| Aesthetic and minimalist design | 4 | Compact sparse composition, distinct story sections, bounded covers and centered reader controls. |
| Error recovery | 3 | Import retries and reader save retry are available; errors retain the current reading location. |
| Help and documentation | 3 | Format guidance, empty states, shelf sharing explanation, and reader control titles. |
| **Total** | **36/40** | Independent follow-up review; baseline was 29/40. |

## Detector evidence

Assessment A was completed before Assessment B findings entered synthesis. The CLI detector scanned `frontend/src/features/books` and returned four broken-image findings: dynamic image sources in `BookCards.tsx` and `BookReader.tsx`, plus two deliberate unsafe image payloads in reader tests. Manual inspection confirmed guarded real asset URLs and fallback/retry behavior; these findings were false positives.

Browser detection ran in fresh, mutable headless pages after clean screenshots. Sparse Overview and sepia Reader each returned one cream-palette finding. Rich Overview returned cream-palette, italic-serif-display, and single-font findings. The warm colors and editorial serif are committed Lumina identity; visible sans-serif controls disprove the single-font sampling result. These signals did not justify changing the brand. No user-visible overlay is claimed. The temporary detector server was stopped.

## What works

- Ebooks and manga share organization while manga retains series and ordered-volume navigation.
- The focused reader has a believable prose rhythm, adjustable appearance, and useful navigation without decorative framing.
- Collection shelves and covers carry the content hierarchy. Production renders actual imported covers or an honest typographic fallback, with no fake catalog content.

## Priority issues resolved

1. **[P1] Sparse sections consumed full-width rows.** Recently added now occupies the left desktop column; compact shelves and Rediscover stack in the right column. Section actions sit beside their headings. Sparse tracks are bounded at 144–160px. Dense collections use wider collection rows naturally. Regression verifies column placement.
2. **[P1] Desktop reading controls sat at viewport edges.** Previous, progress, and Next now share a centered rail capped at 840px and aligned with the actual reading workspace, including open contents/appearance panels. Regression verifies center alignment, containment, and both single/spread states.
3. **[P2] Overview repeated stories across sections.** Continue reading is limited to three entries; Recent excludes those entries; Rediscover excludes stories already shown in Recent, with manga deduplicated by series. Recent's View all opens All books, preserving both formats.
4. **[P2] Ordinary fallback titles broke inside words.** Smaller type and a wider usable cover text area keep Northbound on one line. Browser regression verifies this at the 160px cover size.

No unresolved P0 or P1 design issue was found in the final review. The previous icon-tooltip concern was withdrawn: Lumina's IconButton already provides hover titles.

## Personas and cognitive load

- A first-time collector can import from the Books header or empty state, then find a title without interpreting video-library terminology. Reader tools remain compact but have accessible names and hover titles.
- A large-collection user has search, status filters, author/title/recent sorting, mixed-format shelves, selection, and custom shelf ordering. Section deduplication reduces repeated choices.
- A phone reader gets an isolated reading surface with 44px controls. Collection pages retain Lumina's global mobile navigation; sufficient bottom inset keeps terminal content reachable. The five local collection tabs remain a visible classification choice, but do not compete with app navigation inside the reader.

The reader is the emotional peak: content, comfortable line length, and reachable progression. The sparse overview now looks intentionally arranged without filler content.

## Visual verification

Clean desktop and phone captures are in `output/playwright/books/`: sparse and rich Overview, Manga, Shelves, EPUB reader, and CBZ reader. The richer desktop Overview shrank from a repeated, approximately 2,436px composition to approximately 1,191px, without concealing available collection items behind decorative whitespace. Browser checks cover accessibility, horizontal overflow, sparse geometry, ordinary fallback wrapping, and reader controls.

## Minor observations

Global mobile navigation overlays the currently scrolled content at the viewport bottom, as elsewhere in Lumina. This review preserves the agreed shell boundary. Long-term Discover value depends on the household's imported collection; the current view uses visible unread books, without an external recommendation service.

## Design questions retained for future work

As collections grow, should readers curate multiple personally ordered shelves or rely more heavily on author/series browsing? Is a later discovery source warranted once household collection discovery is validated? Neither question blocks this release or adds speculative UI now.
