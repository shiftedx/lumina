# React image-gallery libraries for Lumina

Research date: 2026-10-24. Versions were checked against the npm registry and the projects' own release/source repositories on that date. The recommendation assumes Lumina's current React 19 + TypeScript + Vite client-only SPA, cursor-paged APIs, responsive image derivatives, and a library that can plausibly grow to 100,000 photos.

## Recommendation

Use **`@tanstack/react-virtual@3.14.22` for a window-virtualized list of Lumina-owned justified rows**, and lazy-load **`yet-another-react-lightbox@3.32.2`** for the immersive viewer. Do not add React Photo Album to the production path.

This split has the clearest ownership boundary:

- Lumina packs known `width`/`height` metadata into justified rows with a small pure function. TanStack virtualizes those rows against the existing window scroll, so the number of photo elements is proportional to visible rows rather than the number fetched. TanStack is deliberately headless, supports window virtualizers and measured/dynamic item sizes, and leaves cross-axis layout to the app; that is exactly the control Lumina's existing `WallGrid` design already expects. [TanStack introduction](https://github.com/TanStack/virtual/blob/main/docs/introduction.md), [React adapter](https://tanstack.com/virtual/latest/docs/framework/react/react-virtual), [Virtualizer API](https://tanstack.com/virtual/latest/docs/api/virtualizer)
- YARL owns only the modal viewer: responsive `srcSet`, limited adjacent-slide preloading, pointer/touch/keyboard navigation, zoom, fullscreen, slideshow, captions/counter, RTL, and React 19 support. Its portal marks page siblings `inert`/`aria-hidden`, exposes a labelled modal dialog, restores focus, makes inactive slides inert, and labels the carousel/slides; these claims are visible in the current source rather than inferred from marketing. [README](https://github.com/igordanchenko/yet-another-react-lightbox/tree/v3.32.2), [Portal source](https://github.com/igordanchenko/yet-another-react-lightbox/blob/v3.32.2/src/modules/Portal.tsx), [Carousel source](https://github.com/igordanchenko/yet-another-react-lightbox/blob/v3.32.2/src/modules/Carousel.tsx), [documentation](https://yet-another-react-lightbox.com/documentation)
- Both are MIT licensed, current, TypeScript-first, and explicitly accept React 19. The latest releases were published two days before this review for TanStack and in July 2026 for YARL. [TanStack releases](https://github.com/TanStack/virtual/releases), [YARL releases](https://github.com/igordanchenko/yet-another-react-lightbox/releases), [TanStack package](https://www.npmjs.com/package/@tanstack/react-virtual), [YARL package](https://www.npmjs.com/package/yet-another-react-lightbox)

The main risk is not a library defect: Lumina must own stable row packing when the container width, density, filter, or page set changes. Keep row packing deterministic from photo dimensions, cache it by width bucket/query, use `useWindowVirtualizer`, overscan several rows, and restore by photo ID rather than raw row index. The API must always provide positive width and height; malformed dimensions should fall back to a bounded ratio before layout.

## Current Lumina fit

Lumina already has two relevant precedents:

- `frontend/src/features/gallery/WallGrid.tsx` is a custom, fixed-height window virtualizer. It keeps the viewport ±3 rows plus the focused row in the DOM, preserves roving focus, restores scroll/focus, and drives a capped image loader. Its fixed poster/square row model cannot represent arbitrary photo aspect ratios without a second layout model.
- `frontend/src/features/gallery/StillWall.tsx` cursor-pages 60 items with an intersection sentinel, but explicitly remains non-virtualized and only applies `content-visibility`. It assumes 16:9 or square media. Reusing that unbounded DOM approach for photos would contradict the 100k requirement.
- `frontend/src/main.tsx` calls `createRoot`; Lumina does not hydrate server-rendered React. SSR-specific gallery behavior is therefore informational, not a selection criterion today.
- `GalleryArt`/`imageLoader` already supply bounded image concurrency, fetch priority, placeholders, responsive renditions, and fast-scroll behavior. The photo surface should preserve those policies where its API derivative URLs can be represented, instead of accepting a library's default eager image behavior.

## Candidate comparison

| Candidate | Current state (2026-10-24) | What it provides | 100k and Lumina fit | Decision |
| --- | --- | --- | --- | --- |
| React Photo Album | 3.6.2, MIT, React 18/19; released 2026-10-17 | Excellent justified rows, columns, masonry, responsive `srcset`/`sizes`, configurable rendering, SSR options, and a newish infinite-scroll/offscreen layer. Its own size budgets are 4 kB core and 1.4 kB scroll, excluding React. [release](https://github.com/igordanchenko/react-photo-album/releases/tag/v3.6.2), [docs](https://react-photo-album.com/documentation), [size limits](https://github.com/igordanchenko/react-photo-album/blob/v3.6.2/.size-limit.json) | Its scroll helper is **offscreen replacement, not indexed virtualization**. Rows mode stores every fetched batch, flattens every fetched photo on render, and swaps each far-away batch for one measured placeholder. At 60-photo pages, 100k reached photos still means about 1,667 placeholder nodes and all 100k metadata records retained; every batch is also an independent row-layout boundary. Singleton masonry wraps every photo in `Offscreen`, so it can leave one placeholder per photo. [InfiniteScroll source](https://github.com/igordanchenko/react-photo-album/blob/v3.6.2/src/scroll/InfiniteScroll.tsx), [Offscreen source](https://github.com/igordanchenko/react-photo-album/blob/v3.6.2/src/scroll/Offscreen.tsx) | Strong rapid-delivery alternative below measured large-library thresholds; reject for the hard bounded-window requirement. Do not wrap it in TanStack: both want to own layout/render grouping. |
| TanStack Virtual | 3.14.22 adapter + 3.19.7 core, MIT, React 16.8–19; released 2026-10-22 | Headless vertical/horizontal/window virtualization, measurement, overscan, scroll-to-index/offset, snapshots and initial measurement cache. Multi-lane items go to the shortest lane, but the app must render the cross-axis position. [release](https://github.com/TanStack/virtual/releases/tag/%40tanstack%2Freact-virtual%403.14.22), [API](https://tanstack.com/virtual/latest/docs/api/virtualizer), [VirtualItem](https://tanstack.com/virtual/latest/docs/api/virtual-item) | A count of 100k affects the internal model, not DOM count. Virtualizing composed justified **rows**, not individual photos/lanes, preserves reading order and makes keyboard navigation predictable. Dimensions let Lumina calculate exact row heights without post-load measuring. | **Choose.** Best match for Lumina-owned layout, route restoration, accessibility, and bounded DOM. |
| React Virtuoso | 4.18.19, MIT for `Virtuoso`, `Grid`, and `Masonry`; React 16–19 | Higher-level components with loading/end callbacks and measured items. Core components are explicitly free/MIT; the separate Message List is commercial. [pricing/license](https://virtuoso.dev/pricing/), [repository](https://github.com/petyosi/react-virtuoso), [npm](https://www.npmjs.com/package/react-virtuoso) | Less code for conventional lists, but its Grid expects same-size items and Masonry is column-based. Neither supplies justified photo rows, so Lumina still owns grouping while taking on a larger, more opinionated runtime. | Viable runner-up if requirements move to masonry; no advantage for justified timeline rows. |
| `react-window` | 2.5.0, MIT, React 18/19; published 2026-10-22 | Small List/Grid abstraction with dynamic row height support in v2. [npm](https://www.npmjs.com/package/react-window), [changelog](https://github.com/bvaughn/react-window/blob/main/CHANGELOG.md) | Mature and simpler, but its component API is less natural for Lumina's existing window-scroll surface and changing grouped rows. It offers less headless control than TanStack without solving layout. | Good conservative fallback, not first choice. |
| Masonic | 4.1.0, MIT; last npm change 2025-04-22 | Purpose-built virtualized masonry and infinite loading. [repository](https://github.com/jaredLunde/masonic), [npm](https://www.npmjs.com/package/masonic) | Correct if the product requires Pinterest-style columns, but that visual order is weaker for a chronological household timeline and the package brings many small hook/memoization dependencies. It does not solve an immersive viewer. | Reject for the chosen justified chronology. |

## Lightbox comparison

### Yet Another React Lightbox — choose

YARL 3.32.2 is MIT and supports React 16.8–19. The core package is intentionally modular: its repository budgets the core at 12 kB, Zoom at 5.5 kB, Fullscreen at 1.75 kB, Slideshow at 1.5 kB, Counter at 0.75 kB, and core CSS at 1.4 kB (size-limit output, excluding React). Import plugin entry points individually and code-split the viewer so the timeline does not pay for it until open. [package source](https://github.com/igordanchenko/yet-another-react-lightbox/blob/v3.32.2/package.json), [size limits](https://github.com/igordanchenko/yet-another-react-lightbox/blob/v3.32.2/.size-limit.json)

The default carousel preloads `2 * preload + 1` slides and defaults to two adjacent slides in each direction; it supports finite mode, responsive image candidates, touch/pointer swipe, Escape close, labelled controls, and reduced-motion-aware portal animation. [documentation](https://yet-another-react-lightbox.com/documentation), [README responsive-image example](https://github.com/igordanchenko/yet-another-react-lightbox/tree/v3.32.2#recommended-setup)

Integration constraints:

- Use `carousel={{ finite: true, preload: 2 }}` for cursor-loaded data; wrapping from the loaded end to the beginning is misleading.
- Keep `slides` referentially stable, as the official documentation warns, and append the next API page when `on.view` approaches the loaded tail.
- Supply `alt`, `width`, `height`, original/preview `src`, and every available derivative in `srcSet`. Do not send an original as a thumbnail.
- Lazy-import YARL, its CSS, and plugins. Map labels to Lumina copy. Test focus return, Tab containment, Escape, arrow keys, swipe, pinch/zoom, reduced motion, and screen-reader announcements; source-level semantics are strong evidence, not a blanket accessibility certification.
- A slideshow must expose a clear pause control and stop announcing every automatic change while focus is outside. Current YARL source sets the carousel live region to `off` during autoplay without focus and `polite` otherwise. [Carousel source](https://github.com/igordanchenko/yet-another-react-lightbox/blob/v3.32.2/src/modules/Carousel.tsx)

### PhotoSwipe — credible alternative, not chosen

PhotoSwipe 5.4.4 is MIT, framework-agnostic, supports responsive images, dynamic import, gestures/zoom, and array or callback-like dynamic data sources. It renders only nearby slides, and its official docs specifically show a data array whose grid has only three DOM thumbnails. [home/version](https://photoswipe.com/), [getting started](https://photoswipe.com/getting-started/), [data sources](https://photoswipe.com/data-sources/), [methods](https://photoswipe.com/methods/)

It is attractive for enormous viewer data sources, but 5.4.4 was last published in May 2024 while v6 is described as under development, and it requires an imperative bridge into React. Its official docs also removed built-in fullscreen and captions from v5 (captions due to accessibility problems), while Lumina explicitly wants fullscreen, slideshow, and details. YARL therefore gives a smaller integration surface and current React-native maintenance. [PhotoSwipe project](https://github.com/dimsemenov/PhotoSwipe), [v5 changes](https://photoswipe.com/)

## Concrete implementation boundary

1. Fetch cursor pages of photo metadata; never fetch original bytes for the grid. Require `width`, `height`, `thumbnail_url`, `preview_url`, and `original_url` in the public contract.
2. Pack photos into deterministic justified rows for a width bucket and density target. Keep source order in DOM. A final incomplete row uses natural widths and a bounded height rather than stretching.
3. Render row indices with `useWindowVirtualizer`, an estimated/exact row height, several rows of overscan, semantic date sections, and native buttons. Preserve the focused row if it leaves the normal range, following `WallGrid`'s established roving-focus behavior.
4. Use Lumina's image-load budget and `srcset` derivatives. The virtualizer limits elements; the loader limits concurrent requests. They solve different bottlenecks.
5. Lazy-load YARL only after photo activation. Start at the activated photo and append cursor pages near the loaded end. After close, return focus to that photo or its stable replacement and restore its scroll position.
6. Treat 100k as a measured acceptance gate, not a library slogan: seed 100k metadata records with mixed panorama/portrait ratios; verify bounded photo DOM, stable row count/scroll height, no long-task burst on resize/filter, memory after a full traversal, keyboard reachability, and no derivative request storm.

## Principal risks

- **Layout churn:** width/density changes repack every loaded photo. Debounce by `ResizeObserver` frame, cache width buckets, and preserve an anchor photo across repacks.
- **Cursor-only deep navigation:** the lightbox cannot navigate to photos not yet fetched. Append near the loaded tail; true random access later needs an index/keyset API, not another frontend library.
- **Virtual focus:** unmounting a focused tile loses focus. Keep the focused row in the extraction range or move focus deliberately before removal, matching the established `WallGrid` contract.
- **Metadata errors:** zero or corrupt dimensions can poison row math. Validate server-side and clamp on the client.
- **Image memory:** bounded DOM does not guarantee decoded-bitmap memory is reclaimed immediately. Keep YARL preload low, thumbnails derivative-sized, and originals viewer-only.
- **Third-party CSS:** YARL styles are global. Load them with the modal chunk, override via documented variables/classes, and run Lumina's style/license gates.
