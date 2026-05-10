# Tasks

This file is the lightweight in-repo task tracker until project issues are created. Keep tasks public-safe: describe
repository shapes and product behavior, not private source projects.

## Current

- [ ] Confirm final pre-release version, package registry, and publishing credentials before publishing.
- [ ] Publish first pre-release after explicit release-owner approval.

## Done

- [x] Create Python package skeleton.
- [x] Add `slopscope` CLI entry point.
- [x] Add `count-lines-of-code` compatibility entry point.
- [x] Add test, lint, format, and typecheck commands.
- [x] Implement the core report data model.
- [x] Implement `cloc` availability detection.
- [x] Implement `cloc` CSV parsing for language summaries.
- [x] Implement `cloc` file-level parsing for directory/source/test aggregation.
- [x] Implement fallback file discovery with Git and filesystem traversal.
- [x] Implement pure-Python fallback language mapping, default excludes, and physical line counts.
- [x] Implement fallback file-level rows with physical line counts.
- [x] Implement path classification for source, tests, docs, examples, scripts, specs, and tooling.
- [x] Implement source/test, area, and directory aggregation over file-level rows.
- [x] Implement plain text rendering for the default single-repository report.
- [x] Implement optional Rich rendering with plain fallback.
- [x] Implement JSON rendering for the default single-repository report.
- [x] Add `--format rich|plain|json` and `--no-color`.
- [x] Load `[tool.slopscope]` from `pyproject.toml`.
- [x] Add `--config PATH`.
- [x] Apply configured excludes, language filters, included fallback globs, source/test dirs, named areas, and nested
  buckets to the default single-repository report.
- [x] Parse and validate named projects, optional projects, and named profiles.
- [x] Surface invalid config with clear stderr and a non-zero exit.
- [x] Execute named profiles from configuration.
- [x] Support YAML total profiles with `--total-only`.
- [x] Support physical-line profile totals for compatibility with `wc -l`-style recipes.
- [x] Support grouped top-N profiles.
- [x] Support `--top N` overrides for grouped profiles.
- [x] Implement `--project NAME`, repeatable `--project`, and `--project all`.
- [x] Render multi-project workspace snapshots and per-project default reports.
- [x] Add multi-project JSON output with skipped optional projects.
- [x] Skip optional missing projects and fail required missing projects.
- [x] Add fixture coverage for standard Python, Django-style, infrastructure, grouped YAML, multi-project, and
  desktop-style layouts.
- [x] Preserve `SUM` rows from `cloc` language summaries.
- [x] Surface `cloc` failures with stderr and a non-zero exit.
- [x] Create public documentation scaffold.
- [x] Write sanitized product requirements.
- [x] Write public roadmap/backlog.
- [x] Write initial configuration guide.
- [x] Write initial migration guide.
- [x] Prepare first pre-release package metadata with synchronized project and package versions.
- [x] Add MIT license text for packaged release artifacts.
- [x] Add installation guidance for source checkouts, editable development installs, future pre-release package
  installs, optional `cloc`, and optional Rich.
- [x] Add release workflow documentation for checks, local builds, artifact inspection, changelog updates, and
  publish authorization boundaries.
- [x] Expand the migration guide with prerequisites, validation commands, semantic-difference notes, and public-safe
  representative migration tracking.
- [x] Document lightweight changelog workflow.
- [x] Complete the pre-`1.0` representative migration requirement with at least three real authorized migrations,
  recording only public-safe repository shapes and validation notes.
- [x] Add small-tooling pre-release consumption evidence for the sibling-checkout `just loc` recipe without counting it
  as a replacement migration.

## Later

- [ ] Decide whether Rich is a default dependency or optional extra.
- [ ] Decide whether the compatibility alias remains permanent.
- [ ] Decide whether YAML total mode defaults to physical lines or `cloc` code lines.
- [ ] Evaluate whether `sloccount` compatibility is worth implementing.
