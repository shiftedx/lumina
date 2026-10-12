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

- Run focused tests while iterating and `make check` before handing work back.
- Match the surrounding code: naming, comment density, idioms. No speculative abstractions.
- Every change ships with a test that fails without it.
- Schema changes: add a step to `MIGRATIONS` in `backend/app/db.py`, keep upgrades additive, document the rollback in `docker/README.md`.
- Treat AI model output as untrusted input (ADR 0013).
- Never write credentials, tokens, real hostnames or private media details into code, tests, docs or issues.
- See [CONTRIBUTING.md](CONTRIBUTING.md) for PR and commit conventions.
