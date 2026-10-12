# Building and verifying Lumina

The repository declares Python 3.12.14 and Node.js 22.17.1 in `.tool-versions`. Use those versions, not whatever is installed globally.

## Host prerequisites

Install Git, GNU Make, Docker Engine with the Compose plugin, and [mise](https://mise.jdx.dev/getting-started.html) using the installation method appropriate for your operating system. Docker is not needed for the unit, type and build checks; it is needed for the Docker Compose deployment.

## Clean-clone gate

From a new clone of the exact candidate commit:

```bash
git status --short
mise install
mise exec -- python scripts/preflight.py
mise exec -- make bootstrap
mise exec -- make check
```

The first command must print nothing. `scripts/preflight.py` fails if the declared Python or Node versions are not active or if Git, Make, npm, or Docker Compose is unavailable. `make bootstrap` installs the locked backend and frontend dependencies without relying on an existing virtual environment or `node_modules` directory.

The supported distribution is Docker Compose. After the clean gate, follow `docker/README.md` for the exact artifact build, loopback startup, backup, and current-release restore procedures. Every release starts from a fresh deployment; there is no cross-version migration or downgrade.
