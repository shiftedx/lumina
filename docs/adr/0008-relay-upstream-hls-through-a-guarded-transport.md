# ADR 0008: Relay upstream HLS through a guarded transport

- Status: Accepted
- Date: 2026-07-19

## Context

Lumina's capability seam reports playback only for a progressive http(s) stream the browser can render directly. Segmented sources report `segmented_transport_not_supported`, so a Household member cannot watch a Twitch VOD in Watch even though the content is public and browser-playable through HLS. Handing the browser the upstream manifest would expose upstream addresses, signed query tokens, and any member-owned request headers, and would let manifest-referenced or redirected addresses reach private network space. The existing local VOD HLS packager is a different mechanism: it muxes bounded split tracks into Lumina-authored fMP4 on disk and does not relay an upstream manifest.

## Decision

Lumina adds a guarded upstream-HLS relay as the first supported segmented-playback path, using one Twitch VOD as the tracer case. Playback (`can_play`) becomes available only for a Twitch VOD whose HLS master the relay supports; generic HLS from other providers and live Twitch stay not-playable, and acquisition (`can_acquire`) is unchanged and still mirrors the worker's download-transport allowlist.

The relay parses the upstream master and each media playlist, resolves every referenced address (variant, rendition, segment, initialization resource, and encryption key) to an absolute URL, and rewrites it to an authenticated, owner-scoped Lumina address. It fetches each upstream resource on the member's behalf and revalidates the public-source policy at fetch time, so the browser receives only Lumina-owned addresses and never an upstream URL, host, cookie, request header, or signed token. Rewriting is a strict allowlist of known address-carrying tags; any playlist that carries an upstream address on a tag the relay does not rewrite (for example content steering, URI-form session data, a rendition report, or an HLS Interstitials asset list) is rejected rather than passed through. As a name-independent backstop, the fully rewritten manifest is scanned once more and rejected if any scheme-bearing (`scheme://`) or protocol-relative (`//host`) address survives, since every Lumina-owned address is a root-relative single-slash path; so no upstream address reaches the browser verbatim regardless of which attribute or tag carried it. The relay fails closed on private, loopback, link-local, mixed public/private, unsupported-scheme, malformed, oversized, and recursive manifests, and on redirects into private space. Expired signed resources are re-resolved server-side without changing the opaque public stream identity. The relay is a distinct seam from the local VOD HLS packager and never materializes upstream media to disk; it holds only a bounded, per-generation, in-memory map of opaque resource identifiers to upstream addresses. Sessions are owner-bound and every resource, response body, manifest size, per-generation resource count, session count, concurrent read, idle interval, and maximum lifetime is bounded, matching the controls on the existing remote-playback path.

The AES content key for an encrypted playlist is, by necessity of client-side decryption, relayed to the authenticated member's browser through a Lumina-owned address; the upstream key URL, cookies, and headers are not.

## Consequences

- A Household member can play a supported Twitch VOD in Watch through Lumina without any upstream address or credential reaching the browser.
- The public-source policy remains the single SSRF authority and is enforced on every relayed master, playlist, segment, initialization resource, key, and redirect at fetch time.
- The relay is scoped to the tracer case; broadening it to other providers or to live media is a deliberate, separately reviewed change.
- The relay itself is playback only. Acquisition of the same Twitch VOD tracer was subsequently added in issue #95 as a scoped exception: the download-transport gate admits yt-dlp's native (guarded) HLS downloader for exactly this tracer, keyed off the same predicate as playback, so every acquisition fetch flows through this transport. The generic `SAFE_NATIVE_PROTOCOLS` allowlist (and the #91 capability lockstep it governs) is unchanged, and acquisition of any other segmented source remains out of scope. Broadening the acquire path beyond the tracer is a deliberate, separately reviewed change.
- Client-side rendition selection is performed by the browser's HLS player from the rewritten master; the relay does not expose a server-side rendition ladder for this transport.
- Mid-playback expiry recovers through the existing Watch flow: a master-fetch expiry re-resolves and redirects transparently, while an expired segment surfaces as a fail-closed error that the browser's HLS player reports as a fatal load error, and the player then issues one session refresh to the re-resolved generation and resumes at the retained position.
