# Lumina media icons

A bespoke SVG system for Lumina's native and web media interfaces: **104 icons and 40 solid alternates, for 144 master SVGs**. The original Home, Library, Requests, and User settings artwork is preserved, including its four selected versions.

The SVGs are the production artwork. The preview sheets are rendered from that geometry. Background panels, warm indicators, text, and example controls in the presentation are not baked into the icon files.

## What's included

| Family | Icons | Coverage |
|---|---:|---|
| Navigation | 12 | Original four destinations, search, discover, back/forward, chevrons, menu, close |
| Playback | 20 | Transport, ten-second seeking, shuffle/repeat, sound, subtitles, audio tracks, speed, queue, sleep timer |
| Viewing | 10 | Fullscreen, picture in picture, cast, AirPlay, devices, headphones, fit/fill |
| Media | 10 | Movies, series, music, albums, artists, playlists, photos, Live Photos, video, podcasts |
| Collections and actions | 16 | Favorite, bookmark, rating, add/remove, check, overflow menus, edit/share/link, folders, filter/sort, history |
| Transfers and server | 12 | Downloads, upload, sync, cloud states, server, Wi-Fi states, trash, restore, backup |
| Account and system | 16 | Profiles, settings, notifications, security, information, help, warning, calendar, logout, QR scan, grid/list, external link |
| Photo tools | 8 | Camera, crop, rotation, zoom, slideshow, photo stack |

Open `catalog.html` locally to browse the full set. It supports search, categories, light/dark presentation, outline/solid style, size adjustment, and copying IDs or SVG markup. `manifest.json` is the machine-readable inventory, including an icon's meaning, category, file paths, native name, and whether a solid alternate exists.

## Design contract

- **Coordinate frame:** `viewBox="0 0 24 24"` for every master.
- **Structural stroke:** 1.75 units, rounded caps and joins; a few subordinate details use a lighter stroke where needed.
- **Recommended everyday size:** 24–28 px/pt. Larger playback controls can use the same vectors in a larger host button.
- **Color:** one foreground color on a transparent background. Web masters use `currentColor`; native copies use literal black for template rendering.
- **Geometry:** generous interior space, rounded silhouettes, and simplified details that relate to the original navigation family.
- **Solid artwork:** real transparent cutouts where needed, rather than a background-colored patch.
- **Dependencies:** no embedded bitmap, icon font, external font, filter, mask, script, or remote asset in the master SVGs.

Do not resize one icon's internal paths to compensate for a control layout issue. All assets share a stable coordinate frame; optical balancing belongs in the source artwork, and hit areas belong in the host component. Keep the icon's aspect ratio square.

These are ordinary vector image assets, not custom SF Symbols. They do not carry SF Symbol variable-weight, interpolation, or symbol-animation metadata. Lumina's official sunrise mark is unchanged.

## Color, style, and interaction state

Bind artwork to Lumina's existing semantic foreground tokens. The reference sheets use light ink `#15130F`, cream `#EFE9DD`, charcoal `#151517`, and the warm accent `#DBB688`. Those references are not a request to create another app theme or hardcode a new palette in controls.

**Outline and solid describe artwork styles.** They do not determine whether a feature is enabled, whether a button is pressed, or whether playback is running. A solid play triangle is still a play action. While playing, explicitly swap the action icon to `pause` and change the accessible label to “Pause.”

For the original four navigation destinations, their selected artwork is exposed through the new helpers' `solid` variant. The original `svg/selected/` files remain available for compatibility. Other solid alternates live in `svg/solid/`.

Not every icon has a useful solid version. Both helpers fall back to the outline geometry when `variant="solid"` or `.solid` is requested for an icon without an alternate. This avoids inventing a filled shape that weakens the meaning. Check `hasSolid` in the manifest when a design requires a specific alternate.

Keep selected indicators, circles, background pills, borders, badges, labels, and progress rings in the host component. Navigation can combine its solid artwork with Lumina's existing warm indicator or stronger label. Toggles such as shuffle and repeat should expose their state independently through the existing visual and accessibility treatment. A color change alone should not be the only indication of selection.

Preserve focus, pressed, disabled, loading, and destructive treatments. The artwork size does not define the button's hit target.

## Choose the correct transport icon

| Icon IDs | Intended meaning |
|---|---|
| `back`, `forward` | Navigate through app history or the current navigation flow |
| `previous`, `next` | Move to the previous or next item, track, chapter, or episode |
| `rewind`, `fast-forward` | Scan backward or forward within the current item |
| `seek-back-10`, `seek-forward-10` | Jump ten seconds within the current item |
| `play`, `pause`, `stop` | Distinct transport actions; swap the artwork to match the available action |
| `repeat`, `repeat-one` | Repeat a group/queue or repeat the current item |
| `download`, `download-done` | Download action or completed local availability |
| `sync`, `backup`, `restore` | Synchronize, protect a copy, or recover a trashed item |

Do not use the navigation arrow as “previous track,” or a rewind icon for a fixed ten-second jump. The visible icon, tooltip, accessible label, and action should agree. If your player uses a different seek interval, create a matching numeric asset rather than labeling the ten-second artwork “Skip 30 seconds.”

The repeat controls illustrate different modes; playback logic remains in the app. Likewise, the presence of a cast, AirPlay, Live Photo, or backup icon does not imply that the corresponding feature is implemented or available on the current device.

Handle right-to-left layout intentionally at the component level. Do not mirror the entire pack: media transport direction, numeral forms, and device/service symbols have semantics distinct from navigation flow.

## Native SwiftUI integration

`native/LuminaMediaIcons.xcassets` contains **144 ordinary Image Sets**, each with a black SVG, template rendering intent, and preserved vector representation. Add the catalog to the relevant targets through the checkout's existing project-generation workflow, then add or adapt `native/LuminaIcon.swift`.

```swift
LuminaIcon(glyph: .play, variant: .solid, size: 28)

LuminaIcon(glyph: .userSettings, variant: .outline, size: 24)
```

`LuminaGlyph` uses Swift-friendly names such as `.userSettings` while file and web IDs use kebab case. The helper inherits the containing view's foreground styling. Its decorative image should be paired with an accessible label on the enclosing control.

```swift
Button {
    togglePlayback()
} label: {
    LuminaIcon(
        glyph: isPlaying ? .pause : .play,
        variant: .solid,
        size: 28
    )
}
.accessibilityLabel(isPlaying ? "Pause" : "Play")
```

For direct asset use:

```swift
Image("LuminaPlaySolid")
    .renderingMode(.template)
    .resizable()
    .scaledToFit()
    .frame(width: 28, height: 28)
```

The four original navigation pairs keep their names: `LuminaHome` / `LuminaHomeSelected`, `LuminaLibrary` / `LuminaLibrarySelected`, `LuminaRequests` / `LuminaRequestsSelected`, and `LuminaUserSettings` / `LuminaUserSettingsSelected`. Other alternates use a `Solid` suffix, such as `LuminaPlaySolid`. The helper handles that naming difference.

**If the original navigation catalog is already installed, merge the new image sets or replace that catalog. Do not compile two catalogs containing the same eight original asset names.** The original artwork remains the same. The earlier `LuminaNavigationIcon` helper can coexist with the new `LuminaIcon` helper because their types have different names, but both should resolve the same single set of navigation assets.

Use ordinary Image Sets, not Symbol Image Sets. Keep the app's current navigation and player architecture. Validate asset import, template tinting, light/dark appearances, and actual control sizes in a local Xcode build.

## Web integration

### React

`web/LuminaIcon.tsx` contains the geometry and accepts an icon name, optional variant, size, optional accessible label, and normal SVG props.

```tsx
<button
  type="button"
  aria-label={isPlaying ? "Pause" : "Play"}
  onClick={togglePlayback}
>
  <LuminaIcon
    name={isPlaying ? "pause" : "play"}
    variant="solid"
    size={28}
  />
</button>
```

The default artwork is decorative; put the action's accessible name on its button or link. For a meaningful standalone image, pass `label="Downloaded"`. Avoid repeating the same accessible name on both the image and its enclosing control.

```tsx
<LuminaIcon name="download-done" label="Downloaded" size={24} />
```

Set the CSS `color` property through the existing theme. Keep SVG stroke width consistent across the family. For an actual toggle, use the appropriate control semantics, such as `aria-pressed`, independently of the selected artwork.

The supplied dynamic renderer includes the full geometry map. **Per-icon tree shaking is not guaranteed.** If bundle size matters, use only the individual SVGs required by a screen or adapt the generator to produce per-icon modules. Do not assume that importing the component includes only the icon named at one call site.

### Individual SVGs and sprite

Inline an individual SVG when it should inherit `currentColor`. An external `<img src="icon.svg">` does not inherit the parent's CSS color into its document; use inline SVG, a CSS mask, or an explicit-color export for that use case. Standalone master SVGs carry a label; when placing one inside an already labeled control, mark it decorative to avoid duplicate announcements.

`web/lumina-media-sprite.svg` exposes `lumina-{id}` symbols and `lumina-{id}-solid` symbols for all 40 alternates. The original four also retain `lumina-home-selected`, `lumina-library-selected`, `lumina-requests-selected`, and `lumina-user-settings-selected` aliases.

```html
<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
  <use href="#lumina-play-solid"></use>
</svg>
```

Inline the sprite definitions once for this fragment-reference pattern. Keep the outer SVG's square viewBox, size, and accessible/decorative semantics.

## Package map and maintenance

| Path | Purpose |
|---|---|
| `svg/` | 104 base masters |
| `svg/selected/` | The four unchanged original navigation selected masters |
| `svg/solid/` | 36 additional solid masters |
| `native/` | Template asset catalog and SwiftUI helper |
| `web/` | React renderer and symbol sprite |
| `source/icons.json` | Editable canonical geometry and naming data |
| `source/build.py` | Python standard-library exporter |
| `manifest.json` | Complete inventory and integration metadata |
| `catalog.html` | Offline searchable browser and copy tools |
| `preview/` | Overview, light/dark catalog sheets, solid variants, and size checks |

Edit `source/icons.json` when changing an icon, then run the exporter from the package root:

```sh
python3 source/build.py
```

Treat exported SVGs, native copies, React geometry, and sprite definitions as synchronized outputs. Avoid fixing one copy and leaving the others inconsistent. The optional `source/render.py` regenerates all five preview sheets using Pillow and Inkscape; its header documents font overrides. Re-render the affected preview and inspect the icon at its real display size after a geometry change; enlarged inspection alone can hide collapsed gaps and heavy intersections. Preserve existing IDs and native names unless you deliberately provide a migration.

From the repository root, use the integration wrapper to regenerate and mirror
the package's outputs into the native and web applications:

```sh
python3 scripts/lumina_icons.py sync
python3 scripts/lumina_icons.py check --render
```

`sync` runs the standard-library exporter above, then updates the shared Apple
asset catalog and SwiftUI helper plus the React helper and sprite. `check`
verifies the full source/export inventory, byte-for-byte synchronization,
native target enrollment, and approved preview dimensions. On macOS,
`--render` also rasterizes all 144 masters at 20, 24, 28, and 96 pixels with
macOS Core Graphics and rejects blank artwork or opaque backgrounds.

## Verification and integration checklist

All 144 master SVGs passed XML and file-reference checks and rendered successfully with Inkscape at 20, 24, 28, and 96 px. The rendered alpha bounds remain inside their viewports. The icon families and solid alternatives were visually inspected, and the eight original navigation masters were verified byte-for-byte against the earlier pack. All 144 native Image Sets have matching source files; the sprite contains 144 artwork symbols plus four legacy selected aliases. The offline catalog script passed a JavaScript syntax check.

No Xcode/device build, React application build, or live Lumina integration was performed. Those checks remain the integrating agent's responsibility.

- Check every icon used by the app at its actual size, in light and dark themes.
- Verify that outline/solid swaps do not shift layout.
- Confirm transport icons and accessible names match their actions.
- Preserve navigation routes, playback logic, focus behavior, hit targets, and badges.
- Check selected, disabled, loading, and destructive appearances in their real controls.
- Confirm old and new native catalogs do not introduce duplicate asset names.
- Run the checkout's focused checks and required verification gates.

## References

- [Lumina brand identity contract](https://github.com/shiftedx/lumina/blob/main/apple/docs/brand-assets.md)
- [Apple: Adding images to your Xcode project](https://developer.apple.com/documentation/xcode/adding-images-to-your-xcode-project)
- [Apple: Template rendering](https://developer.apple.com/documentation/swiftui/image/templaterenderingmode)
- [Apple: Custom symbol requirements](https://developer.apple.com/documentation/uikit/creating-custom-symbol-images-for-your-app)

The geometry is custom artwork for this package and does not depend on an external icon library. Familiar transport and device conventions are retained so that the controls remain understandable.
