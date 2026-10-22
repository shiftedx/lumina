PYTHON ?= python3

.PHONY: help bootstrap bootstrap-test backend-test frontend-test frontend-typecheck frontend-build e2e-realstack e2e-mocked e2e-smoke e2e-full e2e-tier-test e2e-browsers playperf models-smoke perf check

help:
	@echo "Available targets:"
	@echo "  bootstrap          Install locked backend and frontend dependencies"
	@echo "  bootstrap-test     Test platform-specific bootstrap behavior"
	@echo "  backend-test       Run the Python test suite"
	@echo "  frontend-test      Run deterministic frontend module tests"
	@echo "  frontend-typecheck Type-check the React application"
	@echo "  frontend-build     Type-check and build the React application"
	@echo "  e2e-realstack      Browser journeys and the perf budgets against the real backend and synthetic media"
	@echo "  e2e-smoke          49 critical browser journeys plus setup, under 10 minutes"
	@echo "  e2e-full           Full parallel browser gate, under 45 minutes"
	@echo "  e2e-mocked         Browser specs against a mocked API (Vite dev server)"
	@echo "  e2e-browsers       Focused real-decode WebKit remux/transcode playback (also in full; needs 'npx playwright install webkit')"
	@echo "  playperf           Library playback timing in Google Chrome: cold-cache first frame (direct, resume, music, deep link, slow side requests), resumes, quality switches (not in check)"
	@echo "  models-smoke       Real llama-server + speech server with tiny downloaded models, in the image (not in check; needs Docker and network)"
	@echo "  perf               Household-scale load + browser measurement on seeded temp data (not in check)"
	@echo "  check              Run all verification targets"

bootstrap:
	$(PYTHON) scripts/bootstrap.py

bootstrap-test:
	$(PYTHON) -m unittest discover -s scripts -p 'test_*.py'

backend-test:
	$(PYTHON) scripts/run_backend_tests.py

frontend-typecheck:
	npm --prefix frontend run typecheck

frontend-test:
	npm --prefix frontend test

frontend-build:
	npm --prefix frontend run build

# The perf budgets run throttled, so one real stack at a time on this host (every checkout shares the lock): parallel
# checks never time each other. One invocation, so one fixture server start.
PERF_LOCK ?= /tmp/lumina-perf.lock
e2e-realstack: frontend-build
	scripts/with-lock.sh $(PERF_LOCK) 1800 sh -c 'cd frontend && npx playwright test -c playwright.realstack.config.ts --project=setup --project=journeys --project=perf'

# Tier output must be empty; the driver allocates isolated loopback ports and fixture roots.
E2E_REPEAT ?= 1
E2E_TIER_WORKERS ?= 4
E2E_OUTPUT ?= $(shell mktemp -d /tmp/lumina-playwright-tier.XXXXXX)
e2e-tier-test:
	node --test frontend/scripts/run-playwright-tiers.node-test.mjs

e2e-smoke:
	node frontend/scripts/run-playwright-tiers.mjs --tier smoke --repeat $(E2E_REPEAT) --workers $(E2E_TIER_WORKERS) --output "$(E2E_OUTPUT)"

e2e-full:
	scripts/with-lock.sh $(PERF_LOCK) 1800 node frontend/scripts/run-playwright-tiers.mjs --tier full --repeat $(E2E_REPEAT) --workers $(E2E_TIER_WORKERS) --output "$(E2E_OUTPUT)"

E2E_WORKERS ?= 2
e2e-mocked:
	cd frontend && npx playwright test --workers=$(E2E_WORKERS)

e2e-browsers: frontend-build
	cd frontend && npx playwright test -c playwright.realstack.config.ts --project=setup --project=webkit-playback

playperf: frontend-build
	scripts/with-lock.sh $(PERF_LOCK) 1800 sh -c 'cd frontend && npx playwright test -c playwright.playperf.config.ts'

models-smoke:
	docker build -t lumina-models-smoke:local .
	docker run --rm --entrypoint python -v "$(CURDIR)/scripts:/smoke:ro" lumina-models-smoke:local /smoke/models_smoke.py

perf: frontend-build
	@d=$$(mktemp -d) && trap 'rm -rf "$$d"' EXIT && backend/.venv/bin/python scripts/perf/seed_household.py "$$d/data" && backend/.venv/bin/python -u scripts/perf/load.py "$$d/data" --ui

check: bootstrap-test e2e-tier-test frontend-typecheck frontend-build backend-test frontend-test e2e-full

.PHONY: seed-titles perf-titles
seed-titles:
	backend/.venv/bin/python scripts/perf/seed_titles.py "$(DATA)"

perf-titles:
	@d=$$(mktemp -d) && trap 'rm -rf "$$d"' EXIT && backend/.venv/bin/python scripts/perf/seed_titles.py "$$d" && cd backend && PERF_TITLES_ROOT="$$d" .venv/bin/python -m pytest tests/perf/test_title_budgets.py tests/perf/test_reco_budgets.py tests/perf/test_reco_title_budgets.py tests/perf/test_live_youtube_budgets.py -q -p no:cacheprovider
