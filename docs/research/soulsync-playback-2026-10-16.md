# SoulSync playback audit — 2026-10-16

## Scope and evidence

This is a source review of [SoulSync commit
9e35006055a5161cbe26f3b7af010ab5f5f2b093](https://github.com/Nezreka/SoulSync/tree/9e35006055a5161cbe26f3b7af010ab5f5f2b093),
not a deployed-product test. The README markets one household-media experience
with a web player, download sources, media servers, radio, podcasts,
audiobooks, video, and server delegation. [Its player and video
claims](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L195-L221)
are positioning, not proof that every behavior works at runtime.

I inspected the browser player and service worker, streaming, radio, play-log,
lyrics, video-watch, direct-play, podcast, and audiobook paths, plus focused
tests. No upstream test suite was run and no SoulSync instance was available.
Implemented below means reachable source code was found; tested means a
relevant source test was found.

## Music playback: claim compared with implementation

| Capability | Evidence level | What the source actually does | Material caveat |
| --- | --- | --- | --- |
| Web music player and queue | Implemented; focused tests | The classic player owns an HTML audio element, queue, media controls, progress, and rendering. It saves raw queue items and an index in a fixed browser local-storage key. [Initialization](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/media-player.js#L1-L83); [persistence](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/media-player.js#L3085-L3123). | Reload restores the queue, then deliberately sets its index to minus one. It does not restore playing position, selected output, or a cross-device session. The storage key is not profile-namespaced and queue objects can include a file path. |
| Missing-item playback | Implemented; focused tests | With automatic download disabled, a compatible missing track is mapped to a Deezer preview. With it enabled, the UI waits for a download or stream-preparation path. [Preview selection test](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/src/test/media-player-queue-preview.test.ts#L7-L95). | It is expressly a 30-second 128 kbps preview, not a full remote stream or a transparent library substitute. [Preview implementation](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/streaming/deezer_preview.py#L1-L12); [metadata](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/streaming/deezer_preview.py#L39-L73). |
| Full remote and library streaming | Implemented | The server starts per-listener preparation tasks and exposes listener-scoped status. It range-proxies a media-server URL or local file. [Start/status API](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/web_server.py#L12581-L12657); [range streaming](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/web_server.py#L12666-L12696). | This is browser delivery controlled by SoulSync. It does not demonstrate control of another player queue or resuming on another device. |
| Automatic acquisition and prefetch | Implemented; backend tests present | Queue prefetch calls a protected server endpoint and the player schedules work after queue changes and restoration. [Endpoints](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/web_server.py#L6810-L6841); [browser flow](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/media-player.js#L1540-L1714). | It is a local queue convenience, not a canonical server playback queue. The staging folder is cleared before a new preparation run and preparation has bounded waits. [Preparation](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/streaming/prepare.py#L83-L112); [timeouts](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/streaming/prepare.py#L135-L139). |
| Gapless and crossfade | Crossfade implemented; narrow unit test | Crossfade uses a second browser audio element, ramps both element volumes every 60 ms, then hands off to the next queue item. It is labelled experimental and starts only for sequential, local-library next items with finite durations. [Crossfade engine](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/media-player.js#L2277-L2409). | This is not decoder-level gapless playback. Streamed/non-library entries hard-cut; shuffle, repeat-one, timer end, or a non-sequential next item also prevent the fade. The test uses fake audio and verifies handoff logic, rather than audible joins, decoded buffers, or end-to-end media. [Test](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/src/test/media-player-crossfade.test.ts#L79-L117). |
| Loudness normalization | Marketed as processing | The README describes ReplayGain 2.0 during processing. [Claim](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/README.md#L134-L142). | The inspected browser player contains no ReplayGain parsing or gain application. Do not describe it as verified playback normalization without separately validating the conversion, metadata, and runtime-output path. |
| Synced lyrics | Implemented | Track changes trigger a lyrics fetch; the player highlights timed lines and lets a user seek by selecting one. [Fetch](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/media-player.js#L207-L225); [timing and seek](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/media-player.js#L1035-L1165). | It follows browser time updates. The inspected code provides no evidence of synchronized lyrics on delegated devices. |
| Scrobbling and history | Implemented | The browser log endpoint records playback and determines eligibility at half duration or four minutes, then invokes Last.fm and ListenBrainz work. [Endpoint](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/web_server.py#L11082-L11135). | Logging is best-effort from browser progress; it does not establish a reconciled listening session across devices. |
| Radio | Implemented; selection tests present | Radio draws local database/library candidates. It caps artists and ranks by play count, Last.fm score, recency penalty, and deterministic jitter. [Selection rules](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/radio/selection.py#L37-L43); [ranking](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/radio/selection.py#L159-L220); [route](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/web_server.py#L12550-L12576). | The examined ranking does not create a remote-catalog station. It remains subject to library availability and queue acquisition. |
| Visualization and OS media controls | Implemented | The player uses a Web Audio analyser for visualization and wires browser Media Session controls. [Visualizer graph](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/media-player.js#L3374-L3415). | Browser/OS integration is not casting, speaker grouping, or remote-player control. |

## PWA, device continuity, and long-form media

SoulSync is an installable PWA in the narrow web-app sense: it has a standalone
manifest and registers a service worker. [Manifest](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/manifest.json#L1-L36);
[registration](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/init.js#L3848-L3859).
The service worker caches static assets and images, while HTML and API requests
pass through the network. Inspected code does not cache stream media for
offline listening. [Policy](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/sw.js#L1-L25);
[fetch handling](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/sw.js#L93-L110).

I found no inspected source path for Chromecast, AirPlay, Sonos, a generic
renderer abstraction, or a remote-control/session-handoff protocol. This is
an evidence-bound finding, not proof that no uninspected extension exists.
Per-listener streaming state and a browser-local queue do not demonstrate
target transfer or continuity.

The newer shared media bar has basic HTML-audio controls: 15-second rewind,
30-second forward, and local speed selection. Its own comment says
continuation is caller-owned; source changes reset current time. [Shared player
contract and state](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/src/components/media-player-bar/media-player-bar.tsx#L24-L104);
[controls](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/src/components/media-player-bar/media-player-bar.tsx#L106-L190).
Podcast progress lives in route-provider React state. [Active playback
state](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/src/routes/podcasts/-ui/podcast-context.tsx#L60-L64);
[in-memory update](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/src/routes/podcasts/-ui/podcast-context.tsx#L200-L226).
Audiobooks are explicitly preview samples: selecting an ASIN sets an Audible
preview stream rather than a book session. [Sample context](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/src/routes/audiobooks/-ui/audiobook-context.tsx#L20-L27);
[sample selection](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/src/routes/audiobooks/-ui/audiobook-context.tsx#L135-L159).
No inspected code established server-persisted episode/book bookmarks,
chapters, or cross-device long-form resume.

## Video: server delegation and watch together

SoulSync’s play-on-your-server language should be separated from its own video
player. The watch-together implementation resolves a local file or obtains a
media-server stream URL, reports a direct-play compatibility verdict, and
serves either the local file or an upstream range proxy. [Playable and stream
routes](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/api/video/watch.py#L146-L286).
The verdict uses extension/container/codec knowledge; the inspected watch-stream
endpoint has no transcoding branch. [Direct-play verdict](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/core/video/direct_play.py#L92-L140).

The party client creates a local HTML video element for each opted-in
participant. It synchronizes to a room position every five seconds and seeks
when drift exceeds two seconds. [Local video setup](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/chat.js#L12171-L12247);
[drift correction](https://github.com/Nezreka/SoulSync/blob/9e35006055a5161cbe26f3b7af010ab5f5f2b093/webui/static/chat.js#L12324-L12351).
That is a real shared timeline, but each participant’s browser bears
codec/network compatibility. It is not a server-transcoding party and the
inspected code does not control a remote media server’s native player.

## Practical Lumina differentiation with acceptance criteria

1. **Make continuity a server contract.** Persist canonical queue, cursor,
   elapsed position, rate, repeat/shuffle, and intended target by profile;
   reconcile reconnects against a versioned session. Acceptance: refresh,
   browser restart, and target switch each resume the same queue item within
   one second, without a duplicate scrobble or second download request.
2. **Keep gapless and crossfade separately measurable.** SoulSync has
   conditional UI handoff, so Lumina should describe its audio-clock decoded
   buffer gapless behavior separately from optional crossfade. Acceptance: a
   contiguous-frame fixture has no inserted/omitted samples at its boundary;
   crossfade fixtures preserve ordering under shuffle, repeat, and streaming
   fallback.
3. **Expose source provenance.** Every queue entry should distinguish local
   file, full server stream, download-in-progress, and 30-second preview.
   Acceptance: source kind, duration confidence, availability, and reason are
   available in UI and API; a preview cannot be represented as full playback.
4. **Treat long-form media as durable.** Persist episode/book progress,
   per-title speed, chapters, completion thresholds, and conflict resolution.
   Acceptance: two clients returning from offline use apply an explicit,
   documented conflict policy that honors newer intentional playback (including
   rewinds), preserves completion semantics, and emits exactly one completion
   event.
5. **Define renderer ownership across integrations.** A target-neutral
   playback session should report support for seek, gapless, crossfade, volume,
   subtitles, direct play, and transcode before playback starts. Acceptance: a
   client can render an accurate capability sheet for every selected target.
6. **Make video compatibility actionable.** Keep direct-play, remux, and
   transcode verdicts distinct and provide a reasoned fallback. Acceptance:
   incompatible codec/container fixtures return a deterministic capability
   response and recoverable playback result rather than a client-side failure.
7. **Set an honest offline boundary.** If offline media is supported, make its
   owner scope, quota, explicit removal, and account separation observable. If
   it is not, show online required before play. Acceptance: offline tests prove
   authorized owner-scoped playback, quota handling, removal, and denial after
   a profile/account change.

## Limits

Relevant SoulSync tests exist for queue-preview selection, crossfade handoff,
prefetch, radio selection, Deezer preview, and watch streaming. Their
existence supports intended contracts, but the inspected crossfade test does
not exercise actual output. No statement here establishes interoperability with
an external renderer, real mobile device, or production media server.
