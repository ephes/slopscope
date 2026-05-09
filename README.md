# slopscope

Colorful repository line-count reports by language, source, tests, and directory, with `cloc` support and a
pure-Python fallback.

This project is not published yet. The repository now contains the initial installable Python package scaffold, a
first `cloc`-backed language summary slice, and internal report data models for language and file rows.

`slopscope` is intended to replace small, repeated `just loc` and `just yaml-lines` implementations with one
reusable Python CLI that can be added as a development dependency.

## Planned Features

- Language summaries using `cloc` when available.
- Pure-Python fallback for environments without `cloc`.
- Rich colored terminal tables with plain text fallback.
- Source-vs-tests summaries for `src/` + `tests/` projects.
- Directory buckets sorted by line count.
- YAML-only total counts for infrastructure repositories.
- Multi-project workspace reports.
- JSON output for future CI or badge integrations.
- Configuration through `pyproject.toml`.

## Usage

```bash
uv run slopscope
uv run slopscope path/to/repository
uv run slopscope --engine cloc
```

For migration compatibility, the package also exposes:

```bash
uv run count-lines-of-code
```

The initial implementation supports `--engine auto|cloc|python`. `auto` and `cloc` use `cloc` when it is available.
The Python fallback is planned but not implemented yet, so `--engine python` fails with a clear message.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The same commands are available through the `justfile` as `just test`, `just lint`, `just format-check`,
`just typecheck`, and `just check`.

## Project Status

This repository is in early implementation. The completed slices are intentionally narrow: package metadata, console
scripts, `cloc` availability detection, language-summary CSV parsing, an internal language-summary report model,
file-level `cloc` CSV parsing, and focused tests. See:

- [Product Requirements](docs/product-requirements.md)
- [Documentation Index](docs/README.md)
- [Roadmap](docs/roadmap.md)
- [Tasks](docs/tasks.md)
- [Configuration](docs/configuration.md)
- [Migration Guide](docs/migration.md)
- [Changelog](CHANGELOG.md)
