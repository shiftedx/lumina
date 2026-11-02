# Social preview sources

The current link and invite cards use the same image-led direction as the Lumina website: Newsreader headings, the approved sunrise mark, neutral charcoal/ivory, and actual native app screens. The renderer composes these into static 1200 × 630 JPEGs; it does not reconstruct the app interface or load any household server.

`ios-home-v1.jpg` and `tvos-home-v1.jpg` are exact copies of the website’s approved `assets/ios-home.jpg` and `assets/tvos-home.jpg` public-demo captures. Their original names, capture revision, method, privacy qualifications and SHA-256 values are recorded in `native-captures.json`. That record describes the source screenshots; the final share cards crop, scale and compose them into simple CSS screen frames.

The screenshots show Spring (2019), Blender Studio / Blender Foundation, Creative Commons Attribution. See the repository’s `CREDITS.md` and `docs/assets/blender-sources.json`. Demo film ratings and progress are synthetic. The source images contain no household media, real credentials or member details.

Run `mise exec -- npm --prefix frontend run social:render`. Current outputs are `lumina-share-v4.jpg` and `lumina-invite-v3.jpg`. Bump the output filenames and server metadata revision when replacing them so external crawlers receive new image URLs. Retain earlier versioned outputs for existing embeds.
