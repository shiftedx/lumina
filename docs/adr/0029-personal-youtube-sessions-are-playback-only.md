# ADR 0029: Personal YouTube sessions are playback only

- Status: Accepted
- Date: 2026-10-19

## Context

Some individual YouTube videos require a signed-in browser session. A Google Data API token does not authorize yt-dlp playback. Sharing a server owner's cookies would grant one member's access to other household members. Existing anonymous extraction results are coalesced across members, and acquisition deliberately accepts only public media.

## Decision

A member may explicitly import a Netscape-format YouTube browser cookie file under personal settings. Keep only bounded, unexpired YouTube-domain cookies and require a recognizable sign-in cookie. Seal the normalized cookie data using AES-GCM through the existing secret service, with a YouTube-specific member binding. The existing `totp-key` remains the recovery key; a missing key requires reconnection rather than silently minting another on read. Neither settings responses nor profile exports return the credentials.

Anonymous preview remains the first attempt. Only a restricted individual HTTPS YouTube video may retry with the requesting member's saved session. Inject cookies into that extraction's in-memory cookie jar, never extractor options, environment variables, command lines or plaintext temporary files. Keep the guarded networking and public destination checks. Strip authentication headers and cookies from extracted transport metadata. Authenticated preview results bypass shared extraction caches; DASH indexes are member-scoped, and authenticated media bypasses the persistent range cache. Packaging remains bounded and disposable.

This exception covers recorded video playback. It does not authorize acquisition, shared follows, search/discovery, replay chat, or authenticated live relay playback. An authenticated preview disables saving. A refused extraction keeps a source handoff and a personal connection link; a connected session does not imply permission to every video. Do not mark a whole account expired merely because one video was refused.

Every private stream retains its connection generation. Replacing or disconnecting a connection retires that member's private streams; requests also validate the generation so an extraction that completes after disconnect cannot revive access. Already delivered bytes cannot be recalled. Disconnect removes Lumina's credential copy and does not revoke the browser session at Google.

## Consequences

Schema 32 adds `youtube_sessions`; upgrades are additive. Rollback deletes its credentials and stamps schema 31, preserving other member data. Database backups contain encrypted sessions; keep the recovery key separately. Browser session exports are sensitive and authenticated yt-dlp use can result in provider account restrictions. The UI explains this before opt-in and never requests a Google password. Credential persistence does not prove that YouTube will accept playback; expiry, account entitlement, provider challenges and unsupported transports still require a graceful source handoff.
