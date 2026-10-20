# Using Jellyfin clients with Lumina

Lumina can serve its library directly to compatible Jellyfin API clients, including Infuse and Swiftfin, without running Jellyfin Server. Enable the optional API in Lumina, add Lumina's server address as a Jellyfin connection in the client, and sign in with your Lumina member account. Lumina's browser player remains available at the same address.

Reviewed for Lumina 0.4.0 on October 19, 2026. Compatibility statements describe the documented API and client fixes, not certification of every device, app version or codec. The [operator guide](../docker/README.md#jellyfin-apps-infuse-swiftfin) is the authoritative setup reference; [ADR 0010](adr/0010-connected-apps-and-an-opt-in-jellyfin-compatible-api.md) records API behavior and limits.

For the public website edition, see [Jellyfin clients and migration](https://luminamedia.app/docs/jellyfin-clients/).

## Which Jellyfin apps work with Lumina?

| Client or client type | Lumina compatibility |
| --- | --- |
| Infuse on Apple TV, iPhone, iPad and Mac | Documented browsing, playback and watch-state workflow through a Jellyfin connection. |
| Swiftfin | Documented connection and member sign-in workflow. |
| Jellyfin for Roku and Android TV | API compatibility includes documented playback fixes for these clients. Actual playback still depends on device codecs and app version. |
| Other direct Jellyfin API clients | The API targets these workflows; verify your client's browsing, playback and resume behavior before switching. |
| Apps that require server-hosted jellyfin-web, including the official Jellyfin phone web wrappers and Jellyfin Media Player | Unsupported: Lumina does not serve jellyfin-web. Use Lumina's browser interface or a compatible API client. |
| Plex apps | Unsupported: Lumina does not implement the Plex client protocol. |

Jellyfin's [client directory](https://jellyfin.org/clients/) lists clients for Jellyfin Server; inclusion there does not establish Lumina compatibility. Lumina implements its own compatible API rather than the entire Jellyfin server. Jellyfin server plugins cannot be installed in Lumina.

## How do I connect Infuse or Swiftfin?

1. Configure [remote HTTPS](../docker/README.md#remote-https) or [LAN HTTP mode](../docker/README.md#lan-http-mode). Lumina is loopback-only by default.
2. In Lumina, open **Manage Server → Connections → Metadata → Apps like Infuse**, enable the API, and copy the server address shown there.
3. In Infuse, add a **Jellyfin** share. In Swiftfin, choose **Connect to Server**. Enter Lumina's address and your Lumina member username and password.
4. If your member account has two-step verification enabled, create an **app password** in **Settings → Connected apps → Add an app** and use that password in the client.
5. In Infuse, keep **Library Mode** enabled to browse Movies, Shows and Channels with artwork.

Use the server root, for example `https://media.example.com`. Do not append `/jellyfin` or `/emby`. For LAN HTTP mode, use the address and port shown by Lumina. Each household member should sign in as themselves so the client sees that member's permitted media and progress.

## Does watch progress sync between Lumina and Infuse?

The compatible API supports watch state and resume positions shared with Lumina's web interface for the signed-in member. Each client sign-in appears in **Settings → Connected apps** and can be revoked there. A client's local queue or device-specific settings are separate from server watch progress; this is not a promise of seamless playback handoff between every app.

## Can I move from Jellyfin to Lumina?

Lumina can import Jellyfin watched status, resume positions and favorites for matched items. You can preview a member's import or, as the vault owner, preview a household import that pairs existing usernames and can create household members.

First make the same media available to Lumina and let it scan. Configure **Manage Server → Connections → Metadata → Jellyfin server to import from** with an address reachable from inside Lumina's container. Use **Settings → Privacy & data → Watch history from Jellyfin** for one member or **Settings → Members → Bring members over from Jellyfin** for the household.

Matching uses file-path suffixes, then TMDB, IMDb and TVDB IDs. Different container mount prefixes are allowed. Preview the match results before importing; newer Lumina progress is preserved. The import does not migrate Jellyfin's complete database, plugins or server settings. Keep the source server until you have checked your library and clients. Follow the complete [migration procedure](../docker/README.md#moving-over-from-jellyfin) for credentials, new-member links and matching details.

## What if a client connects but cannot play?

Check the [operator guide's client notes](../docker/README.md#jellyfin-apps-infuse-swiftfin) and your [hardware transcoding configuration](../docker/README.md#hardware-transcoding-intel-qsvvaapi). Direct play, remux and transcoding depend on the source file, available hardware and the client's reported capabilities. After an upgrade, close and reopen the client to refresh item details. If you report a problem, include app version, device, source codec and a sanitized description of the failure; never include passwords or token-bearing playback URLs.

See also [Lumina vs Plex vs Jellyfin](lumina-vs-plex-vs-jellyfin.md), the [FAQ](faq.md), and [release notes](../RELEASE_NOTES.md).
