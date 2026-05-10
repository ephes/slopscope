# Roadmap

This roadmap is the public backlog for `slopscope`. It intentionally describes project shapes rather than private
source repositories.

## Phase 0: Project Scaffold

- [x] Create Python package skeleton.
- [x] Add CLI entry point `slopscope`.
- [x] Add compatibility entry point `count-lines-of-code`.
- [x] Add test runner, formatter, linter, and type checker.
- [x] Add minimal README and documentation structure.

## Phase 1: Core Counting Model

- [x] Define internal report data model.
- [x] Implement `cloc` availability detection.
- [x] Run `cloc --csv --quiet` for language summaries.
- [x] Run `cloc --by-file --csv --quiet` for file-level aggregation.
- [x] Parse `cloc` CSV output with `csv.DictReader`.
- [x] Preserve `SUM` rows where useful.
- [x] Surface `cloc` failures with clear stderr and non-zero exit.

## Phase 2: Python Fallback

- [x] Implement fallback file discovery.
- [x] Prefer `git ls-files -z` in Git repositories.
- [x] Fall back to filesystem traversal outside Git repositories.
- [x] Implement default language mapping by filename and suffix.
- [x] Implement default excludes for caches, virtualenvs, build output, and dependency directories.
- [x] Count physical lines with UTF-8 and ignored decode errors.
- [x] Mark fallback output clearly as physical-line based.

## Phase 3: Classification and Aggregation

- [x] Implement source-vs-tests classification.
- [x] Implement area classification.
- [x] Implement directory bucketing.
- [x] Support configurable source and test directories.
- [x] Support configurable named areas.
- [x] Support configurable nested buckets such as `shells/<name>`.
- [x] Sort output deterministically by lines, files, then name.

## Phase 4: Rendering

- [x] Implement plain table rendering.
- [x] Implement Rich table rendering.
- [x] Add `--format rich|plain|json`.
- [x] Add `--no-color`.
- [x] Add JSON output matching the internal report model.
- [x] Keep rendering separate from counting and aggregation.

## Phase 5: Configuration

- [x] Load `[tool.slopscope]` from `pyproject.toml`.
- [x] Support `--config`.
- [x] Support excluded directories and languages.
- [x] Support included globs for the Python fallback.
- [x] Support configured source/test dirs, named areas, and nested directory buckets.
- [x] Parse and validate named projects.
- [x] Parse and validate optional projects.
- [x] Parse and validate named profiles.
- [x] Add validation errors for invalid config.

## Phase 6: Profiles

- [x] Add YAML-only total profile support.
- [x] Support physical-line totals for compatibility with `wc -l`-style recipes.
- [x] Support `cloc` code-line totals for YAML.
- [x] Add grouped top-N reports for repeated subtrees.
- [x] Support language filters in profiles.

## Phase 7: Multi-Project Workspaces

- [x] Implement `--project NAME`.
- [x] Implement repeatable project selection.
- [x] Implement `--project all`.
- [x] Render multi-project snapshot.
- [x] Render per-project default reports.
- [x] Add multi-project JSON output with skipped optional projects.
- [x] Skip optional missing projects with a concise notice.
- [x] Fail required missing projects.

## Phase 8: Migration Fixtures

Use public fixture repositories or synthetic test fixtures for these shapes:

- [x] standard Python `src/` + `tests/` package
- [x] Django package with Python, templates, docs, and JavaScript
- [x] infrastructure repo with YAML-only totals
- [x] role-like collection with grouped YAML counts
- [x] multi-project frontend/backend workspace
- [x] desktop-style repository with generated app shell build artifacts

## Phase 9: Release Readiness

- [x] Add package metadata.
- [x] Add installation docs.
- [x] Add migration guide.
- [x] Add changelog workflow.
- [x] Publish first pre-release.
- [x] Migrate at least three representative repositories before `1.0`.
