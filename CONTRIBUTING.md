# Contributing

Bug reports, fixes and focused features are welcome. For anything larger than a bug fix, open an issue first so we can agree on the approach before you write it.

## Setup

```bash
mise install
mise exec -- make bootstrap
./scripts/run-dev.sh
```

[docs/BUILDING.md](docs/BUILDING.md) lists host prerequisites. The mocked browser suite needs Chromium once: `mise exec -- npm --prefix frontend exec playwright install chromium`.

## Pull requests

1. Branch from `main`. `main` is protected: changes land through pull requests only.
2. Keep each PR to one change. Add or update tests with the code.
3. Run `mise exec -- make check` and paste its final lines in the PR. There is no hosted CI, so this local run is the gate. [docs/VERIFICATION.md](docs/VERIFICATION.md) maps each kind of change to faster focused commands.
4. Read the relevant [ADR](docs/adr/) before changing an architectural boundary. If you change one, add or amend an ADR in the same PR.
5. Update [docker/README.md](docker/README.md) when operator-facing behaviour changes.

Commits follow [Conventional Commits](https://www.conventionalcommits.org): `fix(player): ...`, `feat(library): ...`, `docs: ...`. Reference the issue (`#123`) when there is one.

## Rules

- Never commit credentials, cookies, tokens, real hostnames or personal media details, including in tests and fixtures. Use `example.com`, `home.arpa` and documentation IP ranges.
- No new runtime dependency without a reason in the PR description.
- Schema changes need an upgrade step and a documented rollback (see [Upgrades](docker/README.md#upgrades)).
- Security issues go through [SECURITY.md](SECURITY.md), not public issues.

Coding agents: start with [AGENTS.md](AGENTS.md).

By contributing you agree your work is licensed under [AGPL-3.0-or-later](LICENSE).
