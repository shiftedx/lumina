# Security policy

## Reporting

Report vulnerabilities through GitHub [private vulnerability reporting](https://github.com/shiftedx/lumina/security/advisories/new), not public issues. Include the commit or image ID, deployment topology, impact and reproduction steps. Use synthetic accounts and media, and leave out passwords, tokens, cookies and personal data.

Only the latest release is supported. Response is best-effort: acknowledgement within 3 business days, an initial assessment within 7, updates every 14 while a confirmed issue is open. Disclosure normally follows a fixed release, or 90 days after confirmation. There is no bug bounty. Good-faith research on systems you own or are authorised to test is welcome.

## In scope

Issues reproducible in the latest release using:

- the default loopback Docker deployment, or the documented single TLS reverse proxy;
- LAN HTTP mode on a private LAN (plaintext on that LAN is accepted by design);
- public HTTP(S) media sources;
- the guarded playback and live-chat relays (YouTube and Twitch HLS, VODs, live chat): the browser must never receive an upstream media address, manifest, key, cookie, header, signed token, chat continuation or OAuth/EventSub material, and no member may reach another member's stream, chat session or recording;
- live recording: capture goes through the same guarded HLS transport, stores no upstream secrets, is member-scoped and bounded, and Twitch chat is captured forward-only;
- the shipped web, backend, Docker and recovery artifacts.

Out of scope: native (non-Docker) runs (best-effort only), extractor breakage without security impact, DRM, content-rights disputes, social engineering, compromised hosts or Docker admins, port 8765 published beyond loopback or a private LAN, arbitrary proxy chains, and hostile writers on the Library mount. Upstream dependency bugs are in scope when a supported configuration exposes them.

See the [operator guide](docker/README.md) for the supported topology.

## Member switching

"Who's watching?" lets a household switch members on one browser without retyping a password. These are the rules the feature promises; none of them involves a token, cookie value or password in this document.

**Opt-in, per device.** A member's session is remembered in the device ring only when that member ticks "Show me in Who's watching on this device" during their own password sign-in. It is off by default, and setup and invite flows never remember anyone. A browser that was never opted in shows no names and no artwork before sign-in.

**What the ring is.** An HttpOnly cookie named after the session cookie with a `_ring` suffix, limited to the `/api/session` path (so it is never sent to media, artwork or Jellyfin routes), mirroring the session cookie's `Secure` and `SameSite` settings, and holding at most six members with one entry each. Malformed, forged or oversized values are ignored and the cookie is rewritten canonically. Remembered sessions are the members' own ordinary sessions and obey absolute and idle expiry, password change, reset and deactivation like any other.

**Vault owners always need their password.** The ring never holds a usable owner session. An owner appears only as a signed marker (member id plus an HMAC under the app secret) that authenticates nothing: it lets the picker show the name and sends the owner to the password form. Switching away from an owner signs that owner session out, and any role change signs the member out everywhere. The marker is display-only and not secret; holding one reveals an owner's name on that picker and nothing more.

**Every switch rotates.** Switching to a remembered member mints a fresh session for that member carrying the old session's absolute expiry (never extended), revokes the old one and rewrites the ring. A ring segment copied passively therefore stops working once the member is switched to. Listing the ring touches neither activity time nor expiry.

**Residual risk, accepted.** Someone with access to an opted-in browser can act as its remembered non-owner members; that is inherent to switching without a PIN, and is why it is opt-in. Someone who copies the ring cookie and switches to a member before the legitimate browser does keeps that member's session until its original absolute expiry (at most the configured session duration) and the member drops off the original picker; the member's password change ends it. A party that can write cookies for this host (a sibling subdomain, or any on-path party in LAN HTTP mode) could plant a ring holding the writer's own session, so a victim who picks that tile acts as that writer; the tile shows the writer's name. Use "Forget on this device" and sign out on shared machines.

**Cross-site and brute force.** Switch, forget, login and logout need a trusted Origin or Referer when only the ring cookie is present, and the cookie is `SameSite`; CORS does not let a foreign page read the listing. A switch charges the sign-in rate limit only when it fails, so ordinary household switching never blocks password sign-in, and an attacker with ring access gains only sessions that already sit on that browser. Listing is rate limited separately.

**Client hand-off.** Before the cookie changes, the app sends the leaving member's buffered recommendation events, finishes in-flight playback saves and closes the live event stream. The player is closed first so the leaving member's last position is saved under them; a switch that fails therefore leaves no mini player, and the title has to be reopened.

**Known limitations.** Other tabs of the same browser are not told about a member change: such a tab keeps the previous member's screen while the shared cookie names someone else. Its writes are refused (the CSRF token is bound to the session) but its reads show the new member's data under the old name, so reload other tabs after switching. On shared or kiosk devices, decline the browser's offer to save the vault owner's password (or turn password saving off for the browser), because the owner's password tile is an ordinary sign-in form that browsers offer to remember. When a switch or sign-in ends without an answer (network error, timeout or a 5xx), the app reloads the session instead of assuming nothing changed, so it follows whichever cookie the browser now holds.

**Unchanged.** Jellyfin and device tokens, the recommendation event routes and the database schema are not touched by member switching.

## Two-step verification and app passwords

Two-step verification is optional per member (RFC 6238 TOTP: SHA-1, 6 digits, 30-second steps, one step of drift). The shared secret is stored AES-GCM encrypted under a key derived from the app secret file; recovery codes and app passwords are stored only as SHA-256 digests of high-entropy random values. A correct password for such an account yields only a five-minute challenge bound to the same client address, and only a correct code or unused recovery code turns it into a session. Wrong codes count toward the same per-address block and public-address account lock as wrong passwords, a code is accepted at most once, and a challenge allows five attempts. The trusted-device cookie is an HMAC over the member, its expiry, the password hash and the sealed secret, so a password change or re-enrollment voids it.

Jellyfin apps and HTTP Basic cannot carry a second step: for an account with two-step verification they refuse the account password and accept a per-device app password instead. An app password therefore grants that member's full API access over HTTP Basic without a code; it is shown once, can only be created from a signed-in browser session, never works on the web sign-in form, and revoking it signs out the app that used it. **Accepted:** an app password, or a Jellyfin sign-in made before the member turned two-step verification on, keeps working until revoked ("Sign out all apps" under Connected apps); and the "password correct, code needed" answer confirms the password to whoever typed it, which the code then still protects.
