# Changelog

All notable changes to `slopscope` will be documented here.

Release workflow: keep new changes under `Unreleased`. At release time, move the relevant bullets into a dated
version section such as `## 0.1.0a1 - 2026-05-09`, leave a fresh `Unreleased` section for the next cycle, and note
any CLI, configuration, output, migration, or publishing compatibility details.

## Unreleased

## 0.3.0a1 - 2026-09-18

Compatibility notes: the default report, profiles, multi-project reports, the composition report, and their JSON shapes
are unchanged. `--size-limits` is a new, opt-in mode with its own JSON shape (`report_type: "size_limits"`,
`schema_version` 1). The new `[tool.slopscope.size_limits]` configuration section is rejected by `0.2.0a1` and earlier,
so repositories that add it need `0.3.0a1` or later. No new runtime dependencies are required.

- Add `slopscope --size-limits`, a local check that holds oversized units at their current size. Functions and
  methods (default 150 span lines), classes (1,500 span lines), and test files (3,000 code lines) are measured with
  the composition report's span and code-line definitions. Units over a limit must be recorded in a JSON allowlist
  keyed as `path::Qualified.name`, where they may shrink but not grow. The report lists new offenders, grown and
  shrunk allowlisted units, and stale entries; `--strict` exits 1 on new or grown offenders; `--seed-allowlist` writes
  the first allowlist and refuses to overwrite one; `--update-allowlist` lowers and drops entries but never adds or
  raises one. Configure limits and the allowlist path in `[tool.slopscope.size_limits]`; with `--project`, each
  project has its own allowlist. JSON output uses `report_type: "size_limits"` with `schema_version: 1`.
- Split duplicate-block assembly out of the composition report builder, which the new check flagged as a function
  over 150 span lines. Composition output is unchanged.

## 0.2.0a1 - 2026-09-18

Compatibility notes: the default report, profiles, multi-project reports, and their JSON shapes are unchanged. The
composition report is opt-in through `--composition` and has its own JSON shape (`schema_version` 4). The new
`[tool.slopscope.composition]` configuration section is rejected by `0.1.0a1`, so repositories that add it need
`0.2.0a1` or later. `--help` lists new options and shows `--engine`'s `auto` default. `--churn` runs `git log` only
when requested; no new runtime dependencies are required.

- Add a `rich` optional extra (`slopscope[rich]`) so downstream projects without Rich as a runtime dependency can opt
  into colored Rich tables; without the extra, the default `--format rich` continues to fall back to plain output.
- Update post-release documentation now that `0.1.0a1` is published on the package index and GitHub.
- Add an opt-in Python composition report, `slopscope --composition [PATH]`. It assigns every physical line of every
  discovered Python file one structural category (`blank`, `comment`, `docstring`, `import`, `definition`,
  `assertion`, `error_handling`, `literal_data`, `other_code`) using the standard library `ast` and `tokenize`
  modules, and reports statement counts, continuation lines, code lines per statement, per source/test and area
  totals, and the largest modules, classes, and functions. It supports `--config`, `--project`, `--format`, and
  `--no-color`; `--limit N` sets the largest-item list size. Read, decode, and parse failures are reported on stderr
  and exit with code 1. JSON output uses a new, separate shape with `report_type: "composition"` (or
  `"composition_projects"` with `--project`); its first released `schema_version` is 4, because the shape changed
  while the report was developed.
- Reject `--engine`, `--profile`, `--total-only`, and `--top` together with `--composition`, and `--limit`,
  `--snapshot`, `--baseline`, and `--churn` without it, with argparse usage errors. The default report, profiles, and existing JSON shapes are unchanged; `--help` now
  lists the new options and shows `--engine`'s `auto` default in its help text.
- Add semantic tags to the composition report as a separate, overlapping dimension over code lines: `qt` for names
  bound from PySide/PyQt/qtpy imports, `logging` for the `logging` module and loggers assigned from
  `logging.getLogger()`, and `data_shape` for fields of dataclass, attrs, enum, NamedTuple, TypedDict, and pydantic
  classes, all resolved through imports. Add a `compat` wording marker, construct counts, and test-file placement into
  `test`, `fixture`, `setup`, `module_level`, and `helper_or_unknown` with conservative pytest and unittest rules. JSON
  output lists every detector with its version.
- Add token-based duplicate detection to the composition report: exact token windows anchored at line starts are
  verified token by token, extended while every copy agrees, and merged into per-file duplicated code lines and a list
  of the largest duplicate blocks with all their copies.
- Add `--snapshot PATH` to write the composition JSON to a file and `--baseline PATH` to compare with an earlier
  snapshot. Baselines with a different report type or schema version are rejected; analyzer or detector version
  differences are reported as warnings.
- Add a `[tool.slopscope.composition]` configuration section with `limit`, `min_duplicate_tokens`, extra `qt_modules`,
  opt-in `logging_patterns`, `compat_markers`, `churn_branch`, and `churn_months`. Baselines report differing counting
  settings as warnings.
- Add `--churn` to the composition report: Python lines added and removed per month on the configured branch's
  first-parent history, limited to the analyzed path, following renames, and skipped with a notice outside Git.
- Amend the "deep language parsing" non-goal to allow the opt-in, Python-only composition report.

## 0.1.0a1 - 2026-05-10

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
- Complete the public-safe representative migration tracker for pre-`1.0` with real package, raw shell recipe, and
  multi-project workspace migrations.
- Record small-tooling pre-release consumption evidence for the sibling-checkout `just loc` recipe without counting it
  as a replacement migration.
- Normalize absolute `cloc --by-file` paths before classification so configured project reports keep source/test and
  directory buckets relative to each selected project.
