# Changelog

All notable changes to `slopscope` will be documented here.

Release workflow: keep new changes under `Unreleased`. At release time, move the relevant bullets into a dated
version section such as `## 0.1.0a1 - 2026-05-09`, leave a fresh `Unreleased` section for the next cycle, and note
any CLI, configuration, output, migration, or publishing compatibility details.

## Unreleased

- Prepare first pre-release package metadata with version `0.1.0a1`, MIT license text, alpha classifiers, and
  synchronized package version metadata.
- Add the initial installable Python package scaffold with `src/slopscope`.
- Add `slopscope` and `count-lines-of-code` console scripts.
- Implement the first `cloc` language-summary slice with CSV parsing and clear unavailable-engine errors.
- Add an internal language-summary report model used by the CLI rendering path.
- Add internal `cloc --by-file --csv --quiet` command support and file-row CSV parsing.
- Add internal pure-Python fallback file discovery using Git file lists or filesystem traversal.
- Implement pure-Python fallback language summaries with default excludes, filename and suffix language mapping,
  physical-line counting, and explicit physical-line output labeling.
- Add internal source/test, area, and directory classification plus deterministic aggregation over file-level rows.
- Add pure-Python fallback file-level rows using mapped languages and physical line counts.
- Add default report rendering for language, source/test, area, and directory sections with `--format rich|plain|json`
  and `--no-color`.
- Add `[tool.slopscope]` configuration loading from `pyproject.toml` and `--config PATH`.
- Apply configured excludes, language filters, fallback include globs, source/test dirs, named areas, and nested
  directory buckets to the default single-repository report.
- Parse and validate named projects, optional projects, and named profiles for later execution phases.
- Execute configured named profiles with `--profile NAME`.
- Add `--total-only` for profile totals, including YAML physical-line totals compatible with `wc -l`-style recipes.
- Add grouped profile reports for patterns such as `roles/*`, with configured `top` values and `--top N` overrides.
- Add profile JSON output for total and grouped reports.
- Execute configured projects with `--project NAME`, repeatable `--project`, and `--project all`.
- Add multi-project plain, Rich, and JSON reports with project snapshots, per-project default reports, and skipped
  optional projects.
- Skip missing optional project paths with a concise stderr notice and fail missing required project paths clearly.
- Accept `cloc` language summary CSV files that use `files` instead of `filename` for the file-count column.
- Add synthetic migration fixture coverage for standard Python, Django-style, infrastructure YAML, grouped YAML,
  multi-project, and desktop-style repository layouts.
- Add pytest, Ruff, mypy, and `just` developer commands.
- Add Rich to the development dependency group and add a `just loc` dogfood recipe for the local repository report.
- Start public project documentation and initial product requirements.
- Clarify migration, configuration, and roadmap docs after initial review.
- Add installation and release workflow documentation for first pre-release readiness.
- Expand the migration guide with prerequisites, validation commands, semantic-difference notes, and representative
  migration tracking before `1.0`.
- Record the first public-safe representative migration tracking entry, including the expected count difference from
  removing a project-local counter implementation.
