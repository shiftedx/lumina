# Direct A integration and Impeccable review

The initial review deployment (`44a00edb`) shipped the production data contracts and incremental presentation changes, but retained too much of the previous composition. It did not satisfy the approved A mockup. This correction implements the mockup through the production components, retaining real routes, permissions, actions and server state. Reference: prototype `e8720a92fd6c9b03c76d08e7c8351d0b6b32ca32`.

## Scope and ownership

All non-settings primary destinations and their browse/detail/player workflows were reviewed: Home; All video/Movies/Shows/Anime and title/season/episode; Music albums/artists/Saved, album/artist details, playlists, management and offline player; Books overview/ebooks/manga/shelves/discover, title/series/chapters/readers; Requests Discover/catalogs/search/title/history/manage/dialogs; Streaming Browse/Live/channels/search/watch/theater; Photos timeline/albums/people/trash/viewer/import; Downloads and collections. Settings layouts, marketing, native releases and public route contracts were not redesigned.

Root owns shell, semantic tokens, shared gallery/search/focus, cross-surface integration and deployment. Video/Home owns its feature files and title details; Music owns its catalog/details then Requests/Streaming/watch; Books owns its catalog/details/readers, then independently gathers detector/browser evidence. File ownership is explicit and non-overlapping during each packet. All workers use the shared implementation worktree and preserve existing work.

The prototype's fixtures, local progress, fake playback and preference key are excluded from product behavior. Open-film artwork is limited to browser test fixtures, with source/credit/hash provenance under `frontend/e2e/fixtures/open-films/`.

## Impeccable assessments and feedback-driven changes

Assessment A was an independent design review using the product register, approved A composition, source and desktop/phone captures. Initial heuristic scores (0–4): status 3; real-world match 4; control/freedom 3; consistency 3; error prevention 3; recognition 3; efficiency 3; minimalism 3; recovery 3; help 2. Total 30/40 before the final feedback fixes. This is not an automated accessibility conformance score.

Strengths: artwork leads the experience; media names retain their own hierarchy; playback/reader and permission boundaries remain explicit. The principal cognitive risk is combining media browsing with secondary collection/administration tools on small screens. The first-time, low-vision/keyboard and distracted-phone personas informed the changes below.

| Finding | Concrete correction and retained contract |
| --- | --- |
| P1: old chrome, ruled sections and boxed feature layouts dominated the approved design | Home, Movies/Shows, All video, Books and Requests now open on an artwork stage at viewport y=0, behind compact floating chrome. Music uses large square recency shelves; Streaming uses two larger real picks with deduplication. |
| P1: italic/bold and image-logo substitutions contradicted the title system | Shared normal serif media role is 48/34 px. Home/video/Requests render actual title text. Album/artist/book/watch titles use the same role; controls/captions/body use sans-serif. Brand artwork and logo data contracts remain available. |
| P1: phone keyboard movement could place a poster under the fixed tab bar | WallGrid measures the actual mobile bar height when scrolling the selected row. Existing scroll padding and added target margins clear the bar. Browser checks assert the focused card lies above the bar. Partial offscreen rows while scrolling remain normal fixed-navigation behavior. |
| P1: tab arrows reduced the YouTube label to a fragment | The phone context row scrolls directly, without persistent arrow buttons, and has narrower gaps at 320 px. A browser assertion requires the complete YouTube control to be in view. All destinations and Refresh remain available. |
| P1: phone media labels inherited a hidden-caption rule | Caption visibility and virtual row measurements were updated together; album/artist/poster names and search-result captions remain visible at phone sizes. |
| P2: Music utilities and collection promotion competed with recency | Albums/Artists stay visible. Playlists, management and offline listening remain in Music tools; the collection sampler uses a separate disclosure. Complete catalog URLs, Saved, member preference and queues remain connected. |
| P2: Photos accumulated too many controls before the art | Sort moves into Filters with camera/date controls; search is the single contextual entry and keyboard shortcut target. The phone panel stays inside the viewport. A legacy selector that clipped the select wrapper to one pixel was fixed and the real sorting workflow is tested. |
| P2: Streaming selectors lost context when the shell scrolled away | Compact visible View/Provider labels accompany the real capability-gated selectors. Provider search semantics are unchanged. |
| P1: secondary Books hero action had poor contrast in light appearance | Every action over the book stage uses on-art roles; primary actions use the approved neutral fill. Light/dark browser accessibility checks cover the result. |
| P1: moving the first continuation into the Books hero lost its direct resume action | Continue reading uses the existing saved reading file and checkpoint. Details remains alongside it. A boundary check verifies the saved file route without checkpoint mutation; the real EPUB/CBZ journey checks persisted resume. |
| P2: no-art stages could resemble an unexplained load failure | Existing real poster/colour fallbacks and focal framing remain; the broad stage no longer disappears when its first title lacks a backdrop. Home fields retain explicit contrast. |
| P2: remaining local typography, side accents and header contrast | Watch title overrides, fallback card leading, keyboard shortcut contrast and two Music status side accents were corrected. Management retains semantic status/error text. |

Assessment B scanned all 11 feature directories independently. Initial source findings: 14; final: 12. Both actionable Music side-accent warnings were removed. Remaining warnings are 10 image-source detections in guarded runtime image/fallback code (including two intentional reader sanitization test payloads), plus two approved gallery corner-marker geometry false positives. No runtime image guard was removed to silence a detector.

Mutable browser preflight and detector injection succeeded in fixture journeys for Home, Movies, Music and real-cover Books. The four approved light-theme cream-palette notices are brand-contract exceptions, not new palette decisions. Home also exposed shortcut contrast and fallback leading, which were corrected; its two hero text warnings were compositing false positives. These were ephemeral headless overlays: no persistent human-visible browser overlay is claimed. Temporary review servers and harnesses were stopped/removed.

## Production limits and performance

The actual runtime/backend remains the integrated production contract: authenticated recent album aggregation, member `ui_prefs`, scoped search, and validated ComicInfo bookmarks; no new schema migration is introduced by this correction. Existing fallback/page-only archive semantics remain explicit.

The final build is approximately 81 KB aggregate CSS gzip versus 76.6 KB in the first integration. The bounded aggregate allowance is 82 KB; the shared core is approximately 7.3 KB with a 7.4 KB allowance for canonical controls and artwork scrims. Lazy JavaScript ceilings and dependencies are unchanged. Artwork fixtures are absent from the shipping bundle.

Full repository/Core/native UI gates remain unrun under the owner's focused-testing instruction. External provider credentials/network playback, physical casting and physical CBR/PDF qualification are not claimed. Production deployment verification covers the running revision/assets/health/configuration; fixture and real-stack journeys provide authenticated behavior evidence without capturing private household media.
