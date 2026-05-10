test:
    uv run pytest

lint:
    uv run ruff check .

format:
    uv run ruff format .

format-check:
    uv run ruff format --check .

typecheck:
    uv run mypy

loc:
    uv run slopscope --engine python .

check:
    uv run pytest
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy
