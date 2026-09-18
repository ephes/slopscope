# slopscope

Colorful repository line-count reports by language, source, tests, and directory, with `cloc` support and a
pure-Python fallback.

The first pre-release is published as `0.1.0a1`. The repository contains the installable Python package,
`cloc`-backed language summaries and file summaries, internal report data models, a pure-Python fallback for
physical-line reports, default path classification, rendered reports for language, source/test, area, and directory
summaries, `[tool.slopscope]` configuration loading from `pyproject.toml`, configured profile execution for YAML
totals and grouped top-N reports, and configured multi-project workspace reports.

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
- Python composition report (`--composition`): every physical line of every Python file in one structural category
  (blank, comment, docstring, import, definition, assertion, error handling, literal data, other code), statement
  counts, continuation lines, overlapping semantic tags (Qt, logging, data shapes) resolved through imports, a
  compatibility-wording marker, test-file placement (tests, fixtures, setup, module level, helpers), token-based
  duplicate detection, the largest modules, classes, and functions, JSON snapshots with baseline comparison, and
  opt-in monthly churn from Git history. Standard library `ast` and `tokenize` only, configurable through
  `[tool.slopscope.composition]`.

## Planned Features

- Persistent metrics and trend storage beyond single JSON snapshots.

## Installation

Install the published pre-release as a development dependency. Pre-release resolution must be enabled while the latest
available version is pre-release-only:

```bash
uv add --dev --prerelease allow slopscope
uv run slopscope
```

For pip-based environments:

```bash
python -m pip install --pre slopscope
slopscope --help
```

To use a source checkout:

```bash
git clone https://github.com/ephes/slopscope.git
cd slopscope
uv run slopscope --help
uv run slopscope --engine python .
```

To use the checkout as an editable development dependency in another repository:

```bash
uv add --dev --editable ../slopscope
uv run slopscope
```

`cloc` is optional. With `--engine auto`, `slopscope` uses `cloc` when the binary is available on `PATH` and falls
back to the pure-Python engine otherwise. Install `cloc` separately if you want `cloc` code-line semantics.

Rich is also optional. Human-readable output defaults to `--format rich`, but if Rich is not installed the command
falls back to plain text. Install Rich separately for colored tables when using `0.1.0a1`; the base install has no
runtime dependencies and remains correct for `--format plain` and `--format json`. The unreleased development tree
also exposes a `rich` extra for the next package release.

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
uv run slopscope --composition
uv run slopscope --composition --format json > composition.json
uv run slopscope --composition --project all --limit 20
uv run slopscope --composition --snapshot composition.json
uv run slopscope --composition --baseline composition.json
uv run slopscope --composition --churn
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

`--composition` switches to the Python composition report. Where the default report answers "how many lines", the
composition report answers "lines of what". It parses each discovered `.py` and `.pyi` file with the standard library
`ast` and `tokenize` modules and reports:

- line categories that add up to the physical line count, split by source, tests, and other files
- statements (`ast.stmt` nodes), code lines per statement, and continuation lines, which are size signals independent
  of formatter layout
- semantic tags that overlap the categories: `qt`, `logging`, and `data_shape`, each resolved through the file's own
  imports, plus a `compat` marker for identifiers that name themselves legacy, fallback, or compat code
- construct counts, and where test-file code lines sit: tests, fixtures, setup, module level, or helpers
- duplicated code lines and the largest duplicate blocks, found on exact token sequences so formatting and comments
  do not matter
- per-area totals
- the largest modules by code lines, and the largest classes and functions by span, with qualified names such as
  `src/pkg/module.py:Outer.method`

It reuses discovery, excludes, source/test classification, `--config`, `--project`, `--format`, and `--no-color`.
`--limit N` sets the size of the largest-item lists (default 10). `--snapshot PATH` also writes the JSON report to a
file, and `--baseline PATH` compares with an earlier snapshot, rejecting snapshots with a different schema version.
`--churn` adds Python lines added and removed per month on a configured branch's first-parent history.
`[tool.slopscope.composition]` configures list sizes, the duplicate threshold, extra Qt modules, opt-in logging
patterns, compatibility marker words, and the churn branch and window. `--engine`, `--profile`, `--total-only`, and `--top`
cannot be combined with `--composition`. Files that cannot be read, decoded, or parsed are reported on stderr and in
the report, and make the command exit with code 1. JSON output has its own `report_type: "composition"` shape. See
[Composition Report](docs/composition.md) for the category definitions, counting rules, JSON shape, and known limits.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The same commands are available through the `justfile` as `just test`, `just lint`, `just format-check`,
`just typecheck`, and `just check`. Use `just loc` to dogfood the local `slopscope` package with the Python engine
and Rich output from the development environment.

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
reports, synthetic migration fixture coverage, release workflow documentation, and the first, structural slice of the
Python composition report. See:

- [Product Requirements](docs/product-requirements.md)
- [Documentation Index](docs/README.md)
- [Roadmap](docs/roadmap.md)
- [Tasks](docs/tasks.md)
- [Configuration](docs/configuration.md)
- [Composition Report](docs/composition.md)
- [Migration Guide](docs/migration.md)
- [Release Workflow](docs/release.md)
- [Changelog](CHANGELOG.md)
