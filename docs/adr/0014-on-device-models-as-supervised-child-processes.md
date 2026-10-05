# ADR 0014: On-device models run as supervised child processes inside the Lumina image

- Status: Accepted
- Date: 2026-09-26

## Context

Semantic search and subtitles from speech should work on the day a household installs Lumina, on the household's CPU, without a second service to install. The reference host is 4 vCPUs and 8 GiB shared with other services. Sidecar containers need compose changes, and controlling them needs the Docker socket, which is root on the host.

## Decision

- Two CPU runtimes are built into the image: `llama-server` (llama.cpp from a pinned commit, portable CPU build) for embeddings, and a Lumina-owned faster-whisper server in its own venv (`/opt/lumina-asr`) for speech. There is no chat model on the box.
- `app/services/model_supervisor.py` starts one child process per model on demand, on `127.0.0.1` with a per-start secret, and stops it after 5 minutes idle. A second crash within 10 minutes marks the model failed until Retry. Local speech transcription and embedding backfill never run at once.
- Model files come only from a read-only catalog of pinned URLs and sha256s, into `app-data/models/`, verified before use and excluded from backups.
- `app/services/model_endpoints.py` is the one place that decides local vs external. A ready local model wins. It is presented to the existing OpenAI-compatible client code as an ordinary base URL plus secret, so features do not change.

## Consequences

- The image grows by about 200–300 MB. Upgrading a runtime is a Dockerfile/lock change plus `make models-smoke`.
- Model memory counts against the Lumina container. The supervisor refuses to start a model that does not fit, and the feature falls back.
- ADR 0013 still holds. Output from local models is as untrusted as any other and is validated the same way.
