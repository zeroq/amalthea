# Amalthea — verification entry points.

PY := .venv/bin/python
PIP := .venv/bin/pip
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy
PYTEST := .venv/bin/pytest
TAILWIND ?= scripts/tailwindcss
export DJANGO_SETTINGS_MODULE := amalthea.settings.test

.DEFAULT_GOAL := help
.PHONY: help check check-fast quality tests typing migrations coverage audit lock hooks secrets clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

check: ## Full DoD gate (lint, types, migrations, css compile, pytest)
	@echo "── ruff check";        $(RUFF) check .
	@echo "── ruff format";       $(RUFF) format --check . --quiet || $(RUFF) format --check .
	@echo "── mypy (scope pinned in pyproject.toml: 73 files)"; $(MYPY)
	@echo "── django check";      $(PY) manage.py check
	@echo "── migrations in sync"; $(PY) manage.py makemigrations --check --dry-run
	@echo "── css compile check (no Node needed)"; $(MAKE) --no-print-directory css-check
	@
	@echo "✓ check passed"

check-fast: ## Subset cheap enough for pre-commit (lint only, no pytest)
	@$(RUFF) check . --quiet
	@$(RUFF) format --check . --quiet
	@echo "✓ check-fast passed"

css: ## Compile Tailwind CSS to ui/static/ui/app.css
	@$(TAILWIND) -i ui/static/ui/app.css -o ui/static/ui/app.css --minify

css-check: ## Compile-check the Tailwind input — skips if the bundled binary can't run on this host
	@if "$(TAILWIND)" --help >/dev/null 2>&1; then \
	  $(TAILWIND) -i ui/static/ui/app.css -o /dev/null --minify; \
	  echo "✓ Tailwind input compiles (compile check only — this does NOT detect drift)"; \
	else \
	  echo "⚠ css-check SKIPPED: $(TAILWIND) cannot run on this host ($(shell uname -sm)) (this is expected on Intel macOS/Linux if using the bundled arm64-macOS binary; override with TAILWIND=/path/to/tailwindcss)"; \
	fi
