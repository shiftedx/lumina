# Lumina vs Plex vs Jellyfin: choosing a self-hosted media server

Lumina is an open-source media server for households that want movies, TV and music together with YouTube videos, Twitch streams and saved creator content. Consider Lumina as a Plex or Jellyfin alternative when that mix matters to you. Choose by the apps and workflows your household actually uses: Lumina has its own browser interface and an opt-in Jellyfin-compatible API, rather than Plex client support or complete Jellyfin server compatibility.

This comparison is maintained by the Lumina project. Reviewed for Lumina 0.4.0 on October 19, 2026. Lumina claims below link to its operator guide and implementation decisions; Plex and Jellyfin claims link to their own documentation. This is a workflow comparison, not an exhaustive feature inventory or an independent ranking. Client behavior can change with app and server versions.

For the public website edition, see [Lumina vs Plex vs Jellyfin](https://luminamedia.app/docs/lumina-vs-plex-vs-jellyfin/).

## Which server fits my household?

| Your priority | Start by evaluating | Why |
| --- | --- | --- |
| Local media alongside YouTube, Twitch and creator downloads | Lumina | Search, watch and save online media alongside your household library in one interface. |
| Playing your library through Plex apps on your existing devices | Plex | Plex supplies its own server and client ecosystem. Lumina does not serve Plex clients. |
| The full Jellyfin server, jellyfin-web and its plugin ecosystem | Jellyfin | Lumina implements a compatible API for selected client workflows; it does not host jellyfin-web or run Jellyfin server plugins. |
| Watching Lumina through Infuse or other Jellyfin API clients | Lumina | Enable the compatible API, then connect the app directly to Lumina. Check the [compatibility guide](jellyfin-clients.md) first. |
| Trying a different server while retaining Jellyfin watch progress | Lumina | Preview and import watched status, resume positions and favorites for matched media. |

The best test is a small library on the actual devices your household uses. Keep your existing server available while evaluating client playback, subtitles, resume behavior and remote access.

## How do Lumina, Plex and Jellyfin compare?

| Area | Lumina | Plex | Jellyfin |
| --- | --- | --- | --- |
| Personal media | Movies, TV and music with metadata and household access controls | Personal libraries managed by Plex Media Server | Personal libraries managed by Jellyfin Server |
| Client model | Lumina browser player plus compatible Jellyfin API clients | Plex apps and web player | Jellyfin web player and official/community clients |
| YouTube and Twitch in Lumina's workflow | Integrated search, playback, live streams and yt-dlp saving | Not assessed here; see Plex's current product documentation | Not assessed here; third-party integrations are outside this comparison |
| Software licensing | AGPL-3.0-or-later | Proprietary product with free and paid offerings | Free software; server licensed GPL-2.0 |
| Server installation | Docker Compose; native amd64 and arm64 release images | Multiple desktop, Linux and NAS platforms | Multiple native platforms and container deployments |
| Hardware transcoding | Intel QSV/VAAPI, subject to host/device configuration | Check Plex's current hardware and plan requirements | Check Jellyfin's current GPU and platform requirements |
| Moving Jellyfin watch state into Lumina | Preview/import matched watched status, resume positions and favorites | Plex migration behavior not assessed here | Source server for Lumina's import |

Sources: [Lumina setup and features](../README.md), [Lumina hardware transcoding](../docker/README.md#hardware-transcoding-intel-qsvvaapi), [Plex overview](https://support.plex.tv/articles/200288286-what-is-plex/), [Plex Media Server](https://www.plex.tv/your-media/), [Jellyfin overview](https://jellyfin.org/docs/), [Jellyfin installation](https://jellyfin.org/docs/general/installation/), [Jellyfin server source and license](https://github.com/jellyfin/jellyfin), and [Jellyfin hardware acceleration](https://jellyfin.org/docs/general/administration/hardware-acceleration/).

No price or performance ranking is implied by this table. Consult each project's current documentation for paid features, hardware requirements and device support.

## When is Lumina a useful Plex alternative?

Lumina is a useful Plex alternative to evaluate if you want a self-hosted browser experience that brings your local library and online creators together. You can search YouTube, follow channels, watch YouTube and Twitch live streams with chat, and save supported media through yt-dlp. Saved videos sit alongside movies, shows and music rather than requiring a separate viewing workflow.

Lumina also includes household members and invitations, per-member limits, local recommendations, and requests through Sonarr and Radarr. Its server is open source under [AGPL-3.0-or-later](../LICENSE), with no Lumina subscription required. You supply the server, storage and network access; third-party apps and services have their own terms.

Plex remains the relevant choice to evaluate when your household depends on Plex apps, Plex-specific features or Plex's own content offerings. A Plex app cannot sign in to Lumina, and Lumina does not document a Plex watch-history importer. Adding the same media folders to another server is separate from migrating accounts, metadata edits or playback history.

## When is Lumina a useful Jellyfin alternative?

Consider Lumina if you want its combined local-library and online-video workflow while continuing to use a compatible Jellyfin API client such as Infuse. Lumina is a separate server. You do not install Lumina as a Jellyfin plugin, and you do not need to keep Jellyfin running to serve Lumina's library.

Jellyfin is the more direct fit when you require its full server behavior, server plugins or server-hosted jellyfin-web. Lumina's compatible API is optional and disabled by default. Apps that expect the server to provide jellyfin-web cannot use Lumina as a drop-in replacement. Device codecs and client versions also affect playback. See [which Jellyfin apps work with Lumina](jellyfin-clients.md#which-jellyfin-apps-work-with-lumina).

For a household already using Jellyfin, Lumina can import watched status, resume positions and favorites after matching library files or provider IDs. Preview the import before applying it. This is a watch-state migration, not a full transfer of Jellyfin's database, plugins or settings. See [moving from Jellyfin](jellyfin-clients.md#can-i-move-from-jellyfin-to-lumina).

## Can Lumina replace Sonarr, Radarr or subscription streaming services?

Lumina integrates household requests with Sonarr and Radarr; those applications still manage releases and downloads in their own workflows. Lumina's yt-dlp saving handles a separate online-media workflow. It does not replace Netflix, Disney+ or other DRM-protected subscription services, and it does not include rights to movies or TV shows.

Public online sources are the default. Members can optionally connect an encrypted personal YouTube session for recorded-video playback. Discovery and downloads remain anonymous, and DRM is unsupported. Provider availability and terms still apply. See [source scope](../README.md#scope) and [request integration](adr/0018-requests-fulfil-through-household-sonarr-radarr.md).

## How do I try Lumina?

1. Follow the pinned Docker Compose commands in the [installation guide](../README.md#run).
2. Create your household admin and add a small read-only media folder using the [operator guide](../docker/README.md).
3. Try browser playback and your intended [Jellyfin clients](jellyfin-clients.md), then check online sources you use.
4. Configure [remote HTTPS](../docker/README.md#remote-https) before evaluating access outside the server itself.

### Evaluate playback on your actual devices

Start with a movie, an episode and a music album that represent your library. Include the audio and subtitle formats you actually use, rather than relying on a single browser-friendly demo. Try direct playback, switching subtitle tracks, seeking and resuming as a second household member. If a device requires conversion, confirm the host's hardware acceleration setup and try the number of simultaneous streams you expect at home.

Lumina provides direct play, remuxing and transcoding, but the path chosen depends on the file and the client's capabilities. A fast direct-play result on one device does not establish transcoding capacity on another. The same principle applies when comparing Plex and Jellyfin. Use the same media, device, network and playback mode before drawing a performance conclusion. This comparison does not claim that one server is universally faster.

### Keep media files and application state separate

Lumina supports read-only external media folder imports, so an evaluation can use existing files without granting Lumina permission to rewrite that library. Lumina's own database, settings and downloaded media still need persistent storage and backups. Follow the operator guide's volume configuration rather than pointing two applications at the same application-data directory.

Importing a folder makes the files available to the new server; it does not automatically reproduce another server's users, collections, plugin settings or metadata edits. Lumina documents a dedicated Jellyfin watch-state import, with preview and matching rules, for the history described above. Do not infer an equivalent Plex importer from the ability to scan files already used by Plex.

### Test online sources and remote access separately

Try the YouTube channels and Twitch streams you follow, including the distinction between watching a source now and saving supported content. Availability can change upstream, independently of a Lumina release. A source playing in your existing browser session does not prove that anonymous discovery or a server-side download can access it.

Finally, test remote playback on a connection outside your LAN after configuring HTTPS. Check sign-in, the public server address, resume behavior and the effect of limited upload bandwidth. Keep your previous server and a verified backup until the whole household's essential workflows work. A successful local browser session alone is not enough evidence to complete a migration.

Continue with the [FAQ](faq.md), [release notes](../RELEASE_NOTES.md) and [project source](https://github.com/shiftedx/lumina). Lumina is independent of, and is not endorsed by, Plex or Jellyfin.
