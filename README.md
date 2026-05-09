# slopscope

Colorful repository line-count reports by language, source, tests, and directory, with `cloc` support and a
pure-Python fallback.

This project is not published yet. The repository now contains the initial installable Python package scaffold,
`cloc`-backed language summaries, internal report data models for language and file rows, and a pure-Python fallback
for physical-line language summaries.

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
uv run slopscope --engine python
```

For migration compatibility, the package also exposes:

```bash
uv run count-lines-of-code
```

The initial implementation supports `--engine auto|cloc|python`. `auto` uses `cloc` when it is available and falls
back to the Python engine when `cloc` is not on `PATH`. `--engine cloc` keeps failing clearly when `cloc` is
unavailable.

The Python engine discovers files with `git ls-files` when possible and otherwise walks the filesystem. It applies
default excludes for common caches, virtual environments, dependency directories, and build output, then counts
physical lines in mapped text-like files using UTF-8 with ignored decode errors. Python fallback reports are marked
with `Engine: python (physical lines)`.

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
file-level `cloc` CSV parsing, fallback file discovery, fallback language mapping, physical-line counting, and
focused tests. See:

- [Product Requirements](docs/product-requirements.md)
- [Documentation Index](docs/README.md)
- [Roadmap](docs/roadmap.md)
- [Tasks](docs/tasks.md)
- [Configuration](docs/configuration.md)
- [Migration Guide](docs/migration.md)
- [Changelog](CHANGELOG.md)
