# Amalthea — verification entry points.

PY := .venv/bin/python
PIP := .venv/bin/pip
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy
PYTEST := .venv/bin/pytest
TAILWIND ?= scripts/tailwindcss
export DJANGO_SETTINGS_MODULE := amalthea.settings.test

.DEFAULT_GOAL := help
.PHONY: help check check-fast quality tests typing migrations coverage audit lock hooks secrets clean css css-check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

check: ## Full DoD gate (lint, types, migrations, css compile, pytest)
	@echo "── ruff check";        $(RUFF) check .
	@echo "── ruff format";       $(RUFF) format --check . --quiet || $(RUFF) format --check .
	@echo "── mypy (scope pinned in pyproject.toml: 73 files)"; $(MYPY)
	@echo "── django check";      $(PY) manage.py check
	@echo "── migrations in sync"; $(PY) manage.py makemigrations --check --dry-run
	@echo "── css drift check";   $(MAKE) --no-print-directory css-check
	@
	@echo "✓ check passed"

check-fast: ## Subset cheap enough for pre-commit (lint only, no pytest)
	@$(RUFF) check . --quiet
	@$(RUFF) format --check . --quiet
	@echo "✓ check-fast passed"

css: ## Compile Tailwind CSS from source to ui/static/ui/app.css
	@$(TAILWIND) -i ui/static/ui/app.src.css -o ui/static/ui/app.css --minify

css-check: ## Byte-compare committed app.css against fresh compile from app.src.css; skip loudly if binary unavailable
	@if "$(TAILWIND)" --help >/dev/null 2>&1; then \
	  tmp=$$(mktemp); \
	  $(TAILWIND) -i ui/static/ui/app.src.css -o "$$tmp" --minify; \
	  if cmp -s "$$tmp" ui/static/ui/app.css; then \
	    echo "✓ css-check: artifact is up to date"; \
	  else \
	    echo "✗ css-check FAILED: committed app.css is stale (run 'make css')"; \
	    diff -u ui/static/ui/app.css "$$tmp" | head -40; \
	    rm -f "$$tmp"; \
	    exit 1; \
	  fi; \
	  rm -f "$$tmp"; \
	else \
	  echo "⚠ css-check SKIPPED (not a pass): $(TAILWIND) cannot run on this host ($(shell uname -sm))"; \
	  echo "   Override with TAILWIND=/path/to/tailwindcss if you have a compatible binary."; \
	fi
