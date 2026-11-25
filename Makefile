.PHONY: up down reset test rules-report

up:            ## start db → migrate → seed → api
	docker compose up --build -d

down:
	docker compose down

reset:         ## wipe the database volume and start fresh
	docker compose down -v && docker compose up --build -d

test:          ## every test layer, unattended (needs the db service running)
	docker compose up -d db
	cd api && uv run pytest -q

rules-report:  ## print the rules report for APP_NOW from a freshly seeded database
	docker compose run --rm --no-deps -e APP_NOW=$${APP_NOW:-2026-11-11T15:00:00Z} api python -m app.rules_report
