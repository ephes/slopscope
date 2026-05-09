# Tasks

This file is the lightweight in-repo task tracker until project issues are created. Keep tasks public-safe: describe
repository shapes and product behavior, not private source projects.

## Current

- [ ] Implement the core report data model.
- [ ] Implement `cloc` file-level parsing for directory/source/test aggregation.
- [ ] Implement pure-Python fallback scanning.
- [ ] Implement path classification for source, tests, docs, examples, scripts, and tooling.
- [ ] Implement plain text rendering.
- [ ] Implement Rich rendering.
- [ ] Implement JSON rendering.
- [ ] Load `[tool.slopscope]` from `pyproject.toml`.
- [ ] Support named projects and optional projects.
- [ ] Support YAML total profiles.
- [ ] Support grouped top-N profiles.
- [ ] Add fixture coverage for standard Python, infrastructure, grouped, multi-project, and desktop-style layouts.

## Done

- [x] Create Python package skeleton.
- [x] Add `slopscope` CLI entry point.
- [x] Add `count-lines-of-code` compatibility entry point.
- [x] Add test, lint, format, and typecheck commands.
- [x] Implement `cloc` availability detection.
- [x] Implement `cloc` CSV parsing for language summaries.
- [x] Preserve `SUM` rows from `cloc` language summaries.
- [x] Surface `cloc` failures with stderr and a non-zero exit.
- [x] Create public documentation scaffold.
- [x] Write sanitized product requirements.
- [x] Write public roadmap/backlog.
- [x] Write initial configuration guide.
- [x] Write initial migration guide.

## Later

- [ ] Decide whether Rich is a default dependency or optional extra.
- [ ] Decide whether the compatibility alias remains permanent.
- [ ] Decide whether YAML total mode defaults to physical lines or `cloc` code lines.
- [ ] Evaluate whether `sloccount` compatibility is worth implementing.
- [ ] Publish first pre-release.
