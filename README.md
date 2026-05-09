# slopscope

Colorful repository line-count reports by language, source, tests, and directory, with `cloc` support and a
pure-Python fallback.

This project is prepared for its first pre-release but is not published yet. The repository contains the installable
Python package, `cloc`-backed language summaries and file summaries, internal report data models, a pure-Python
fallback for physical-line reports, default path classification, rendered reports for language, source/test, area,
and directory summaries, `[tool.slopscope]` configuration loading from `pyproject.toml`, configured profile
execution for YAML totals and grouped top-N reports, and configured multi-project workspace reports.

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
- Multi-project workspace reports from `[tool.slopscope.projects]`.
- Optional project skipping for missing configured projects.
- JSON output for future CI or badge integrations.

## Planned Features

- Persistent metrics and trend storage.

## Installation

Until the first package release is published, use a source checkout:

```bash
git clone <public-repository-url> slopscope
cd slopscope
uv run slopscope --help
uv run slopscope --engine python .
```

To use the checkout as a development dependency in another repository before publication:

```bash
uv add --dev --editable ../slopscope
uv run slopscope
```

After a pre-release is published to a package index, install it like any other development dependency. Pre-release
resolution must be enabled when the latest available version is pre-release-only:

```bash
uv add --dev --prerelease allow slopscope
uv run slopscope
```

For pip-based environments:

```bash
python -m pip install --pre slopscope
slopscope --help
```

`cloc` is optional. With `--engine auto`, `slopscope` uses `cloc` when the binary is available on `PATH` and falls
back to the pure-Python engine otherwise. Install `cloc` separately if you want `cloc` code-line semantics.

Rich is also optional. Human-readable output defaults to `--format rich`, but if Rich is not installed the command
falls back to plain text. Install Rich separately in projects that want colored tables; no runtime dependency is
required for plain text or JSON output.

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
uv run slopscope --project frontend
uv run slopscope --project frontend --project backend
uv run slopscope --project all
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

Configured `profiles` can be selected with `--profile NAME`. Configured `projects` can be selected with
`--project NAME`, repeated as needed, or `--project all`.

Project execution supports:

- paths resolved relative to the configuration file
- top-level filters and classification settings applied to each project
- multi-project plain, Rich, and JSON output
- optional missing projects skipped with a concise stderr notice
- required missing projects failing clearly with a non-zero exit

`--project` and `--profile` cannot be combined in the current implementation.

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

Build the local package without publishing:

```bash
uv build
uv run python -m zipfile -l dist/slopscope-*.whl
uv run python -m tarfile -l dist/slopscope-*.tar.gz
rm -rf dist
```

Publishing is intentionally not part of the normal development check. Confirm the target registry, credentials, and
final version before running a publish command.

## Project Status

This repository is in pre-release readiness. The completed slices are intentionally narrow: package metadata,
console scripts, `cloc` availability detection, language-summary and file-summary CSV parsing, fallback file
discovery, fallback language mapping, physical-line counting, internal aggregation for source/test, area, and
directory summaries, plain/Rich/JSON rendering for the default single-repository report, configuration loading for
that report, named profile execution for YAML totals and grouped top-N reports, configured multi-project workspace
reports, synthetic migration fixture coverage, and release workflow documentation. See:

- [Product Requirements](docs/product-requirements.md)
- [Documentation Index](docs/README.md)
- [Roadmap](docs/roadmap.md)
- [Tasks](docs/tasks.md)
- [Configuration](docs/configuration.md)
- [Migration Guide](docs/migration.md)
- [Release Workflow](docs/release.md)
- [Changelog](CHANGELOG.md)
