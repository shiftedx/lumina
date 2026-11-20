# Music Home and contextual mix server verification

Implementation tickets [#344](https://github.com/shiftedx/lumina/issues/344), [#345](https://github.com/shiftedx/lumina/issues/345) and [#346](https://github.com/shiftedx/lumina/issues/346). Product behavior and UI acceptance are in the [workflow contract](music-home-and-queue-workflows.md).

## Client endpoints

| Operation | Web session | Native connected app |
| --- | --- | --- |
| Read Music Home | `GET /api/me/music-home` | `GET /Lumina/Music/home` |
| Replace Music Home layout | `PUT /api/me/music-home/layout` | `PUT /Lumina/Music/home/layout` |
| Preview contextual mix | `GET /api/music/contextual-mix` | `GET /Lumina/Music/contextual-mix` |

Web mutations carry the existing session CSRF token. Native calls use the existing connected-app identity and root paths. The private route prefix is internal. See [ADR 0010](../adr/0010-connected-apps-and-an-opt-in-jellyfin-compatible-api.md). Preserve the existing browse preference and legacy mix routes. Use the existing queue session APIs when acting on a preview or restoring a full queue.

Home includes canonical `layout`, compact `resume`, up to 12 `recent` album summaries, up to 12 `playlists`, and `mixes` descriptors with `kind` and `name`. Downloads remain device-local. Replacing the layout with `{"order":[],"hidden":[]}` restores the default order and shows all shelves. Resume is null for an empty session. It otherwise includes `state` (`playable` or `waiting`), `revision`, `entry_index`, `current_index`, `position_seconds` and `track` presentation fields. Waiting has a null track ID and no playable URL. Use the revisioned session for actual restore and conflict handling.

Contextual mix takes `seed_kind=track|album|artist`, `seed_id` and optional repeated `exclude_item_ids`. The response has `seed`, `items` and `exhausted`. Items use the existing track shape plus a reason with `code` and its identifying metadata. Up to 100 items are returned. `exhausted` means all eligible results fit after exclusions, including an empty result. An album's tracks come first in disc and track order. A missing or invisible seed returns 404. A visible seed can return no matches. Preview reads leave the queue intact.

## Generated queue entries

Use `GET` and `PUT /api/me/music-session` on web and `GET` and `PUT /Lumina/Music/session` on native. Use `base_revision` and the existing conflict flow for writes.

A generated logical track entry can include `provenance: {source: "contextual_mix", reason: "seed_track" | "same_artist" | "shared_genre"}`. Copy the reason code from the contextual mix response. The server stores those two fields in the existing queue references. Each visible generated track is eligible for one occurrence. Any intentional manual occurrence of the same item takes precedence and all manual duplicates keep their order. Hidden or missing entries are pruned before returning or storing the queue. Acquisition entries and Wait continue to use the existing logical queue policy.

Older clients can read the queue and ignore provenance. A logical rewrite omitting provenance turns those entries into ordinary tracks while preserving the acquisition entries it sends. A flat rewrite keeps its playable order, cursor and position and drops logical acquisition intent as before. Use logical entries for any client that manages acquisitions. Web checkpoints now preserve the optional field. Native codecs can add it in #348.

## Shared fixture check

From the root, start the [disposable proof server](music-home-baseline.md#run-the-shared-server) with `LUMINA_MUSIC_HOME_FIXTURE=1`. Pass its private manifest to the verifier in a second terminal.

```sh
mise exec -- backend/.venv/bin/python scripts/e2e/verify_music_server.py "$LUMINA_NATIVE_FIXTURE"
```

The verifier signs into the same catalog through both surfaces. It checks cross-client layout changes and reset, per-member Resume and recent listening, shared playlist visibility, stable ranked mixes, ordered discs, exclusions, empty results, and private seed isolation. The provenance check adds a hidden item ahead of the selected occurrence and checks the same cursor after reload, along with duplicate pruning, cross-client rewrites, stale conflict responses and the legacy downgrade. It uses the published synthetic fixture only and prints check outcomes without credentials.

## Recorded evidence

- Shared real-server verifier passed both tickets on November 19, 2026.
- 273 focused backend cases passed, covering Music Home and mixes, session revisions, web/native auth and existing Home layout behavior.

```sh
mise exec -- backend/.venv/bin/python -m pytest -q \
  backend/tests/test_music_api.py backend/tests/test_music_playlists.py \
  backend/tests/test_music_listening_session.py backend/tests/test_jellyfin_music_native.py \
  backend/tests/test_jellyfin_home_layout.py backend/tests/test_v1_sessions.py \
  backend/tests/test_media_contract.py
```

- #346 passed 72 focused backend queue, library and output tests plus 27 browser session unit tests. The live fixture passed the web/native queue checks.

## Web Music Home

With the production web build and a fresh shared proof server, run the real browser workflow in installed Chrome.

```sh
mise exec -- node frontend/scripts/verify-music-home.mjs "$LUMINA_NATIVE_FIXTURE"
```

The verifier uses authenticated owner and member browser contexts at desktop and phone widths. It completes the fixture owner's media setup, then exercises `/audio?pane=home`, keyboard focus and shelf editing, layout persistence and isolation, explicit Resume at 0:23 with duplicates and Wait, browsing during playback, saved browse preference, legacy artist and album links, and a real device download. The existing `/audio` browse entry point and playback preferences remain in use. Mixes opens the existing presets and playlists until #351 adds contextual mix actions.

## Capability and legacy fallback

`GET /api/me/music-home/capability` and `GET /Lumina/Music/home/capability` return `{"enabled":true}` or `{"enabled":false}` using their respective authenticated sessions. Old servers return 404. Connectivity and authentication failures leave capability unconfirmed. Set `LUMINA_MUSIC_REWORK_ENABLED=false` to disable Home, its layout writes and contextual preview routes without modifying library browse, presets, playback, queue revisions, saved layouts or downloads.

## Offline checkpoint verification

On a freshly started #343 proof server with the production web build, run this in installed Chrome.

```sh
mise exec -- node frontend/scripts/verify-music-offline-resume.mjs "$LUMINA_NATIVE_FIXTURE"
```

The verifier saves audio from the real player, restarts Chrome using the disposable profile, opens the cached shell while disconnected, and checks that playback starts only on explicit Resume. A second session updates the authoritative revision. The browser reconnects, chooses Keep this device and verifies the local seek was committed with the duplicate occurrences and Wait entry intact. The offline shell resumes the selected downloaded occurrence. The unchanged logical queue stays available for the connected player.
