# Music Home and contextual mix server verification

Implementation tickets [#344](https://github.com/shiftedx/lumina/issues/344) and [#345](https://github.com/shiftedx/lumina/issues/345). Product behavior and UI acceptance are in the [workflow contract](music-home-and-queue-workflows.md).

## Client endpoints

| Operation | Web session | Native connected app |
| --- | --- | --- |
| Read Music Home | `GET /api/me/music-home` | `GET /Lumina/Music/home` |
| Replace Music Home layout | `PUT /api/me/music-home/layout` | `PUT /Lumina/Music/home/layout` |
| Preview contextual mix | `GET /api/music/contextual-mix` | `GET /Lumina/Music/contextual-mix` |

Web mutations carry the existing session CSRF token. Native calls use the existing connected-app identity and root paths. The private route prefix is internal. See [ADR 0010](../adr/0010-connected-apps-and-an-opt-in-jellyfin-compatible-api.md). Preserve the existing browse preference and legacy mix routes. Use the existing queue session APIs when acting on a preview or restoring a full queue.

Home includes canonical `layout`, compact `resume`, up to 12 `recent` album summaries, up to 12 `playlists`, and `mixes` descriptors with `kind` and `name`. Downloads remain device-local. Replacing the layout with `{"order":[],"hidden":[]}` restores the default order and shows all shelves. Resume is null for an empty session. It otherwise includes `state` (`playable` or `waiting`), `revision`, `entry_index`, `current_index`, `position_seconds` and `track` presentation fields. Waiting has a null track ID and no playable URL. Use the revisioned session for actual restore and conflict handling.

Contextual mix takes `seed_kind=track|album|artist`, `seed_id` and optional repeated `exclude_item_ids`. The response has `seed`, `items` and `exhausted`. Items use the existing track shape plus a reason with `code` and its identifying metadata. Up to 100 items are returned. `exhausted` means all eligible results fit after exclusions, including an empty result. An album's tracks come first in disc and track order. A missing or invisible seed returns 404. A visible seed can return no matches. Preview reads leave the queue intact.

## Shared fixture check

From the root, start the [disposable proof server](music-home-baseline.md#run-the-shared-server) with `LUMINA_MUSIC_HOME_FIXTURE=1`. Pass its private manifest to the verifier in a second terminal.

```sh
mise exec -- backend/.venv/bin/python scripts/e2e/verify_music_server.py "$LUMINA_NATIVE_FIXTURE"
```

The verifier signs into the same catalog through both surfaces. It checks cross-client layout changes and reset, per-member Resume and recent listening, shared playlist visibility, stable ranked mixes, ordered discs, exclusions, empty results, and private seed isolation. It uses the published synthetic fixture only and prints check outcomes without credentials.

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
