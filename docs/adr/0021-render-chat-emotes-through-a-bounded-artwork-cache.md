# ADR 0021: Render chat emotes through a bounded artwork cache

- Status: Accepted
- Date: 2026-10-12

## Context

YouTube chat supplies native Unicode emoji and custom image emotes; Twitch IRC supplies message-relative custom emote spans. Flattening all of these to shortcut text loses their presentation. Live and replay chat share one normalized event model and one frontend row. Passing provider image URLs directly to that row would bypass the guarded public networking boundary. Registering every chat image in the poster cache can evict artwork IDs while serializing a large chat response.

## Decision

Keep Unicode emoji as text, preferring native emoji IDs over shortcut aliases. Custom emotes retain bounded fallback text and Unicode-codepoint spans. The normalizer accepts at most 32 emotes per event, 128 characters per label and 2048 characters per HTTPS image URL. Moderation clears emotes along with text.

Stored event JSON may retain private provider image URLs. Public conversion is separate from storage conversion and maps these sources only to authenticated, opaque Lumina image paths. Legacy events without emote metadata continue to render as text; a replay refresh is needed to recover emotes previously flattened during normalization. The existing JSON column needs no database migration.

A dedicated emote ArtworkService instance reuses public DNS/redirect validation, image content checks, coalesced fetches and private browser caching. Its cache is in RAM only: 128 KiB per image, 16 MiB total, 256 cached images, 4096 registered sources and two concurrent fetches. It has separate request budgets and cannot evict poster artwork. One response-scoped resolver deduplicates sources and validates each origin once during registration. It registers at most 64 distinct source attempts, or the registry capacity if smaller, so one response cannot evict its own earlier image IDs. Excess or unavailable images retain their text labels.

The shared live/replay row validates local artwork paths and bounded, nonoverlapping codepoint spans, renders small lazy-loaded inline images, and restores original text when an image fails. Twitch uses static emote renditions. Search continues to use complete fallback text; moderated content never renders images.

## Consequences

- Standard emoji add no image requests. Repeated custom emotes share cached image bytes and browser URLs.
- Provider URLs, cookies and continuation tokens never enter public chat responses.
- Image failures, cache expiry and response budgets preserve readable text and do not interrupt playback.
- Durable emote metadata survives replay serialization and live-recording resume; opaque IDs are regenerated at serving time after restarts.
- Third-party emote extensions and animated provider renditions are outside this change.

Provider references: [Twitch IRC emote ranges](https://dev.twitch.tv/docs/chat/irc/) and [Twitch emote image format](https://dev.twitch.tv/docs/api/reference#get-channel-emotes).
