# Approved flat sunrise

The owner approved this direction and its rollout on 2026-10-19: a flat soft-gold sunrise on neutral charcoal, with all five rays detached from the sun. The material is intentionally flat. The iOS asset reproduces the approved preview pixel for pixel.

The sun radius is 11 design units. Every ray's inner centerline starts at radius 19. The two 3-unit strokes leave approximately 5 units of visible clearance, including the low side rays. The same paths are used in the web brand, favicon, public media mark and native generator.

Run `mise exec -- swift scripts/generate_brand_assets.swift` from `apple/` to reproduce the iOS 1024px icon, both layered tvOS icons, TV top shelf art and email marks. The native bitmap context uses sRGB, matching the reviewed master. The standalone website repository carries the same favicon and header/footer geometry.

The asset catalogs are enrolled through `project.yml`. They use the existing App Store Connect identity `ai.cozylabs.lumina` on iOS and tvOS. `scripts/testflight.sh` archives, exports and uploads inspected binaries; distribution artifacts remain ignored. The private library and optional proof fixtures are not icon source material.

The existing BrandMark tests retain the visible name, hidden decorative art and compact accessible-name contracts. Assertions tied to the old span/i implementation were removed when the detached geometry moved to SVG.
