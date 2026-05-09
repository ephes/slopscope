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
- [ ] Run `cloc --by-file --csv --quiet` for file-level aggregation.
- [x] Parse `cloc` CSV output with `csv.DictReader`.
- [x] Preserve `SUM` rows where useful.
- [x] Surface `cloc` failures with clear stderr and non-zero exit.

## Phase 2: Python Fallback

- [ ] Implement fallback file discovery.
- [ ] Prefer `git ls-files -z` in Git repositories.
- [ ] Fall back to filesystem traversal outside Git repositories.
- [ ] Implement default language mapping by filename and suffix.
- [ ] Implement default excludes for caches, virtualenvs, build output, and dependency directories.
- [ ] Count physical lines with UTF-8 and ignored decode errors.
- [ ] Mark fallback output clearly as physical-line based.

## Phase 3: Classification and Aggregation

- [ ] Implement source-vs-tests classification.
- [ ] Implement area classification.
- [ ] Implement directory bucketing.
- [ ] Support configurable source and test directories.
- [ ] Support configurable named areas.
- [ ] Support configurable nested buckets such as `shells/<name>`.
- [ ] Sort output deterministically by lines, files, then name.

## Phase 4: Rendering

- [ ] Implement plain table rendering.
- [ ] Implement Rich table rendering.
- [ ] Add `--format rich|plain|json`.
- [ ] Add `--no-color`.
- [ ] Add JSON output matching the internal report model.
- [ ] Keep rendering separate from counting and aggregation.

## Phase 5: Configuration

- [ ] Load `[tool.slopscope]` from `pyproject.toml`.
- [ ] Support `--config`.
- [ ] Support excluded directories and languages.
- [ ] Support included globs.
- [ ] Support named projects.
- [ ] Support optional projects.
- [ ] Support named profiles.
- [ ] Add validation errors for invalid config.

## Phase 6: Profiles

- [ ] Add YAML-only total profile support.
- [ ] Support physical-line totals for compatibility with `wc -l`-style recipes.
- [ ] Support `cloc` code-line totals for YAML.
- [ ] Add grouped top-N reports for repeated subtrees.
- [ ] Support language filters in profiles.

## Phase 7: Multi-Project Workspaces

- [ ] Implement `--project NAME`.
- [ ] Implement `--project all`.
- [ ] Render multi-project snapshot.
- [ ] Skip optional missing projects with a concise notice.
- [ ] Fail required missing projects.

## Phase 8: Migration Fixtures

Use public fixture repositories or synthetic test fixtures for these shapes:

- [ ] standard Python `src/` + `tests/` package
- [ ] Django package with Python, templates, docs, and JavaScript
- [ ] infrastructure repo with YAML-only totals
- [ ] role-like collection with grouped YAML counts
- [ ] multi-project frontend/backend workspace
- [ ] desktop-style repository with generated app shell build artifacts

## Phase 9: Release Readiness

- [ ] Add package metadata.
- [ ] Add installation docs.
- [ ] Add migration guide.
- [ ] Add changelog workflow.
- [ ] Publish first pre-release.
- [ ] Migrate at least three representative repositories before `1.0`.
