# ADR 0001: Use a loopback-first deployment boundary

- Status: Accepted
- Date: 2026-07-16

## Context

Lumina holds household accounts, sessions, and private media. The primary Docker run path also needs to remain simple for a single-host installation. Publishing the application port directly to a LAN or the internet would expand the authentication and transport boundary, while trusting arbitrary forwarding chains would make client-address and origin enforcement ambiguous.

## Decision

The base Docker deployment publishes Lumina only on `127.0.0.1:8765`.

Supported remote access uses exactly one TLS-terminating reverse proxy in front of exactly one Lumina process. Lumina trusts forwarding information only from that configured immediate proxy, accepts one canonical client address, requires matching HTTPS public origins, and uses Secure session cookies. First-run administrator setup must be completed over loopback before remote mode can start.

The exact configuration and verification procedure lives in the canonical [`docker/README.md`](../../docker/README.md) operator guide.

## Consequences

- A default installation is not reachable from other hosts.
- Remote operators must provide DNS, TLS termination, and the constrained proxy configuration as one coherent setup.
- Direct publication of port 8765, multiple proxy hops, and arbitrary forwarding chains are unsupported.
- Loopback remains available for health diagnostics, but it is not an authenticated HTTP fallback when Secure cookies are enabled.
- The constrained topology is less flexible than generic reverse-proxy support, but it keeps origin, cookie, and client-address trust explicit and testable.

## Amendment (2026-09-26): opt-in LAN HTTP mode

### Context

A household may want Lumina reachable on the home LAN, as a Jellyfin server usually is: plain `http://<lan-ip>:8765` in a browser and `http://<lan-ip>:8765/jellyfin` in Infuse or Swiftfin, without DNS, certificates or a reverse proxy.

### Decision

`LUMINA_LAN_HTTP=true` is an explicit opt-in that replaces the remote HTTPS contract for that process. Startup accepts it only when:

- `LUMINA_APP_PUBLIC_URL` is `http://` on an IPv4 literal in a private range (10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16: never a DNS name, IPv6, public, link-local or loopback address (IPv6 is excluded because the host-header check cannot match a bracketed IPv6 host);
- the URL is a canonical origin (no credentials, path, query or fragment);
- `LUMINA_SESSION_COOKIE_SECURE` is false and no trusted proxy is configured;
- the frontend URL equals the app URL, and the allowed origins are only that URL plus the host's own `http://127.0.0.1:<port>` / `http://localhost:<port>` aliases.

Trusted hosts and origins derive from the public URL as before. Session cookies stay HttpOnly and SameSite but are not Secure. The first administrator may be created from the LAN, because a LAN-mode process with an empty database starts normally. The HTTPS rules above are unchanged, and without the flag startup behaves exactly as before.

### Consequences

- Passwords, session cookies, Jellyfin access tokens and media travel in plaintext on the LAN. Anyone on that network (or on a compromised device there, or guest Wi-Fi bridged into it) can observe or replay them. Use it only on a network you trust; use Remote HTTPS for anything beyond it.
- Whoever reaches the setup screen first becomes the administrator. Create the first administrator immediately after the first LAN-mode start, or create it over loopback before enabling the mode.
- Port 8765 is published on the LAN address, so the host firewall and router must not forward it to the internet. Lumina refuses public IPs and DNS names as the public URL, but it cannot see how the port is routed.
- No forwarding headers are trusted; rate limiting keys on the direct peer address as Docker presents it (per LAN device on Linux, possibly one shared address on Docker Desktop).

## Amendment (2026-10-05): a local address beside the public address

LAN HTTP mode may also name one **local address**, a vault-owner setting stored next to the public address (`app_settings.local_address`, `PUT /api/admin/local-address`): an `http(s)://` origin whose host is a DNS name ending in `.local`, `.lan`, `.home.arpa` or `.internal`, never an IP, wildcard or the public address's host, reached through the trusted reverse proxy. Its host joins the trusted hosts and its origin the browser origins, live, like the public address's. Cookies follow `X-Forwarded-Proto` from the trusted proxy as before, and HSTS is never sent for an http local address. The suffix rule keeps the plaintext name inside the home network: a name that also resolves publicly would send passwords and cookies over http wherever it leads. Without LAN HTTP mode a saved local address is ignored with a warning, so the Remote HTTPS contract above is unchanged.

## Alternatives considered

- **Publish port 8765 on every interface by default.** Rejected because it exposes an unconfigured or newly bootstrapped vault beyond the local host.
- **Support arbitrary trusted proxy subnets and forwarding chains.** Rejected because the effective client and trust boundary become deployment-specific and easier to misconfigure.
- **Require HTTPS inside Lumina itself.** Rejected because a dedicated edge proxy handles certificate lifecycle and transport concerns without coupling them to the application process.
