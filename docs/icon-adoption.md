# Shared Lumina icon adoption

## Source and regeneration

The approved 104 designs and 40 solid alternatives (144 SVG masters) live in
`assets/icons/`. The supplied geometry, semantic names, previews and style guide
are preserved. `source/icons.json` owns editable geometry; `source/build.py`
owns exports. The React export additionally forwards SVG refs.

Run from the repository root:

```sh
python3 scripts/lumina_icons.py sync
python3 scripts/lumina_icons.py check
python3 scripts/lumina_icons.py check --render # macOS Core Graphics
```

The sync command updates the iOS/tvOS template image catalog, shared Swift helper,
React helper and SVG sprite. The production browser adapter references a
content-hashed external sprite, keeping all 104 designs out of the shared UI
JavaScript bundle. A CSS URL makes that asset discoverable by the offline shell.
Both native targets enroll the same catalog.
Do not edit synchronized copies. `check` validates export drift, safe SVG markup,
unique names, native collisions, template behavior and required previews.

## Adoption coverage

- iOS and tvOS: 209 existing stock/legacy references and the new Account Requests
  action use approved glyphs. Fourteen existing consumer files and a shared
  Label/Button adapter cover navigation, media, playback, books, settings and
  requests. Explicit sizing preserves visibility; labels, hit targets and focus
  remain with their original controls. Legacy LuminaNav raster references are gone.
- Browser application: 121 consumer files route through `frontend/src/ui/icons.tsx` (the compatibility export of
  `frontend/src/luminaIcons.tsx`).
  Of 133 distinct imported symbols, 93 map to approved geometry and 40 remain
  centralized exceptions. Route artwork uses approved selected alternatives.
- Standalone marketing website (`lumina-website` repository): search, external
  links, disclosure and back/forward actions embed five unchanged SVG masters.
  Link/button text remains the accessible name. The shared sunrise mark, favicon
  and launcher icon remain independent brand assets.
- Server Python APIs do not render interactive controls. QR codes, progress/data
  visualization SVGs and media illustrations are not interface icon substitutes.

## Deliberate semantic exceptions

Native displayed SF Symbols remain for replay (4), books/reader (5), reading
appearance (1), alphabet browsing (1), conversion (2), radio (1), pending clock (1)
and license document rows (2). Native OS-owned controls retain platform behavior,
including route pickers, system status, and the floating PiP window.

Browser stock exceptions are listed in one place at the bottom of
`frontend/src/luminaIcons.tsx`: books, vehicles, clipboard/copy, clock, exercise,
visibility, documents, gaming, gauge, globe, drag grip, drive, layers, leaf,
location, mail, messages, moon, newspaper, palette, plugs, radio, replay, RSS,
save, scissors, shield, sparkle, sprout, square, medical, sun, telescope, trophy,
profile edit, utensils and wrench. The existing Books route and theater-mode
frame also retain exact semantic glyphs. Do not silently replace these with an
unrelated approved icon. No additional geometry was invented during adoption.

## Focused qualification

- Canonical/export contract: 144 masters, 148 unique sprite symbols, no native
  name collisions; 576 successful transparent renders at 20/24/28/96 pixels.
- Browser production build and typecheck passed; 8 focused test files passed
  (82 tests), covering mappings, solids, selected aliases, refs, sizing, classes
  and accessibility. Final UI is 9.9 KB gzip against its unchanged 10.5 KB cap;
  the separate shared icon adapter is 2.1 KB against a new 3 KB cap. The hashed
  sprite is 6.45 KB gzip. Externalizing geometry reduced UI + icon JavaScript
  from the initial inline implementation's 18.1 KB to 12.0 KB gzip.
- The focused production offline-browser workflow passed: the hashed sprite is
  included in the prepared cache and the shell works with the server disconnected.
  Production browser SVG rendering was also inspected visually.
- iOS and tvOS Release simulator builds passed. Native changed Swift files also
  passed parser checks and the shared icon controls passed isolated typechecking. The final incremental
  builds include the parent accessibility labels and 24-point tvOS defaults.
  iPad playback rendering was inspected in the simulator with synthetic media.
- Website static build/link check and focused icon/navigation accessibility check
  passed; search opening/dismissal and icon alignment inspected in-browser.

Full suites and native release archives are intentionally excluded from this
ongoing dogfood cycle. Production deployment and native release status must be
reported separately from source adoption.
