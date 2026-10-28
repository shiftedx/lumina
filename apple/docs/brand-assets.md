# Lumina brand identity contract

This is the shared identity contract for Lumina's web, iOS, and tvOS surfaces. It establishes one warm brand/accent color, separate from adaptive neutral controls.

## Canonical rendered identity

- **Sunrise foreground and warm accent:** rendered sRGB `#DBB688` in every appearance mode. It is the approved Lumina mark and the only warm decorative, selection-line, progress, and state accent.
- **Charcoal icon ground:** `#151517` for the charcoal-backed application/icon treatment.
- **Approved rendered sources:** [the public media mark](../../frontend/public/media-mark.svg), [the favicon](../../frontend/public/favicon.svg), and native [`LuminaTheme.brand`](../App/Shared/LuminaTheme.swift). They carry the same rendered sunrise stroke.
- **Mark geometry:** the flat mark has a radius-11 sun and five detached rays. Each ray's inner centerline starts at radius 19; the 3-unit strokes retain about 5 units of visible clearance, including the low side rays.
- **Wordmark typography:** native wordmarks intentionally use the platform's available typography. They preserve the mark's hierarchy and spacing, but are not a claim of pixel-identical type across web, iOS, and tvOS.

The native generator's `#D2A875` sunrise and `#111112` charcoal literals are **generator inputs**, not replacement canonical values. Its explicitly sRGB bitmap context transforms them into the approved rendered `#DBB688`/`#151517` output. Do not “correct” those literals or regenerate icons merely to make the source text match the rendered values; compare generated pixels with the approved assets first.

## Mark and wordmark alignment

In horizontal lockups, align the mark's **painted bottom edge** with the bottom
of the capital **L**, using the text baseline. Do not center the mark against the
text's line box or align the image canvas bottom. The approved 56×34 SVG retains
1.5 units of transparent canvas below its horizon stroke; the shared web bar
lockup and native `luminaBrandMarkBaseline()` account for that padding. Reuse
these rules for login and navigation headers. The offline header's italic L has a small font-relative ink overshoot, retained
in its optical correction. Email uses the separate PNG's padding and table-cell
baselines. Standalone marks and vertically stacked lockups keep their existing
alignment. Asset geometry, colors, fonts,
and touch targets stay unchanged.

## Color-role contract

| Role | Canonical use | Values / boundary |
| --- | --- | --- |
| Brand and warm accent | The mark, decorative accents, selection lines, progress, and meaningful state | Rendered sRGB `#DBB688` in all modes. |
| Icon charcoal | Charcoal-backed icon and mark artwork | `#151517`. |
| Primary control | Ordinary primary actions, including email | Adaptive neutral fill: dark `#EFE9DD`, light `#15130F`; inverse text: dark `#0F0E0C`, light `#F4F1EA`; high contrast uses black/white inverses. |
| Secondary control | Ordinary secondary actions | Clear at rest; neutral pressed/selected wash. Keep existing focus, pressed, disabled, destructive, and hit-target behavior. |
| Enabled switch | Native switch track | Use the brand accent on the switch itself; keep its system thumb and native state behavior. Ordinary surrounding controls retain neutral tint. |
| Accessible accent text | Text that needs an accent relationship | Dark appearance uses `#DBB688`; light and high-contrast appearances use neutral readable ink, never a brown legacy accent. |

Native `LuminaTheme.gold` and `LuminaTheme.spotlight` are removed; no native alias describes an alternate palette. Web UI `--g-gold` and website `--gold` names are compatibility aliases to `var(--g-brand)`; text aliases follow the readable accent-text role above. The warm accent must not become a blanket `.tint`, primary fill, or local hex override. Preserve semantic red/destructive treatment and existing focus, pressed, disabled, and target-size behavior.

Selection underlines and decorative state lines use the brand role directly, including in light appearance. Do not route these lines through the adaptive accent-text role. Keep a non-color selected cue such as label weight or the native switch position, and retain accessible neutral focus outlines.

## Source of truth and change procedure

The shared native roles live in [`LuminaTheme.swift`](../App/Shared/LuminaTheme.swift) and [`MediaPresentation.swift`](../App/Shared/MediaPresentation.swift); web tokens live in [`frontend/src/styles/tokens.css`](../../frontend/src/styles/tokens.css), with component rules in [`frontend/src/ui/ui.css`](../../frontend/src/ui/ui.css). Email roles live in [`email_layout.py`](../../backend/app/services/email_layout.py). Native asset output is produced by [`generate_brand_assets.swift`](../scripts/generate_brand_assets.swift); social cards use [`render-social-cards.mjs`](../../frontend/scripts/render-social-cards.mjs).

When a role changes:

1. Change the canonical role and its shared tokens/styles, rather than adding local hex overrides or a blanket warm tint.
2. Inspect logo output against the approved SVG/icon assets in the intended ICC/sRGB context before changing generator inputs. Regenerate affected social previews and website product captures through their documented commands; update cache revisions/content-hashed URLs and capture provenance.
3. Check dark, light, and high-contrast appearances, then tvOS focus, pressed, disabled, and destructive states without changing hit targets.
4. Exercise a meaningful existing UI workflow. Run `mise exec -- make check` for the application repository; use the standalone website README’s build, capture, and check commands for that repository. Keep coverage at the workflow or shared brand-contract boundary rather than adding a test per color declaration.

## Shared interface icon family

The approved interface SVG family lives in [`assets/icons/`](../../assets/icons/),
with its [style guide](../../assets/icons/STYLE_GUIDE.md) and editable
`source/icons.json`. It is separate from the sunrise brand mark and launcher art.
Use these masters for matching app actions instead of drawing platform variants.

Run `python3 scripts/lumina_icons.py sync` from the repository root after editing
the canonical source; run `python3 scripts/lumina_icons.py check` to detect drift.
`check --render` also verifies every native SVG at 20, 24, 28 and 96 pixels on macOS.
Both iOS and tvOS share the template SVG asset catalog and `LuminaIcon` helper.
The browser uses the generated React helper through `frontend/src/ui/icons.tsx`;
that adapter centralizes the remaining specialized glyph exceptions.

Preserve the provided geometry, names, rounded 1.75-point stroke and inherited
foreground color. Use approved solid variants where appropriate; selected
navigation still uses the existing semantic selection treatment. Native image
sets need explicit sizes and are not SF Symbols. OS-owned controls retain their
native appearance and behavior. Missing semantics must remain explicit rather
than being replaced with unrelated artwork. See the [adoption audit](../../docs/icon-adoption.md).

## Historical qualification and rollout record

The entries below record the state of earlier work; they are not a statement that today's release gate is green.

The owner approved this direction and its rollout on 2026-10-19: a flat warm sunrise on neutral charcoal, with all five rays detached from the sun. The material is intentionally flat. The iOS asset reproduces the approved preview pixel for pixel.

Run `mise exec -- swift scripts/generate_brand_assets.swift` from `apple/` to reproduce the iOS 1024px icon, both layered tvOS icons, TV top shelf art and email marks. The native bitmap context uses sRGB, matching the reviewed master. The standalone website repository carries the same favicon and header/footer geometry.

The asset catalogs are enrolled through `project.yml`. They use the existing App Store Connect identity `ai.cozylabs.lumina` on iOS and tvOS. `scripts/testflight.sh` archives, exports and uploads inspected binaries; distribution artifacts remain ignored. The private library and optional proof fixtures are not icon source material.

The existing BrandMark tests retain the visible name, hidden decorative art and compact accessible-name contracts. Assertions tied to the old span/i implementation were removed when the detached geometry moved to SVG.

Qualification on 2026-10-19: generic iOS and tvOS Simulator builds passed from this branch. The approved 1024px preview and iOS icon have identical pixels; every native foreground size has six painted components (five detached rays plus the sun/horizon). The 17 existing branding/header tests passed. Release archives for iOS and tvOS build 0.1.0 (3), including the owner's current native UI work, passed the archive validator; their ignored distribution directory records source provenance. Distribution signing/export is waiting for the owner's Xcode account sign-in.

The required `mise exec -- make check` ran through typecheck and frontend build, then stopped at the backend suite: 5,210 passed, 11 skipped and 8 failed. Isolating those eight tests produced the same 3 failures and 5 passes on both this branch and unchanged main at `6c66068965d985992bb1719edda6572e3a3b7dbe`; no backend source changed. A separate full frontend test run had 2,619 passes and 3 failures, all three reproduced on unchanged main. The overall gate was not green, and its later browser suites did not run. No unrelated tests or behavior were changed to hide those failures.

Web rollout (2026-10-20): web wordmarks and sharing previews use the transparent `frontend/public/media-mark.svg` foreground, with the approved detached sunrise and rendered sRGB `#DBB688`. The charcoal-backed icon remains for app and home-screen use. The native generator produces the 512px app icon, 180px Apple touch icon and transparent 32/48px ICO fallback. Both normal and offline shells link versioned browser assets; the offline worker includes the transparent mark in its explicit shell allowlist. Social cards are regenerated with `mise exec -- npm --prefix frontend run social:render`; the email-art renderer leaves the approved native-generated transparent email mark intact. The standalone website uses content-hashed asset and screenshot URLs. Its Home and library screenshots are freshly captured from the current built app with an isolated open-movie demo, including desktop and phone layouts.
