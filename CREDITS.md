# Credits

Lumina is built on other people's work. Thank you.

## Bundled in the Docker image

| Project | Used for | License |
| --- | --- | --- |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Every online source: search, streams, downloads, live chat | Unlicense |
| [FFmpeg](https://ffmpeg.org) via [jellyfin-ffmpeg](https://github.com/jellyfin/jellyfin-ffmpeg) 8.1.2-5 | Probing, remux, transcode, artwork | GPL-3.0 |
| [Intel compute-runtime](https://github.com/intel/compute-runtime) and [graphics compiler](https://github.com/intel/intel-graphics-compiler) | OpenCL tone-mapping on Intel GPUs (amd64) | MIT |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | On-device search models | MIT |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [CTranslate2](https://github.com/OpenNMT/CTranslate2) | On-device transcripts | MIT |
| [FastAPI](https://fastapi.tiangolo.com), [Uvicorn](https://www.uvicorn.org), [SQLAlchemy](https://www.sqlalchemy.org), [Pydantic](https://docs.pydantic.dev), [HTTPX](https://www.python-httpx.org) | Backend | MIT / BSD-3-Clause |
| [Mutagen](https://github.com/quodlibet/mutagen) | Music tags | GPL-2.0-or-later |
| [Node.js](https://nodejs.org) | JavaScript runtime yt-dlp uses for YouTube | MIT |
| [React](https://react.dev), [hls.js](https://github.com/video-dev/hls.js), [dash.js](https://github.com/Dash-Industry-Forum/dash.js), [Lucide](https://lucide.dev) | Web app and player | MIT / Apache-2.0 / BSD-3-Clause / ISC |
| [Newsreader](https://github.com/productiontype/Newsreader) | Typeface | OFL-1.1 ([text](frontend/public/fonts/OFL.txt)) |

Exact versions are pinned in `Dockerfile`, `backend/requirements.runtime.lock` and `frontend/package-lock.json`. License texts ship with the image: Python packages in their `site-packages` metadata, Debian packages under `/usr/share/doc`, llama.cpp at `/opt/lumina-llama/LICENSE`, Node.js at `/usr/local/share/doc/node/LICENSE`, and the web app's bundled packages at `/third-party-licenses.txt` on any running Lumina. FFmpeg source for the bundled build is at the [jellyfin-ffmpeg v8.1.2-5 release](https://github.com/jellyfin/jellyfin-ffmpeg/releases/tag/v8.1.2-5).

On-device models are downloaded only when an admin chooses one, and each model's license is shown before download (see `backend/app/model_catalog.json`).

## Data and services

| Service | Used for |
| --- | --- |
| [TMDB](https://www.themoviedb.org) | Movie and TV metadata and artwork. This product uses the TMDB API but is not endorsed or certified by TMDB. |
| [AniList](https://anilist.co) | Anime catalogue for requests |
| [TheIntroDB](https://theintrodb.org) | Intro and credits markers |
| [MusicBrainz](https://musicbrainz.org) and [Cover Art Archive](https://coverartarchive.org) | Music identities, releases, credits and album artwork |
| [Wikipedia](https://www.wikipedia.org), [Wikidata](https://www.wikidata.org) and [Wikimedia Commons](https://commons.wikimedia.org) | Identity-linked artist biographies and photographs. Photo credits link to the original file and its licence. |
| [LRCLIB](https://lrclib.net) | Public song lyrics |
| YouTube, Twitch, Kick | Public media, through yt-dlp |

## Compatibility

Lumina implements part of the [Jellyfin](https://jellyfin.org) API so Jellyfin apps such as Infuse can connect, and can hand requests to [Sonarr](https://sonarr.tv) and [Radarr](https://radarr.video).

Lumina is an independent project, not affiliated with or endorsed by any project or service named here. All trademarks belong to their owners.

## Social preview artwork

The social-card artwork is original imagery generated for Lumina. Its fictional scenes contain no household media or member details. The cards reuse the project’s Newsreader fonts and horizon mark; regenerate them with `mise exec -- npm --prefix frontend run social:render`.

## Email artwork

Email banners use the vault for invitations, the film archive for delivery messages, and the key illustration for security notices, alongside the app’s horizon mark. Their versioned public assets contain no member details or tracking identifiers. Regenerate them with `mise exec -- npm --prefix frontend run email:render`; render all message variants with `mise exec -- backend/.venv/bin/python scripts/email_preview.py /tmp/lumina-email-preview.html`.

## Onboarding artwork

The onboarding film archive, listening room, creator studio, and security-key illustrations are original imagery generated for Lumina in the media-vault artwork’s engraving style. Their fictional scenes contain no household media, member details, real brands, or tracking identifiers.
