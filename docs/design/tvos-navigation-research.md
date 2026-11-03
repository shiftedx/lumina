# Lumina tvOS navigation research and rework proposal

Research date: November 2, 2026. Recommendation: add a permanent Library destination and an explicit Requests label for eligible members, and separate failed navigation discovery from an empty library.

## Scope and version caveats

- Plex’s documentation calls its pattern the **Big Screen** interface and explicitly
  lists Apple TV among the products that use it. It is a behavioral description,
  not a claim that every Apple TV release has identical visual treatment.
  [Plex: Navigating the Big Screen Apps](https://support.plex.tv/articles/navigating-the-big-screen-apps/)
- Swiftfin is Jellyfin’s native iOS and tvOS client. The navigation statements below
  come from its current upstream documentation/repository, rather than Jellyfin Web
  or another third-party Apple TV client. They describe the project’s intended
  behavior and may differ from a particular App Store build.
  [Swiftfin repository](https://github.com/jellyfin/Swiftfin) ·
  [Swiftfin common issues](https://github.com/jellyfin/Swiftfin/blob/main/Documentation/common_issues.md)
- The product comparisons are therefore pattern evidence, not a request to copy
  either product’s information architecture or visual design.

## Plex: a persistent source selector, then local tabs

Plex’s Apple TV app is documented as using a navigation sidebar that can be opened
from the left edge almost anywhere. The sidebar selects a **source**; the selected
source then exposes a top-level local tab set such as Recommended, Library,
Collections, Playlists, and Categories. In other words, its hierarchy is
`Home → source → source-specific browse mode`, rather than a single saved-items
destination standing in for all content navigation.
[Plex: Navigating the Big Screen Apps](https://support.plex.tv/articles/navigating-the-big-screen-apps/)

The source list is not fixed: Plex lets people pin, unpin, and reorder sources, and
keeps unpinned sources reachable through **More**. Its documentation says this
applies to the Apple TV Big Screen interface and can include multiple personal or
shared libraries. That gives frequently used libraries a direct route without
making less-used ones unreachable.
[Plex: Customizing the Big Screen Apps](https://support.plex.tv/articles/customizing-the-apps/)

Plex's redesign also tested a different hierarchy. Its 2025 Apple TV preview centralized personal libraries in a dedicated tab. In August 2026, Plex announced that the Apple TV preview was moving primary navigation back to a collapsible left panel, putting favorite libraries at the top level and making it easier to switch pages within a source. In September, Plex announced that the redesigned tvOS experience was starting to roll out. The August announcement establishes the preview design and the September announcement establishes rollout, without establishing that every installed build has the same controls. The reversal is evidence that access cost matters, particularly when viewers switch libraries often.
[Plex: Apple TV preview release notes](https://forums.plex.tv/t/release-notes-for-plex-experience-preview-apple-tv/903004) · [Plex: August navigation update](https://forums.plex.tv/t/navigation-updates-coming-to-preview-users/941751/1) · [Plex: September tvOS rollout](https://forums.plex.tv/t/plex-for-apple-tv/121357/175)

Plex also treats continuity as navigation state. Apple TV settings offer
“Remember selected tab” *per source* (for example, Library versus Collections),
which avoids returning a person to a generic landing view after they have chosen a
browse mode.
[Plex: Settings for Apple TV](https://support.plex.tv/articles/settings-plex-for-apple-tv/)

**Relevant pattern, not a visual prescription:** make library/source selection a
first-class, always-reachable navigation concern; let a selected library own its
browse submodes; preserve the person’s prior meaningful context; and make any
personalization reversible.

## Jellyfin Swiftfin: global type routes and an explicit per-library route

Swiftfin documents two distinct levels of media navigation on tvOS: the top-level
**Movies** and **Shows** sections aggregate by media type across the server, while
**Media** is the route for browsing by individual library. The project calls this
behavior intentional, while also acknowledging that it can be confusing. This is
strong evidence that global media-type browsing and exact-library browsing solve
different jobs and should not silently substitute for one another.
[Swiftfin: tvOS library navigation note](https://github.com/jellyfin/Swiftfin/blob/main/Documentation/common_issues.md#tvos-contains-the-wrong-items-under-movies)

Swiftfin’s documented library support is deliberately capability-specific: Shows,
Movies, Mixed, Music Videos, and Home Videos are listed as supported; Collections
are partial; Playlists, Music, Photos, and Books are not supported. A tvOS
navigation model should therefore derive visible destinations from real server and
client capability, instead of presenting a generic destination that leads to an
unavailable or empty experience.
[Swiftfin: Library support](https://github.com/jellyfin/Swiftfin/blob/main/Documentation/libraries.md)

**Relevant pattern:** distinguish aggregate discovery (for example, all movies)
from a predictable library browser; label the two clearly; and only advertise
destinations the active client can fulfill.

## Apple tvOS navigation evidence

Apple describes tvOS as a remote-first, focus-based interface: people use
directional input to move focus to every onscreen component, then select it; the
Menu button navigates back. Apple’s guidance is to keep navigation straightforward
and minimize text entry and other complex interactions.
[Apple: Interface fundamentals](https://developer.apple.com/documentation/technologyoverviews/interface-fundamentals)

Apple further says that tvOS users rely on directional focus to reach every UI
element, that focused elements can enlarge, and that layouts must leave sufficient
room for those focus states. It advises using system-provided focus behavior where
possible and avoiding unsolicited focus moves, because focus tells people where
they are.
[Apple: Focus and selection](https://developer.apple.com/design/human-interface-guidelines/focus-and-selection/)

At the product level, Apple asks tvOS apps to support familiar Siri Remote gestures,
embrace focus so people know what to do and where they are, keep artwork legible at
living-room distance, and make sign-in easy and infrequent.
[Apple: Designing for tvOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-tvos)

## Evidence-backed design constraints for any rework

1. There must be a direct, focus-reachable path to both the media collection and
   its library-level organization; a Watchlist alone cannot cover either job.
2. Global discovery routes and named-library routes should be intentional,
   distinguishable destinations, not hidden variants of the same control.
3. The primary navigation should have a stable focus order and sufficient spacing
   for focused expansion. Transient overlays should not create competing menus or
   unexpectedly steal focus.
4. Feature availability should be capability-driven. If a server feature (such as
   requests) is unavailable, it should not occupy a dead first-class destination;
   if it is available, it needs a clear route rather than a buried overflow action.
5. Restore useful context such as the previous library/browse mode where it does
   not conflict with a clear Home entry point. Plex’s per-source tab persistence is
   a concrete precedent for this behavior.

## What Lumina does today

Source reviewed at `dab3044c`. This is a code audit, not a reproduction on the household Apple TV.

- The top header directly lists Movies, TV, Anime, and Music when each is present in the member's navigation response. Each opens the corresponding Library surface, but there is no labeled Library destination in the header. Watchlist is a Home filter and survives even when the category list is empty. Requests, when enabled, is an icon alongside Search and Account. [TV shell and header](../../apple/App/tvOS/TVRootView.swift#L1404)
- The client calls authenticated `GET /Lumina/Settings/navigation`. Initial categories are empty, and any current navigation request failure clears categories, Requests, and Streaming. Watchlist-only is therefore consistent with loading, a failed response, or a successful response with no supported categories and Requests unavailable. It does not identify which happened on the reported device. [Native request](../../apple/Core/Sources/LuminaCore/LuminaClient.swift#L297) · [Store state update](../../apple/Core/Sources/LuminaCore/LuminaStore.swift#L1089)
- The server returns a fixed set of member-visible **category keys**, not individually named library sources. Usable media and member access determine which categories appear. Requests depends on the server setting and effective member policy. A TV without a Requests button could be correctly gated, or the client could have failed to load capabilities. Existing tvOS Requests already has a catalog and My requests, so a rework can reuse both. [Server policy](../../backend/app/services/navigation.py#L27) · [Native endpoint](../../backend/app/routers/jellyfin_settings.py#L339) · [Requests UI](../../apple/App/tvOS/TVRequestsView.swift#L35)
- The existing Home header scrolls away, while Library navigation stays pinned. At a browse root, Back currently replays a history of previously selected global destinations. The proposed hierarchy gives Back a predictable parent while preserving card and shelf focus restoration. [Native navigation history](native-home-library-navigation-followup.md)

Before claiming a device-specific root cause, compare the installed tvOS build and the native navigation response for the same member on the same server. Record the HTTP result, supported category keys and Requests Boolean only. Compare with the web session under the same account. A successful empty response, an HTTP or decode failure, and denied Requests access need different fixes.

## Proposed rework

Give **Library** a permanent, labeled place in the TV masthead. Separate global destinations from filters within a destination. Keep the masthead short and give the Library hub room to explain what is available.

```text
Home       Library       Watchlist       Requests*       Search       Settings
           └── Movies · TV Shows · Anime · Music · Saved Streaming*
```

`*` Requests is a labeled global destination for eligible members. Saved Streaming belongs inside Library when external Streaming is disabled and saved items are available. If external Streaming is enabled, retain its existing route as a labeled global destination; keep Search and Settings as trailing utilities with clear focus and accessibility labels. The logo is decorative. Library opens the category hub every time a user explicitly selects Library from the masthead. Back from a category restores the tile that opened it. A category's filters, grid, and scroll position belong to that category.

| Approach | Benefit | Cost |
| --- | --- | --- |
| **Short top masthead plus Library hub (recommended)** | The first screen clearly names Library and Requests. Reuses existing TV screens and gives categories readable targets. | Browsing a different category takes a second Select from the global masthead. |
| Collapsible left sidebar with visible shortcuts | Better for many named sources or frequent library switching. Categories can be grouped under Library. | Needs a new focus model across hero, shelves, grids, and sidebar. A collapsed rail must stay discoverable. |
| Restore or expand the current category strip | Fastest route when every label is visible. | Still uses successful capability discovery as the only entry to Library and competes for width with global destinations. |

Pick the short masthead first because Lumina currently supports a bounded set of TV categories. Revisit a left sidebar if it gains named libraries or remote trials show that frequent category switching is slower with the hub. Plex-style favorites and pinning are future decisions. A genuine named-library view would require a server contract with stable source IDs and member-visible routes.

### Availability and feedback

- **Loading:** show the Library destination immediately. Inside Library, show a loading state until the current member's capabilities arrive.
- **Ready:** render supported, visible categories. A confirmed empty response gets an honest empty state, distinct from network failure. Unsupported categories get no dead links.
- **Refresh failure:** keep the last successful category list in memory for the same server, account, and session while reporting the failure and offering Retry. Requests stays subject to authorization. On an initial failure, Library remains reachable and explains why it can't list media. A contextual status can explain that Requests availability could not be checked without exposing a request route.
- **Authoritative access change:** replace capabilities after a successful response. Clear them on sign-out, server or member change, or invalid session. On authorization errors, discard affected capability state and rely on server route checks. If a selected category disappears, return to Library and focus an available tile or the empty state. If Requests is revoked while open, exit it to Home and explain why.
- **Requests:** show the word Requests for eligible accounts and preserve the existing My requests route. Separate disabled by the household, unknown, and denied in diagnostics wherever the server can actually distinguish them. The current Boolean alone cannot distinguish disabled from denied.

### Siri Remote behavior and rollout

1. Add the stable Library route, hub, and labeled Requests entry. Reuse the existing category views and Requests policy. Keep Home's hero and shelves full-width, and make Up from the leading content region return to the masthead. On Library, keep navigation accessible while grids scroll.
2. Distinguish unknown, loaded, and failed navigation in shared state, with session-scoped retention on transient failures. Audit iOS consumers before changing shared behavior. Add an owner-visible diagnostic for navigation load failures without logging credentials, real hostnames, or private media.
3. On TV, moving focus never opens a destination. Select enters it. Down enters the hero action or the category grid. Back dismisses overlays and unwinds detail to the originating card, category to its Library tile, and child Requests routes to Requests. At a top-level screen, Back from content returns focus to the active navigation item. From the masthead, Back returns to Home once; Back from the focused Home root hands control to the system. Switching tabs never adds a chronological Back entry. Keep selected and focused states visually distinct.

For acceptance, start at Home and use only the Siri Remote to enter Movies, switch to Music via Library, and open Requests and My requests when eligible. Confirm Select activates the focused card, Back restores the exact card after details and playback, and Search returns focus to its trigger. Test initial load, valid empty libraries, a transient error and retry, category removal, Requests on/off, and an account change. Extend the existing [TV navigation and capability workflows](../../apple/Tests/UITests/NativeProofTests.swift#L311) and qualify focus, legibility, and the exit path on a physical Apple TV at both 1080p and 4K where available.
