# slopscope

Colorful repository line-count reports by language, source, tests, and directory, with `cloc` support and a
pure-Python fallback.

This project is not published yet. The repository now contains the initial installable Python package scaffold,
`cloc`-backed language summaries and file summaries, internal report data models, a pure-Python fallback for
physical-line reports, default path classification, rendered reports for language, source/test, area, and directory
summaries, `[tool.slopscope]` configuration loading from `pyproject.toml`, and configured profile execution for
YAML totals and grouped top-N reports.

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
- Configuration from `[tool.slopscope]` in `pyproject.toml`, or from `--config PATH`.
- Configured excludes, included fallback globs, language filters, source/test dirs, named areas, and nested buckets
  for the default single-repository report.
- Named profile execution from `[tool.slopscope.profiles]`.
- YAML-only total profiles with `--total-only` integer output.
- Physical-line profile totals for compatibility with `wc -l`-style recipes.
- Grouped top-N profile reports for path patterns such as `roles/*`.
- JSON output for future CI or badge integrations.

## Planned Features

- Multi-project workspace reports.

## Usage

```bash
uv run slopscope
uv run slopscope path/to/repository
uv run slopscope --engine cloc
uv run slopscope --engine python
uv run slopscope --format plain
uv run slopscope --format json
uv run slopscope --no-color
uv run slopscope --config path/to/pyproject.toml
uv run slopscope --profile yaml --total-only
uv run slopscope --profile roles --top 20
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

By default, `slopscope` looks for `[tool.slopscope]` in `pyproject.toml` under the inspected path. Missing
configuration keeps the built-in defaults. Use `--config PATH` to load a specific TOML file; missing files, invalid
TOML, and invalid field types fail with a clear `slopscope:` error and exit code 2.

Current single-repository configuration supports:

- `exclude_languages` and `include_languages`
- `exclude_dirs`
- `include_globs` for the Python fallback
- `source_dirs` and `test_dirs`
- `areas`
- `nested_bucket_dirs`

Configured `profiles` can now be selected with `--profile NAME`. Configured `projects` are parsed and validated, but
selecting or executing projects is planned for a later phase.

Profile execution supports:

- `include_languages`, `exclude_languages`, and `include_globs`
- `physical_lines = true` for Python fallback physical-line totals, even when `--engine cloc` is selected
- `physical_lines = false` for normal engine semantics: `cloc` code lines when `cloc` is selected or available, and
  Python physical lines when the Python fallback is selected
- `group_by = "roles/*"` style grouped reports, displayed as `roles/<name>`
- `top = N` in config, with `--top N` as a CLI override
- `--total-only` for one integer plus a newline; without `--profile`, `--total-only` is a usage error

When a profile sets its own language filters or include globs, those profile values are used for that profile.
Top-level values are used only when the profile field is empty. Top-level `exclude_dirs` always apply.

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
summaries, plain/Rich/JSON rendering for the default single-repository report, and configuration loading for that
report plus named profile execution for YAML totals and grouped top-N reports. See:

- [Product Requirements](docs/product-requirements.md)
- [Documentation Index](docs/README.md)
- [Roadmap](docs/roadmap.md)
- [Tasks](docs/tasks.md)
- [Configuration](docs/configuration.md)
- [Migration Guide](docs/migration.md)
- [Changelog](CHANGELOG.md)
