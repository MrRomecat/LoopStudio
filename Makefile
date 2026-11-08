# Эквивалент check.ps1 для POSIX-окружений / будущего CI.
PY ?= python

.PHONY: check lint format-check type test

check: lint format-check type test
	@echo "OK: все проверки зелёные."

lint:
	$(PY) -m ruff check .

format-check:
	$(PY) -m ruff format --check .

type:
	$(PY) -m mypy

test:
	$(PY) -m pytest
