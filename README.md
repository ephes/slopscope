# slopscope

Colorful repository line-count reports by language, source, tests, and directory, with `cloc` support and a
pure-Python fallback.

This project is not published yet. The repository now contains the initial installable Python package scaffold,
`cloc`-backed language summaries and file summaries, internal report data models, a pure-Python fallback for
physical-line reports, default path classification, and rendered reports for language, source/test, area, and
directory summaries.

`slopscope` is intended to replace small, repeated `just loc` and `just yaml-lines` implementations with one
reusable Python CLI that can be added as a development dependency.

## Current Features

- Language summaries using `cloc` when available.
- Pure-Python fallback for environments without `cloc`.
- Plain text output without optional dependencies.
- Optional Rich colored terminal tables with plain text fallback.
- Source-vs-tests summaries for `src/` + `tests/` projects.
- Directory buckets sorted by line count.
- Repository area summaries for source, tests, docs, scripts, examples, specs, and tooling.
- JSON output for future CI or badge integrations.

## Planned Features

- Configuration through `pyproject.toml`.
- YAML-only total counts for infrastructure repositories.
- Multi-project workspace reports.
- Grouped top-N reports.

## Usage

```bash
uv run slopscope
uv run slopscope path/to/repository
uv run slopscope --engine cloc
uv run slopscope --engine python
uv run slopscope --format plain
uv run slopscope --format json
uv run slopscope --no-color
```

For migration compatibility, the package also exposes:

```bash
uv run count-lines-of-code
```

The current implementation supports `--engine auto|cloc|python`. `auto` uses `cloc` when it is available and falls
back to the Python engine when `cloc` is not on `PATH`. `--engine cloc` keeps failing clearly when `cloc` is
unavailable.

The Python engine discovers files with `git ls-files` when possible and otherwise walks the filesystem. It applies
default excludes for common caches, virtual environments, dependency directories, and build output, then counts
physical lines in mapped text-like files using UTF-8 with ignored decode errors. Python fallback reports are marked
with `Engine: python (physical lines)`.

Human-readable output defaults to `--format rich`. Rich is optional: when it is not importable, `slopscope` falls
back to the plain renderer. Use `--format plain` for deterministic dependency-free text, `--format json` for
structured output, or `--no-color` to force colorless human-readable output.

Default rendered sections are:

- Language Summary
- Source vs Tests
- Repository Areas
- Directory Buckets

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
scripts, `cloc` availability detection, language-summary and file-summary CSV parsing, fallback file discovery,
fallback language mapping, physical-line counting, internal aggregation for source/test, area, and directory
summaries, and plain/Rich/JSON rendering for the default single-repository report. See:

- [Product Requirements](docs/product-requirements.md)
- [Documentation Index](docs/README.md)
- [Roadmap](docs/roadmap.md)
- [Tasks](docs/tasks.md)
- [Configuration](docs/configuration.md)
- [Migration Guide](docs/migration.md)
- [Changelog](CHANGELOG.md)
