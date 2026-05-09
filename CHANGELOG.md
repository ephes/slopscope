# Changelog

All notable changes to `slopscope` will be documented here.

## Unreleased

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
- Add pytest, Ruff, mypy, and `just` developer commands.
- Start public project documentation and initial product requirements.
- Clarify migration, configuration, and roadmap docs after initial review.
