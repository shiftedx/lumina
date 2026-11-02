# Native Home and Library navigation follow-up

Implementation base: current `origin/main` at `185dfcf99ee3685072ea9a61378245a95e76437c`, in the isolated `codex/native-home-navigation` worktree. The prototype and web implementation branches, main checkout and release packages were preserved. The owner’s focused-testing instruction in `dogfood-next-handoff.md` applies; no full repository/Core/native suite ran.

## Result and ownership

| Packet | Owner | Scope | Status |
| --- | --- | --- | --- |
| iOS Home navigation | Mobile worker, integrated/reviewed by orchestrator | `apple/App/iOS/MobileRootView.swift` | Complete |
| TV shell/focus and shared private view contracts | Orchestrator | `apple/App/tvOS/TVRootView.swift` | Complete |
| Workflow migration, regression checks and final verification | Orchestrator | `apple/Tests/UITests/NativeProofTests.swift` | Complete |
| Independent source/HIG review | Read-only reviewer | Home/Watchlist/Library access, TV focus boundaries | Complete; findings addressed |
| Web drawer underline | Web worker, integrated by orchestrator | Separate web worktree, `frontend/src/app/shell.css` | Production revision `c3f4a5acfdd126620f779198a49cb32751c852e7` |

Home has no media-category strip on iPhone, Duo or iPad. Library retains its real, server-driven categories. Home’s compact Browse menu preserves Watchlist access; iPad also exposes Library and Account because its system tab bar is hidden. Library opens at its root, including its existing empty-library state. Search, downloads, Now Playing, member state, hero/shelves and the existing primary navigation remain available. Duo portrait keeps Search first in its utility rail, below the status corner.

The TV header displays a decorative sunrise image without the wordmark. A separate Home icon preserves direct Home access without making the logo a focus stop. Down from Home categories explicitly enters the hero Play action; Up restores the category last focused. Down from the Music Library header enters its featured album and Up returns to Music. Loading/preparing heroes do not request focus on disabled controls. Existing shelf entry and history-restoration paths remain in place.

## Capability and validation matrix

| Observable contract | Evidence |
| --- | --- |
| No category tabs on Home; dynamic Movies/TV/Music/Books in Library; Watchlist and Home remain reachable | `testHomeKeepsCategoriesInLibrary` passes on iPad, closed Duo, open portrait Duo and open landscape Duo |
| Home navigation scrolls away, Library categories stay pinned while posters scroll | iPad `testIPadHomeHeaderScrollsAwayAndLibraryCategoriesStayPinned` passes |
| Open portrait empty Watchlist centered; server-backed saved item replaces empty state after relaunch | `testEmptyWatchlistCentersBelowCategoriesAndPopulates` passes |
| TV logo is not a button, wordmark absent, direct Home remains available | `testHeaderEntersHeroBeforeShelves` passes |
| TV Home Movies/TV/Music/Watchlist Down enters hero; Up restores each origin; Music Library Down/Up uses its album hero | Same focused TV regression passes |
| TV unvisited shelf begins on its leading card | Existing `testNewHomeCarouselsEnterAtLeadingCard` passes |

Eight retained device/workflow qualifications pass: two TV workflows, two iPad workflows, and four Duo workflows. The TV regression was extended and rerun to include Music Library; it is not counted twice. Both simulator app builds compile successfully. Final meaningful logs/result bundles are under ignored `output/native-navigation/`: `tv-final`, `tv-music-verified`, `ipad-final`, `duo-closed`, `duo-portrait-navigation`, `duo-portrait` (its Watchlist method), and `duo-landscape-final`. `tv-build-final.log` records the explicit TV build. `git diff --check` passes.

The original signed TV regression failed because Down skipped the hero, then passed after the fix. An earlier unsigned simulator build failed before navigation because keychain storage requires signing. iPad floating-header controls and Duo multi-display coordinates required harness corrections: tests now tap visible controls through the active window and assert the resulting destination/menu. Their useful route, dynamic-category, Watchlist, pinned-header and focus assertions remain. Previous Home-category-width assertions were migrated to Library because the requested design removes that Home control; no workflow test was deleted.

Reviewed screenshots, source hashes and capture provenance are in `output/native-navigation/review/index.html` and `captures.json`. Fixtures use real isolated backend contracts and synthetic media, not household content. Physical-device focus/playback qualification and a new App Store/TestFlight release were not performed. No merge, push, native distribution upload, marketing change or production data mutation occurred.
