#!/usr/bin/env bash

set -euo pipefail

# This only checks the format, but doesn't fix it.
uv run ruff format --check
uv run ruff check

# To fix the format manually run:
# uv run ruff format
# uv run ruff check --fix

# Check the type hints
uv run basedpyright

# Check for spelling errors
uv run codespell

# Run the tests
uv run pytest
