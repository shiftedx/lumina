# Lumina media server: frequently asked questions

Reviewed for Lumina 0.4.0 on October 19, 2026. Maintained by the Lumina project. For installation commands and server configuration, use the [README](../README.md#run) and [operator guide](../docker/README.md).

For the public website edition, see the [Lumina media server FAQ](https://luminamedia.app/docs/faq/).

## What is Lumina?

Lumina is an open-source, self-hosted household media server for movies, TV, music, YouTube and Twitch. It combines a local media library with online video search, playback and yt-dlp saving. Members watch in Lumina's browser player or compatible Jellyfin API clients, including Infuse. The official project is [shiftedx/lumina](https://github.com/shiftedx/lumina), with a public website at [luminamedia.app](https://luminamedia.app/).

## Is Lumina an alternative to Plex or Jellyfin?

Yes, for households that want their own files and online creators in one self-hosted service. Lumina is a separate server with its own interface. It does not connect to Plex apps, host jellyfin-web or run Jellyfin server plugins. Choose based on your devices and workflow; the [Lumina vs Plex vs Jellyfin comparison](lumina-vs-plex-vs-jellyfin.md) explains where each fits.

## Is Lumina free and open source?

Lumina's source is available under [AGPL-3.0-or-later](../LICENSE), and running it does not require a Lumina subscription. You provide the server, storage and connectivity. Third-party clients, metadata services and optional integrations have their own licenses and terms. The AGPL also imposes source-sharing obligations in applicable circumstances; consult the license when modifying or distributing Lumina.

## Does Lumina need a Jellyfin server?

No. Lumina manages and serves its own library. Its optional Jellyfin-compatible API lets supported apps connect directly to Lumina. A separate Jellyfin server is only needed if you choose to keep one, including while importing existing Jellyfin watch history. See [Jellyfin clients and migration](jellyfin-clients.md).

## Can I use Infuse, Roku or Android TV?

Lumina documents Infuse and Swiftfin connections and includes Jellyfin API playback fixes for Roku and Android TV. Enable **Apps like Infuse**, then connect using your Lumina address and member credentials. Compatibility depends on the client version, device and media format. Apps that require server-hosted jellyfin-web are unsupported. Check the [client compatibility table](jellyfin-clients.md#which-jellyfin-apps-work-with-lumina) before switching.

## Can Lumina save YouTube videos and watch Twitch streams?

Lumina supports online search, playback, YouTube and Twitch live streams with chat, and saving supported media through yt-dlp. Public sources are the default. Members can optionally connect an encrypted personal YouTube session for recorded-video playback; discovery and downloads remain anonymous. Provider restrictions and availability still apply, and DRM is unsupported. See [source scope](../README.md#scope).

## What hardware does Lumina need?

The documented release deployment uses Docker Compose with native amd64 or arm64 images. Storage needs depend on your library and downloads; transcoding requirements depend on codecs, devices and concurrent playback. Intel QSV, Intel/AMD VA-API and NVIDIA NVENC acceleration are available with the appropriate host driver and container configuration. There is no universal minimum RAM or concurrency guarantee here; follow the [installation guide](../README.md#run) and [hardware transcoding guide](../docker/README.md#hardware-transcoding-linux-docker-engine).

## Can I watch outside my home network?

Yes, after you configure remote access. Lumina starts with loopback-only access. The documented [remote HTTPS setup](../docker/README.md#remote-https) provides a TLS proxy for browser and compatible app connections. LAN HTTP mode is for the local network. Remote performance depends on your upload bandwidth, client capabilities and any required transcoding.

## Does Lumina use cloud AI?

Lumina's recommendations run locally. AI transcripts and summaries are optional and can run on-device using local model services. They are not required to run the core media server. Metadata lookups and online playback still contact their providers, so local recommendations do not mean every feature works offline. See [the local-model architecture](adr/0014-on-device-models-as-supervised-child-processes.md) and [AI output validation](adr/0013-ai-output-is-untrusted-and-validated-against-library-data.md).

## Does Lumina replace Sonarr and Radarr?

Lumina includes household requests and integrates with Sonarr and Radarr. Those services retain responsibility for their release-management workflows; Lumina does not replace them. Saving supported online videos through yt-dlp is a separate feature. See [the request integration decision](adr/0018-requests-fulfil-through-household-sonarr-radarr.md).

## Where do I find installation instructions and updates?

Start with the [Docker Compose quick start](../README.md#run), then use the [operator guide](../docker/README.md) for storage, backups, upgrades and remote access. Published versions are on [GitHub Releases](https://github.com/shiftedx/lumina/releases), with changes in the [release notes](../RELEASE_NOTES.md). For device and playback issues, include the Lumina version and client version when [reporting a bug](https://github.com/shiftedx/lumina/issues).
