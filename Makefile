.PHONY: up down reset test test-api e2e e2e-setup rules-report gen-api

up:              ## start db → migrate → seed → api → web  (http://localhost:3000)
	docker compose up --build -d --wait api web

down:
	docker compose down

reset:           ## wipe the database volume and start fresh
	docker compose down -v && $(MAKE) up

test: test-api e2e   ## every layer, unattended: rules + DB + API (pytest), then two-browser E2E (Playwright)

test-api:        ## rules, DB constraint and API tests against a rebuilt l10_test database, frozen clock
	docker compose up -d --wait db
	cd api && uv sync --frozen -q && uv run pytest -q

e2e-setup:
	cd web && pnpm install --frozen-lockfile && pnpm exec playwright install chromium firefox

e2e: e2e-setup   ## Playwright against the real stack, reset to the seed first; failures keep a trace in web/test-results
	docker compose up --build -d --wait api web
	docker compose run --rm --no-deps seed python -m app.db.seed --reset /seed/seed.json
	cd web && pnpm exec playwright test

rules-report:    ## print the rules report for APP_NOW (default 2026-11-11T15:00:00Z) from a freshly seeded database
	@docker compose build --quiet migrate seed api >/dev/null  # this checkout's code; stdout stays pure JSON
	@docker compose up -d --wait db >/dev/null 2>&1
	@docker compose run --rm --no-deps migrate >/dev/null
	@docker compose run --rm --no-deps seed python -m app.db.seed --reset /seed/seed.json >/dev/null
	@docker compose run --rm --no-deps -e APP_NOW=$${APP_NOW:-2026-11-11T15:00:00Z} api python -m app.rules_report

gen-api:         ## regenerate web/openapi.json and the TypeScript types from the API
	cd web && pnpm gen:api
