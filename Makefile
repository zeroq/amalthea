# Amalthea — verification entry points.
#
# WHY THIS FILE EXISTS (risk R11 / TODO 1.18)
# ------------------------------------------
# "mypy clean" and "the suite is green" previously named *whatever command you happened to type*.
# `mypy amalthea` reported 0 errors across 10 files while `mypy .` reported 95. A gate whose result
# depends on which paths you name is not a gate. So the Definition of Done lives here, once, and
# every target below is pinned to a single explicit invocation.
#
# Run `make check` before every commit. The git pre-commit hook runs `check-fast`, which is the
# subset cheap enough to run on every save without slowing you down.

PY := .venv/bin/python
PIP := .venv/bin/pip
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy
PYTEST := .venv/bin/pytest
export DJANGO_SETTINGS_MODULE := amalthea.settings.test

.DEFAULT_GOAL := help
.PHONY: help check check-fast quality tests typing migrations coverage audit lock hooks secrets clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

## ---------------------------------------------------------------------------------------------
## The Definition of Done
## ---------------------------------------------------------------------------------------------

check: ## Full DoD gate — run this before committing (lint, types, tests, migrations)
	@echo "── ruff check";        $(RUFF) check .
	@echo "── ruff format";       $(RUFF) format --check . --quiet || $(RUFF) format --check .
	@echo "── mypy (scope pinned in pyproject.toml: 73 files)"; $(MYPY)
	@echo "── django check";      $(PY) manage.py check
	@echo "── migrations in sync"; $(PY) manage.py makemigrations --check --dry-run
	@echo "── pytest";            $(PYTEST) -q
	@echo ""
	@echo "✓ check passed"

check-fast: ## Subset cheap enough for a pre-commit hook (lint + tests only)
	@$(RUFF) check . --quiet
	@$(RUFF) format --check . --quiet
	@$(PYTEST) -q
	@echo "✓ check-fast passed"

quality: ## Lint and format only
	@$(RUFF) check . && $(RUFF) format --check .

tests: ## Test suite only
	@$(PYTEST) -q

typing: ## Type check only (scope is pinned in pyproject.toml, not on the command line)
	@$(MYPY)

migrations: ## Verify no model change is missing a migration
	@$(PY) manage.py makemigrations --check --dry-run

## ---------------------------------------------------------------------------------------------
## Security
## ---------------------------------------------------------------------------------------------

secrets: ## Scan the tracked tree for committed secrets (hooks and CI enforce this too)
	@./scripts/secret-scan.sh --tracked

## ---------------------------------------------------------------------------------------------
## Non-blocking signals
## ---------------------------------------------------------------------------------------------

coverage: ## Coverage report. NON-BLOCKING until the Phase 6 MVP gate (plan §13 #13).
	@$(PYTEST) -q --cov --cov-report=term-missing || true
	@echo ""
	@echo "NOTE: fail_under is 80 but coverage is informational until Phase 6 by decision."
	@echo "      Padding the number with tests that assert nothing is the R11 failure mode."

audit: ## Security and dependency audit
	@$(PY) -m bandit -r . -x './.venv,./tests' -q || true
	@$(PIP) list --outdated --format=columns | head -20

lock: ## Regenerate the pinned, hash-verified requirement closure (requirements/{base,dev}.txt)
	@./scripts/lock.sh

hooks: ## Install the pre-commit + pre-push git hooks
	@./scripts/install-hooks.sh

clean: ## Remove caches and build artefacts
	@find . -path ./.venv -prune -o -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	@echo "✓ cleaned (caches only — .venv and the database are untouched)"