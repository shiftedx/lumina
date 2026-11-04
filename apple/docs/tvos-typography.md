# Lumina tvOS typography

The tvOS interface uses **SF Pro for UI text** and **Newsreader Italic for page and media hero titles**. Official title artwork is still preferred when present; its accessible name remains text. Use the named roles in `TVTypography` rather than choosing a new point size in a screen.

| Role | Default | Typeface and weight | Use |
| --- | ---: | --- | --- |
| Hero title | 52 pt | Newsreader Italic | Featured Home and Requests titles; movie and series billboards |
| Page title | 48 pt | Newsreader Italic | Requests, Settings, and music detail or Now Playing headings |
| Section heading | 31 pt | SF Pro semibold | Shelf headings, results headings, settings sections, and empty-state titles |
| Body and fields | 29 pt | SF Pro regular | Synopsis, guidance, form input, settings labels, and secondary actions |
| Primary action | 29 pt | SF Pro semibold | Play, Resume, View title, confirmation, and other primary controls |
| Navigation | 25 pt | SF Pro medium; semibold when selected | Categories, scope tabs, Discover, Search, and My Requests |
| Card and row title | 25 pt | SF Pro medium | Poster and episode titles, music tracks, and other repeated content |
| Metadata | 23 pt | SF Pro regular | Year, runtime, episode context, counts, format, values, and timestamps |
| Status | 23 pt | SF Pro semibold | Availability, request state, and short contextual labels |

These are default logical points, not screenshot pixels. SF Pro roles use native text styles, and Newsreader uses relative Dynamic Type styles. Use 29 pt for reading text and reserve 23 pt for short supporting text. Apple’s [typography guidance](https://developer.apple.com/design/human-interface-guidelines/typography) lists 29 pt as the tvOS default and 23 pt as the recommended minimum. Pairing codes use the system monospaced face. System keyboard, alerts, pickers, and AVKit transport retain their platform styles.

- Keep labels readable at rest. Use focus outlines and selection indicators for navigation state; color alone must not carry status.
- Give text over artwork a dark backing or gradient. Check both bright and dark artwork.
- Allow up to two lines for card titles at the default size. Reserve room for captions and progress so a focus ring never touches the text. Let rows grow at larger text sizes where possible.
- Keep normal remote targets at least 64 × 64 pt where Lumina owns the button style. Text size and focus area are separate.
- Before changing the scale, review Home, Library, Requests, an episode rail, and Settings at the same simulator resolution. Check long titles, adjacent focus targets, and larger text, then confirm couch-distance legibility on Apple TV hardware.
