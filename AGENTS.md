# AGENTS.md

Lumina is a self-hosted household media server: FastAPI + SQLite backend, React + TypeScript + Vite frontend, shipped as one Docker image.

## Layout

| Path | What |
| --- | --- |
| `backend/app/` | API routers, services, models, `config.py` (all `LUMINA_*` settings) |
| `backend/tests/` | pytest suite |
| `frontend/src/` | React app; Vitest tests (`*.test.ts[x]`) |
| `frontend/e2e/` | Playwright: mocked API specs, plus `realstack/` against the real backend |
| `docs/adr/` | Architecture decisions. Read the relevant ones before changing a boundary |
| `docker/README.md` | Operator guide. Some backend tests parse its rollback SQL blocks |

## Commands

Prefix with `mise exec --` so the pinned toolchain in `.tool-versions` is used.

```bash
make bootstrap                                  # install locked deps
make backend-test                               # pytest
cd backend && .venv/bin/python -m pytest -q tests/test_x.py   # one file
make frontend-test                              # vitest
make frontend-typecheck
make check                                      # full gate, required before a PR
```

## Rules

- Dogfood fixes: when continuing the owner's one-issue-at-a-time batch, read the [continuation handoff](docs/design/dogfood-next-handoff.md) first.
- Run focused tests while iterating and `make check` before handing work back.
- Match the surrounding code: naming, comment density, idioms. No speculative abstractions.
- Branding: before changing logos, color tokens, buttons or navigation styling, read [the shared web/iOS/tvOS brand contract](apple/docs/brand-assets.md).
- Behavior changes must have a meaningful regression check at the public API, service boundary, or user workflow. Reuse or extend an existing contract test before adding another.
- Do not add one test per helper, CSS declaration, class name, or implementation detail. Prefer observable behavior; keep release/build contracts and trust-boundary edge cases.
- Before deleting a test, name the retained contract that covers its useful behavior. Do not merge unrelated assertions merely to lower the reported count.
- Schema changes: add a step to `MIGRATIONS` in `backend/app/db.py`, keep upgrades additive, document the rollback in `docker/README.md`.
- Deployments: follow `docker/README.md#retaining-a-rollback-image`; create image pins with `scripts/preserve-rollback.sh`, keep rollback containers stopped, and preserve their cleanup exclusions.
- Treat AI model output as untrusted input (ADR 0013).
- Never write credentials, tokens, real hostnames or private media details into code, tests, docs or issues.
- See [CONTRIBUTING.md](CONTRIBUTING.md) for PR and commit conventions.
