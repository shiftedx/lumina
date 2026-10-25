# Lumina icon style guide

**Agent handoff · Version 1.0 · October 24, 2026**

Use this document to create additional icons that belong to the approved Lumina navigation and media family. It defines the visual language, SVG construction rules, image-generation workflow, export conventions, and acceptance checks.

**Baseline:** `LUMINA_MEDIA_SVG_SET.zip` contains 104 designs and 40 solid alternates, for 144 master SVGs. Those counts describe the first release; they are not limits on future additions. The original Home, Library, Requests, and User settings icons establish the family and retain their original outline and selected artwork.

**Production method:** the approved icons were authored as SVG geometry. Their presentation sheets were rendered from those SVGs. Image generation is available for exploring an unfamiliar visual idea, but a generated bitmap does not establish precise coordinates, stroke widths, or vector cutouts. Final icons must be constructed and inspected as real SVG artwork.

This document contains enough specifications, prompts, and exact SVG examples to establish the style independently. For the closest match, give the implementing agent the existing ZIP alongside this file.

## 1. The brief in ten rules

1. Extend the existing family. Preserve approved geometry, names, and compatibility unless the user requests a redesign.
2. Use a `24 × 24` coordinate frame and a `1.75`-unit structural stroke with rounded caps and joins.
3. Give each icon one immediately understandable meaning, a strong silhouette, and only the details needed for recognition.
4. Match the perceived weight and size of nearby approved icons. Optical balance matters more than identical bounding boxes.
5. Use one inherited foreground color and a genuinely transparent background.
6. Keep counters, gaps, and off-state interruptions open at the intended display size.
7. Create a solid alternate only where it improves the family. Solid is an artwork style; the control owns interaction state.
8. Use image generation only when visual exploration is useful. Build the final SVG with editable geometric paths.
9. Render the actual exported SVG beside approved references before accepting it. Inspect at 20, 24, and 28 px and enlarged.
10. Update the canonical geometry and regenerate synchronized web, native, sprite, catalog, and preview outputs.

## 2. What Lumina should feel like

The icon family is **warm, precise, restrained, and familiar**. Its character comes from soft corners, generous spaces, balanced proportions, and a few memorable object silhouettes.

The Requests popcorn bucket is an example of the permitted personality: a recognizable object, a rounded canopy, two simple stripes, and one restrained kernel detail. The rest of the family supports that personality through consistent geometry. A conventional play triangle still needs to read as a play triangle.

Use these design decisions:

| Quality | Express it through |
|---|---|
| Warmth | Rounded terminals, softened corners, comfortable spacing |
| Precision | Consistent stroke weight, deliberate alignment, clean curves |
| Personality | A distinctive silhouette or one meaningful detail |
| Clarity | Familiar media conventions and readable negative space |
| Restraint | Few interior marks, one foreground color, simple geometry |
| Cohesion | Reusing related shapes, proportions, and construction methods |

Keep gradients, glow, bevels, shadows, perspective, paper grain, sketch wobble, hatching, multicolor fills, and decorative frames out of the icon masters. Warm paper and charcoal belong to the presentation or app surface. They are not textures inside the glyph.

Do not add Lumina's sunrise mark to each icon. Do not reconstruct or reinterpret the official logo as part of an icon extension.

## 3. Start from the actual references

Before drawing, inspect the package's overview and the nearest three existing icons. Read their SVG geometry as well as viewing their renders.

| Reference | What it establishes |
|---|---|
| `home` | Rounded roof, extended eaves, arched doorway, balanced filled selection |
| `library` | Separated narrow forms, small label cutouts, one leaning spine |
| `requests` | Expressive object silhouette with minimal interior detail |
| `user-settings` | Profile plus secondary controls without a crowded badge |
| `play`, `pause`, `previous`, `next` | Transport proportions and optical weight |
| `photos`, `camera`, `photo-stack` | Consistent frames, lenses, landscape marks, and layered forms |
| `settings` | Slider construction that relates to User settings |
| `cloud-off`, `notifications-off`, `wifi-off` | Deliberate gaps around diagonal off-state slashes |
| `favorite`, `bookmark`, `subtitles` | Different valid approaches to solid alternates |

For a new audio control, compare it to playback icons. For a new photo action, compare it to photo tools. Use the four navigation originals to check overall family character, rather than borrowing their details indiscriminately.

The package has these useful references:

- `source/icons.json`: canonical geometry and semantics.
- `svg/`: individual masters.
- `preview/lumina-media-icons-overview.png`: curated presentation specimen.
- `preview/lumina-media-icons-catalog-light.png` and `preview/lumina-media-icons-catalog-dark.png`: complete outline inventory.
- `preview/lumina-media-icons-solid-variants.png`: outline/solid comparison.
- `preview/lumina-media-icons-size-check.png`: all outline icons at small sizes.
- `catalog.html`: searchable inventory with actual SVG source and style switching.

If only this guide is available, render the exact examples in Section 8 to create a small reference sheet. Record that the full family was unavailable; do not claim an exact comparison against unseen assets.

## 4. Geometry specifications

Distinguish the **fixed family contract** from **starting points for new artwork**. Starting points help the next icon fit; they are not a reason to reshape approved originals.

### Fixed contract

| Property | Requirement |
|---|---|
| SVG coordinate frame | `viewBox="0 0 24 24"` |
| Nominal dimensions | `width="24" height="24"` |
| Structural stroke | `stroke-width="1.75"` |
| Line caps and joins | `round` / `round` |
| Default fill | `none` |
| Web color | `currentColor` |
| Native template color | Literal black in generated native copies |
| Background | Fully transparent |
| Aspect ratio | Square frame; preserve proportions |
| Usual display size | 24–28 px/pt |
| Small-size inspection | 20, 24, and 28 px; inspect any smaller actual product size separately |
| Enlarged inspection | 96 px render or a clearly labeled enlarged sample |

The existing family uses `1.5`-unit strokes for a few subordinate details, including seek numerals and the popcorn kernel division. Keep this exception deliberate and local. Some approved solid transport stop bars have their own optical adjustment; preserve those rather than treating them as a new global stroke weight.

### Starting points for new shapes

| Property | Starting point and judgment |
|---|---|
| Center | Begin around `(12, 12)`, then correct perceived imbalance |
| Main footprint | Usually around 16–20 units across, depending on the shape |
| Typical centerline extent | Roughly `2.25–21.75`; this is not a mandatory bounding box |
| Common corner radii | Around `1–2.25` units; smaller subordinate frames can differ |
| Coordinate increments | Prefer simple quarter-unit increments where practical; curves and optical corrections may use other values |
| Clear gaps | Aim for about `1–1.5` units of visible separation after stroke expansion |
| Interior detail | Usually one to three meaningful marks beyond the main structure |
| Adjustment size | Make small optical changes, commonly `0.125–0.25` units at a time |

Stroke thickness extends beyond the path centerline. For two parallel strokes, the clear gap is approximately:

```text
clear gap = distance between centerlines − (stroke A + stroke B) / 2

Two 1.75-unit strokes with a 1.5-unit clear gap:
centerline distance = 1.75 + 1.5 = 3.25 units
```

Use this to diagnose collapsed gaps, then judge the actual render. Curves, diagonal joins, antialiasing, and nearby filled areas also affect perception. Existing compact controls can have smaller counters; retain them when they remain readable.

### Optical balancing

Compare the new icon to its neighbors at the same rendered size. A circle, a thin arrow, and a filled triangle will not feel equally large merely because their bounding boxes match.

- Let circles and rounded silhouettes occupy enough area to feel as substantial as rectangular neighbors.
- Balance play triangles by their perceived visual mass; do not blindly center their bounding boxes.
- Keep paired controls such as previous/next and rotate-left/right matched in footprint and construction.
- Keep outline and solid variants in the same coordinate frame and visually anchored so swapping them does not jump.
- Match the family before adding decorative complexity.

## 5. Constructing recognizable icons

### Silhouette first

Draw the simplest recognizable outer form. Check it at 24 px. Add an interior detail only when it clarifies the action or object.

Use simple arcs, cubic curves, rounded rectangles, circles, and short path segments. Prefer a small number of purposeful geometric elements over an automatic trace containing hundreds of nodes. Inspect curve joins for bumps, kinks, and accidental flat spots.

For composite icons, make one form dominant. Keep secondary marks sufficiently separate to remain readable. If a second badge requires several miniature symbols, simplify the metaphor or use a separate status indicator in the host component.

### Preserve semantic distinctions

| Meaning | Existing visual vocabulary |
|---|---|
| Navigate back/forward | Shaft arrow |
| Previous/next item | Triangle plus vertical stop bar |
| Scan backward/forward | Double triangle |
| Fixed ten-second jump | Circular arrow plus geometric numeral |
| Movies | Clapperboard |
| Series | Stacked episode cards |
| Video | Wide frame with play triangle |
| Album | Record and sleeve |
| Playlist | Musical list |
| Library | Original media spines |
| Sync | Opposing curved arrows |
| Backup | Protective shield and upload arrow |
| Restore | Trash receptacle and return arrow |

Reuse an existing icon when it already expresses the requested action. Create a new icon when there is a meaningful difference to communicate. Avoid producing alternate-looking duplicates solely to increase the inventory.

### Off states and slashes

Use the existing diagonal direction, from upper left to lower right. Interrupt the underlying geometry where the slash crosses it so the intersection remains clean.

Use real missing geometry or transparent cutouts. A cream-colored patch will fail on charcoal, and a black patch will fail in another tint. Inspect the slash, gaps, and remaining silhouette in both outline and solid styles.

### Numerals and direction

Draw small fixed numerals as geometry, following the current seek icons. They must remain readable at the intended control size and must match the action's actual interval.

When mirroring a timed-seek arrow, keep the numeral unchanged. Handle right-to-left navigation intentionally in the consuming component; do not mirror the entire media icon collection automatically.

## 6. Outline, solid, and interaction state

The base artwork is generally an outline. Small dots or similarly essential marks may be filled within that outline style.

A solid alternate may:

- Fill the main silhouette, as with play, pause, favorite, and bookmark.
- Fill only the focal element, as with some media and device controls.
- Use compound paths with transparent counters, as with subtitle bars or folder-add's plus.
- Combine a filled body with outline details, as with Requests.

Do not obtain every solid alternate by indiscriminately applying `fill="currentColor"` to every path. Inspect what becomes enclosed and what disappears. Use `fill-rule="evenodd"` with appropriate compound geometry where needed, or construct the silhouette from separate shapes. Keep internal openings genuinely transparent.

An alternate is useful only when the icon remains recognizable and its visual weight fits the other solid controls. It is acceptable to omit it. The supplied helpers fall back to outline for icons without a solid alternate.

**Artwork style and control state are separate:**

- A solid play triangle remains a Play action. During playback, change the icon to Pause and update the accessible label.
- A selected navigation destination can use solid artwork plus the existing selection indicator or stronger label.
- Shuffle, repeat, and similar toggles need visible and accessible state independently of icon fill.
- Pressed, disabled, focused, loading, destructive, and notification treatments belong to the host component.

Keep background circles, pills, badges, labels, progress rings, and selected underlines outside the SVG master.

## 7. Color and presentation

Use the app's existing semantic foreground tokens. These reference values describe the approved presentation; they do not require creating a second theme in the application.

| Role | Reference value |
|---|---|
| Light foreground | `#15130F` |
| Dark foreground | `#EFE9DD` |
| Charcoal surface | `#151517` |
| Warm accent | `#DBB688` |
| Presentation paper | `#F4F0E8` |
| Presentation light card | `#FCFAF6` |

The icon itself uses one color. Reserve warm accent treatment for meaningful selection or primary-control context in the surrounding interface. Do not paint every glyph gold or assign unrelated colors to categories.

A presentation sheet may use paper, charcoal, typography, and spacing to show the artwork attractively. Identify the sheet as a specimen. Its icons must be rendered from the actual deliverable SVG files; labels and example control surfaces remain presentation elements.

## 8. Exact SVG format and approved examples

### Master wrapper

Use this wrapper for a standalone meaningful image. Insert geometry inside it and set the real accessible label.

```svg
<svg xmlns="http://www.w3.org/2000/svg"
     width="24" height="24" viewBox="0 0 24 24"
     fill="none" stroke="currentColor" stroke-width="1.75"
     stroke-linecap="round" stroke-linejoin="round"
     role="img" aria-label="Descriptive label">
  <!-- Editable geometric elements go here. -->
</svg>
```

For decorative artwork inside an already labeled button or link, the integration layer should remove redundant image semantics and set `aria-hidden="true"` and `focusable="false"`. The existing React and SwiftUI helpers already treat their default artwork as decorative.

Use `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, and simple `g` transforms. Keep a single root SVG. Avoid embedded bitmaps, `<text>`, fonts, filters, masks, gradients, scripts, `<foreignObject>`, remote references, and external `<use>` dependencies in master files. The generated symbol sprite is a separate export and may be consumed through `<use>`.

### Approved Play outline

This is an exact existing master, included as a construction reference. Preserve it when adding other icons.

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="Play">
  <path d="M8.1 4.78A1.2 1.2 0 0 0 6.25 5.8v12.4a1.2 1.2 0 0 0 1.85 1.02l10.1-6.2a1.2 1.2 0 0 0 0-2.04Z"/>
</svg>
```

Its solid variant wraps the same geometry in `<g fill="currentColor">…</g>`. That is appropriate for this closed silhouette; it is not a universal recipe for more complex icons.

### Approved Requests outline

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="Requests">
  <path d="M5.35 10.25a2.45 2.45 0 0 1-.85-4.52A2.55 2.55 0 0 1 8.1 3.9a2.8 2.8 0 0 1 4.8-.4 2.65 2.65 0 0 1 4.2 2.05 2.55 2.55 0 0 1 1.55 4.7"/>
  <path d="M12.9 3.5c-.75.7-.9 1.6-.45 2.35" stroke-width="1.5"/>
  <path d="M5.25 10.25h13.5l-1.7 9.02a1.5 1.5 0 0 1-1.47 1.23H8.42a1.5 1.5 0 0 1-1.47-1.23Z"/>
  <path d="m9.1 13.5.65 4M14.9 13.5l-.65 4"/>
</svg>
```

### Approved User settings outline

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="User settings">
  <circle cx="8.5" cy="7" r="3"/>
  <path d="M12.75 20.5H3.8a1.05 1.05 0 0 1-1.05-1.05v-.7a5.75 5.75 0 0 1 9.93-3.95"/>
  <path d="M14.75 13.5h.75m3 0h3M14.75 19h3m3 0h.75"/>
  <circle cx="17" cy="13.5" r="1.5"/>
  <circle cx="19.25" cy="19" r="1.5"/>
</svg>
```

The smaller counters in the sliders are deliberate existing details. Compare new controls against their actual render instead of applying a new gap rule to these approved originals.

## 9. Image-generation workflow and reusable prompts

### Choose the useful route

| Situation | Route |
|---|---|
| Familiar media control or simple extension | Construct SVG directly from the nearest approved references |
| Unfamiliar object or ambiguous metaphor | Describe the alternatives; optionally explore a raster concept, then construct SVG |
| User explicitly requests concept imagery | Use image generation for those concepts and label them as raster explorations |
| Existing SVG needs a geometric adjustment | Edit its vector source and rerender |
| Existing raster concept needs a visual revision | Use image editing according to the available tool's current instructions |
| Final deliverable must be SVG | Build and validate geometric SVG regardless of how the idea was explored |

Image generation can propose a silhouette. It cannot substitute for exact grid construction or verify whether a small counter survives at 24 px. Do not rename a PNG to `.svg`, embed it with `<image>`, or deliver an unreviewed automatic trace as a finished member of this family.

### Reference preparation

Inspect any local reference image before passing it into an image-editing workflow. A useful reference sheet contains the nearest three approved icons rendered from their SVGs, with consistent scale and neutral colors. Keep reference roles explicit: approved family sample, editable concept draft, or presentation context.

Use the image tool's active schema. In the tool environment used for this guide, the relevant fields are `prompt`, `transparent_background`, and the reference selectors `referenced_image_paths` or `num_last_images_to_include`.

- For a new text-only concept, use the prompt and `transparent_background: true`; omit reference selectors.
- For an edit of a local raster draft, inspect it first and supply its actual path through `referenced_image_paths` when that is the advertised interface.
- For an edit of conversation images without local paths, use the smallest supported `num_last_images_to_include` that includes the targets.
- Never supply both reference mechanisms in one call.
- Do not invent paths, image indexes, model names, output-format fields, size fields, or other parameters absent from the active tool schema.
- Ask the image tool for true transparency. A painted checkerboard is not alpha transparency.

For reference-driven exploration, one practical method is to render a copy of the nearest approved SVG into a raster draft, then ask the image tool to revise that draft's subject while preserving its visual language. Keep the approved source file intact. The resulting concept still requires fresh vector construction and inspection.

### Prompt A — one new raster concept

Replace the bracketed fields. This prompt is for an exploratory bitmap, not the final SVG.

```text
Use case: logo-brand
Asset type: one exploratory concept for a Lumina media-app icon

Primary request:
Create one icon representing [ACTION OR OBJECT]. The exact meaning is
[ONE-SENTENCE MEANING]. The visual metaphor is [CHOSEN METAPHOR].

Style:
Warm, precise, restrained geometric linework. Rounded line caps and joins.
Simple recognizable silhouette, generous negative space, and only the
interior marks needed for recognition. Match a family designed on a 24×24
grid with a 1.75-unit structural stroke. This grid is a visual target for
the concept; exact coordinates will be authored later as SVG.

Composition:
One isolated, optically balanced glyph, centered within a square frame.
Show it large enough to inspect. Keep comfortable transparent margins.
Use a single solid near-black foreground and a truly transparent background.

Constraints:
One icon only. No text, labels, numerals unless the action explicitly needs
them, UI panels, watermark, logo, background shape, or presentation board.
No gradients, shadows, glow, bevels, perspective, paper texture, sketch
wobble, decorative hatch marks, or colored category fills.
Avoid tiny badges, dense details, touching strokes, and closed-up counters.
The result must be simple enough to redraw with a small number of clean
SVG paths and basic shapes.
```

For a numeric action, replace the no-numerals clause with the exact required numeral and its meaning. Inspect the result carefully; redraw the number as geometry in the final SVG.

### Prompt B — revise a raster draft without style drift

Use this only with the actual draft/reference images included through the supported tool workflow.

```text
Edit target: the supplied raster concept for [ICON ID].
Approved style references: [IDENTIFY EACH PROVIDED REFERENCE AND ITS ROLE].

Change only [ONE CONCRETE ISSUE, SUCH AS THE GAP BETWEEN THE MAIN FORM
AND ITS SECONDARY MARK].

Preserve the chosen metaphor, overall silhouette, optical center, scale,
rounded terminals, uniform apparent stroke weight, single foreground color,
and true background transparency. Preserve all unrelated geometry.
Keep the design consistent with the approved Lumina references.

Remove the accidental intersection or crowded detail rather than adding
decoration. Return one revised raster concept. No labels or presentation UI.
```

If a draft is already close and the remaining issue is a precise coordinate or gap adjustment, make that adjustment in SVG instead of generating more raster variations.

### Prompt C — convert the chosen idea into production vectors

Give this to the coding/design agent after a concept is chosen, or use it directly for a familiar symbol.

```text
Implement [ICON ID] as a new member of the existing Lumina SVG family.
Meaning: [EXACT ACTION OR DESTINATION].
Nearest approved references: [THREE ICON IDS].

Read LUMINA_ICON_STYLE_GUIDE.md and inspect the existing SVGs first.
Construct editable vector paths on the 24×24 grid, with 1.75 structural
strokes, rounded caps/joins, currentColor, and a transparent background.
Use the selected raster concept only as a visual idea if one was supplied.
Reconstruct its geometry deliberately; do not embed or blindly trace it.

Produce the outline and, only if it reads clearly, a matching solid alternate.
Preserve all existing artwork and identifiers. Update source/icons.json,
regenerate the supported exports, and render the actual new SVG beside its
three references at 20, 24, 28, and 96 px on light and dark backgrounds.

Inspect the rendered files, fix concrete defects, and provide the actual
assets, preview, and a short record of verification. State which native or
web application builds were run. Do not claim integration that was not done.
```

## 10. Adding an icon to the existing package

### Step 1: define the action

Record the ID, label, exact meaning, category, nearest references, and whether a solid alternate would be useful. Check for an existing icon with the same meaning.

Use lowercase kebab-case IDs, such as `chapter-list` or `audio-description`. Reserve `-solid` and `-selected` for generated style symbols; do not use them as ordinary base-ID suffixes.

Check uniqueness after conversion to native names and Swift identifiers, including case-insensitive filename comparisons. Two distinct IDs can otherwise collapse to conflicting native names. Avoid Swift reserved words for new identifiers unless the generator consistently escapes declarations and references.

### Step 2: build and inspect the geometry

Use the fixed wrapper and the nearest existing construction. Inspect outline first, then create and inspect the solid alternate if appropriate. Use small targeted corrections rather than repeatedly changing several proportions at once.

### Step 3: update the canonical source

`source/icons.json` owns the geometry. Each icon object has:

| Field | Use |
|---|---|
| `id` | Unique kebab-case identifier |
| `label` | Human-readable name |
| `category` | Exact category string from the top-level category list |
| `meaning` | Clear action or destination semantics |
| `outline` | SVG body markup, without the root wrapper |
| `solid` | Optional SVG body markup for the alternate |
| `nativeName` | Optional deliberate native naming override |
| `legacySelected` | Existing compatibility flag; retain only on the original four |

**Omit `solid` entirely when no alternate exists.** The exporter tests for key presence; `null`, `false`, or an empty string does not correctly represent an absent alternate.

Use an existing category or also add the new category to the top-level `categories` list. Otherwise an icon can export successfully while being absent from the visual catalog.

### Step 4: regenerate the synchronized exports

From the extracted package root:

```sh
python3 source/build.py
python3 source/render.py
```

The export builder uses Python's standard library. The supplied preview renderer uses Inkscape and Pillow. On systems without its default URW font files, point `LUMINA_FONT_REGULAR`, `LUMINA_FONT_BOLD`, and `LUMINA_FONT_SERIF` to available font files. Those fonts affect presentation labels, not the icon geometry.

Do not edit only a generated SVG and leave its React, native, sprite, and catalog versions behind. If the agent cannot run the supplied exporter, synchronize the affected outputs explicitly and document what remains unverified.

### Step 5: account for first-release generator assumptions

These are known characteristics of the delivered generator, not changes already made by this guide:

| Location | Extension task |
|---|---|
| `source/build.py`, catalog introduction | Replace hardcoded `104` / `40` copy with source-derived counts or update it for the new release |
| `source/build.py`, manifest generation | The version is currently emitted as `1.0.0`; make it follow the source version or update both deliberately |
| `source/render.py`, overview and catalog labels | Derive icon, alternate, and category counts instead of preserving the initial `104` / `40` / `8` labels |
| `source/render.py`, `solid_sheet()` | Calculate canvas height from the number of rows; the existing 1430-pixel canvas fits 40 alternates and will clip a ninth row |
| `source/render.py`, overview featured list | Keep this intentionally curated; use the complete catalogs to verify every addition is present |
| `source/build.py`, Swift naming | Only the original `repeat` declaration is specially escaped; handle any additional reserved identifiers consistently |
| `source/build.py`, JSX conversion | New SVG attributes may require an explicit React attribute mapping; stay within the supported vocabulary where possible |
| Generated output directories | The exporter writes files but does not remove obsolete files from a deliberate rename or removal; reconcile those specific obsolete outputs |

For the current solid-sheet layout, a suitable dynamic height calculation is:

```python
rows = math.ceil(len(members) / 5)
height = 205 + rows * 146 + 57
```

Adjust the bottom margin if the layout changes. Additions do not require deleting existing assets. Treat any rename or removal as an explicit migration and preserve unrelated files.

The generator's XML checks are useful but do not prove geometric correctness, small-size legibility, collision-free native names, or valid application integration.

## 11. Export and integration rules

| Output | Convention |
|---|---|
| New base master | `svg/{id}.svg` |
| New solid master | `svg/solid/{id}.svg` |
| Original selected masters | Keep the four existing `svg/selected/` files |
| Native base name | `Lumina` plus the existing PascalCase naming convention |
| New native solid name | Base native name plus `Solid` |
| Original selected native names | Keep the `Selected` suffix for the original four |
| Sprite base symbol | `lumina-{id}` |
| Sprite alternate | `lumina-{id}-solid` |
| Original sprite aliases | Preserve the four `lumina-{id}-selected` aliases |

Native copies use explicit black for template rendering and ordinary Image Sets. They are not custom SF Symbol documents and do not carry variable-weight or symbol-animation metadata. Keep the existing asset-import and project-generation workflow.

Merge or replace overlapping image sets deliberately. Do not compile two catalogs containing the same original asset names.

For web use, inline SVG inherits `currentColor`. An external SVG loaded as an `<img>` does not inherit the surrounding document's `color` in the same way; choose inline SVG, an appropriate CSS-mask integration, or an explicit-color export. Keep raw SVG masters transparent and single-color.

Maintain accessible action names on host controls. Icon dimensions do not define touch targets, focus regions, or layout. Preserve the application's existing accessibility, keyboard, pointer, and tvOS behaviors where applicable.

## 12. Acceptance checks

The new artwork is ready when all relevant checks below pass. Correct actual defects before adding more visual detail.

### Geometry and meaning

- [ ] The new icon expresses one clear action or object and is distinct from nearby meanings.
- [ ] The frame, structural weight, caps, joins, and foreground behavior match the family.
- [ ] The icon feels appropriately sized beside the nearest approved references.
- [ ] No unintended intersections, bumps, tangencies, crowded badges, or closed counters remain.
- [ ] Numerals and directional conventions match the action.
- [ ] Outline and solid share a stable visual anchor and recognizable silhouette.
- [ ] Solid counters and off-state gaps are genuinely transparent.

### Render inspection

- [ ] Render actual exported SVG files at 20, 24, and 28 px and at an enlarged size.
- [ ] Inspect both styles at their intended display sizes; the original size-check sheet mainly presents outlines.
- [ ] Compare the new icons with approved neighbors on light and dark surfaces.
- [ ] Inspect transparency over a contrasting surface or checkerboard.
- [ ] Verify nonempty alpha bounds inside the viewport, with no accidental clipping.
- [ ] Inspect the actual pixels; XML validity and large previews alone are insufficient.

### Package and application

- [ ] Canonical source, masters, native assets, React geometry, sprite, and catalog agree.
- [ ] IDs, generated symbols, native names, and Swift cases are unique and valid.
- [ ] Catalog categories, counts, version labels, and preview dimensions reflect the new release.
- [ ] The original eight navigation masters remain unchanged unless explicitly revised by the user.
- [ ] New exports contain no embedded bitmap, font dependency, remote asset, or unwanted metadata.
- [ ] Native asset import and web type/build checks are run when those projects are available.
- [ ] The handoff clearly distinguishes completed checks from checks left to the integrating agent.

The supplied alpha-bound audit catches empty artwork and artwork touching the viewport edge. It cannot detect an ambiguous symbol, an almost-closed counter, or visual imbalance. Those require inspection.

## 13. Deliverable contract for the next agent

For each requested addition, deliver:

1. The actual outline SVG and useful solid alternate, with stable names.
2. The canonical geometry entry and synchronized platform exports when working in the pack.
3. A preview rendered from the final SVG, showing neighbors and actual-size samples in light and dark appearances.
4. A short change record: icon IDs, meanings, reference choices, variant decisions, and checks completed.
5. Any remaining integration limitation, stated precisely.

If image generation was used, identify its output as concept imagery and retain the useful prompt in the change record. The final production preview must come from the SVG exports. If an SVG capability is unavailable, report that limitation rather than presenting the raster concept as a completed vector asset.

### Copy-and-paste assignment

```text
Extend Lumina's existing icon family with: [ICON LIST AND MEANINGS].

Use LUMINA_ICON_STYLE_GUIDE.md as the style and production contract.
Use LUMINA_MEDIA_SVG_SET.zip / its extracted lumina-media-icons folder as
the reference implementation when supplied. Inspect the actual existing
icons and their geometry before creating additions.

Maintain the 24×24 frame, 1.75 rounded structural stroke, transparent
single-color geometry, optical balance, and simple recognizable forms.
Preserve the original four navigation pairs and existing identifiers.

Use image generation only if a new visual metaphor needs exploration or
concept imagery is requested. Final assets must be editable SVG paths,
not bitmap embeds or unreviewed traces. Keep exact geometry work in SVG.

Create useful outline/solid pairs; do not force solid styles on every icon.
Update the canonical source and synchronized exports, including any count,
category, naming, version, or preview-size adjustments needed by the current
generator. Render and visually inspect the actual files at 20/24/28 px and
enlarged, beside approved references, on light and dark backgrounds.

Deliver downloadable assets, actual rendered previews, and a concise
verification record. Continue through routine design and implementation
choices without a questionnaire. Ask only if an unresolved meaning or
missing required input materially blocks the requested result.
```

## 14. Provenance and reference priority

This guide was derived from the delivered Lumina media pack's `README.md`, `source/icons.json`, `source/build.py`, `source/render.py`, SVG masters, and rendered previews. Its numerical construction defaults supplement the fixed contract; they are guidance for new artwork rather than claims that every approved path uses identical margins or radii.

Use this priority when extending the family:

1. The user's current explicit request.
2. The approved Lumina artwork and its actual source geometry.
3. This guide's fixed specifications and production rules.
4. This guide's suggested construction defaults and optional image-generation prompts.

The broader project brand reference is [Lumina's brand identity contract](https://github.com/shiftedx/lumina/blob/main/apple/docs/brand-assets.md). If a future project change conflicts with this snapshot, identify the conflict and follow the current authorized project direction rather than silently rewriting established assets.
