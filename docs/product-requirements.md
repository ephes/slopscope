# Product Requirements: slopscope

Date: 2026-05-08
Status: Draft

## Summary

`slopscope` is a small Python command-line tool for repository line-count reports. It should provide useful,
colorful output for everyday development while keeping enough structure for automation and future trend tracking.

The core value is not just a total line count. The tool should answer:

- Which languages make up this repository?
- How much code is source code?
- How much code is tests?
- Which directories carry most of the code?
- Can the same command work across normal Python packages, infrastructure repos, and multi-project workspaces?

## Problem Statement

Many repositories grow their own line-count scripts over time. Those scripts usually start as a few lines of shell
or Python, then accrete behavior:

- invoking `cloc`
- parsing `cloc` output
- excluding build artifacts and virtual environments
- grouping files into source, tests, docs, and tooling
- rendering colored tables
- adding fallback behavior when `cloc` is missing
- handling multi-project layouts

This creates repeated maintenance work and inconsistent output. `slopscope` should centralize the common behavior
while allowing repo-specific configuration.

## Goals

- Provide one reusable development dependency for repository line-count reports.
- Prefer `cloc` when it is installed.
- Provide a deterministic Python fallback when `cloc` is absent.
- Render colored terminal tables by default when Rich is available.
- Always provide a plain text fallback.
- Show language totals.
- Show source-vs-tests counts and ratio.
- Show directory buckets sorted by line count.
- Support YAML-only totals for infrastructure-style repositories.
- Support grouped reports such as top directories or role-like subtrees.
- Support multi-project workspaces.
- Keep repo-specific excludes and project layouts in configuration, not shell recipes.
- Expose a migration-compatible `count-lines-of-code` alias.

## Non-Goals

- Replacing mature counters such as `cloc`, `tokei`, or `scc`.
- Perfect semantic equivalence between `cloc` and the Python fallback.
- A persistent metrics database in the first version.
- CI trend charts in the first version.
- A web UI.
- Deep language parsing in Python.

## Users

Primary users:

- Developers who want a quick local overview of repository size and structure.
- Maintainers who want consistent `just loc` commands across multiple projects.
- Infrastructure developers who need YAML-only or grouped counts.

Secondary users:

- CI jobs or release scripts that want JSON output for reporting.

## CLI Requirements

The package should expose:

```bash
slopscope [PATH]
count-lines-of-code [PATH]
```

`count-lines-of-code` should be a compatibility alias for repositories that already use that command name.

Required options:

- `--config PATH`: read configuration from a non-default file.
- `--project NAME`: select one configured project; repeatable or accepts `all`.
- `--profile NAME`: select a named profile such as `default`, `yaml`, or `roles`.
- `--engine auto|cloc|python`: force or auto-select the counting engine.
- `--format rich|plain|json`: choose output format.
- `--no-color`: disable color.
- `--include-language NAME`: limit language output.
- `--exclude-language NAME`: exclude language output.
- `--include-glob GLOB`: include fallback files by glob.
- `--exclude-dir PATH`: add an excluded directory or path.
- `--total-only`: print only the final line count.
- `--top N`: limit directory or grouped output.

## Configuration Requirements

Configuration should live in `pyproject.toml`.

Defaults should work for a standard Python repository with `src/` and `tests/` directories.

Configuration must support:

- excluded directories and path prefixes
- included and excluded languages
- source directories
- test directories
- named areas such as docs, examples, specs, scripts, and tooling
- named projects with relative paths
- optional projects that can be skipped when missing
- named profiles for YAML totals or grouped reports

Example:

```toml
[tool.slopscope]
exclude_languages = ["JSON", "Markdown"]
exclude_dirs = [".git", ".venv", "node_modules", "build", "dist"]
source_dirs = ["src"]
test_dirs = ["tests"]
areas = ["src", "tests", "docs", "examples", "specs", "scripts", "tooling"]

[[tool.slopscope.projects]]
name = "frontend"
path = "."

[[tool.slopscope.projects]]
name = "backend"
path = "../backend"
optional = true

[[tool.slopscope.profiles]]
name = "yaml"
include_languages = ["YAML"]
physical_lines = true
```

## Report Requirements

Default report sections:

1. Overall language summary
2. Source vs Tests, when source/test directories exist or are configured
3. Repository overview by area, when areas are configured or inferable
4. Lines of Code by Directory

Multi-project report sections:

1. Project snapshot with files, code lines, and tests/source ratio
2. Per-project default report

YAML total profile:

- Must support output compatible with simple `just yaml-lines` recipes: a single number.
- Must be able to count physical lines to preserve `wc -l`-style behavior.
- Should also support `cloc` YAML code lines when requested.

Grouped profile:

- Must support grouping by a path prefix such as `roles/*` or `packages/*`.
- Must support top-N output.
- Must support language filtering for grouped reports.

## Counting Semantics

The tool must make counting semantics explicit:

- `cloc` engine: code, comment, and blank counts come from `cloc`.
- Python fallback: counts physical lines per included file unless a future parser adds language-specific code-line
  support.
- Physical-line profiles: count all physical lines in matched files.

Each report should identify the selected engine unless `--total-only` is used.

## Path Classification

Default path rules:

- `tests` area when any path segment is `tests`, or filename matches `test_*`, `*_test.py`, `.test.`, or `.spec.`
- `src` area for configured source directories, defaulting to `src`
- `docs` area for `docs`
- configured named areas such as `scripts`, `examples`, and `specs`
- everything else falls into `tooling`

Default directory buckets:

- `src/<next-part>` for source trees
- `tests` or `tests/<next-part>` for test trees
- `docs` or `docs/<next-part>` when useful
- configured nested buckets such as `shells/<next-part>`
- top-level directory fallback
- `.` for root files

## Rich Output

Rich output should use color:

- names: cyan
- files: magenta
- code and line counts: green
- metadata: dim
- section headings: blue or bold blue

The tool must degrade gracefully to plain text when Rich is unavailable or disabled.

## JSON Output

JSON output should expose the same data model as the rendered reports:

- engine
- projects
- language rows
- source/test summary
- area rows
- directory rows
- skipped optional projects

## Acceptance Criteria

- A repository can add `slopscope` as a development dependency and run `uv run slopscope`.
- Existing `count-lines-of-code` workflows can continue through the compatibility alias.
- A standard Python package gets language, source/test, area, and directory summaries without configuration.
- An infrastructure-style repository can emit a single-number YAML total.
- A grouped profile can render a top-N report for repeated subtrees, such as a `roles/` or `packages/`
  directory with many sibling projects.
- A multi-project workspace can render a project snapshot and per-project reports.
- Repo-specific generated directories can be excluded through configuration.
- The command exits non-zero when a required selected project path is missing.
- Optional missing projects are skipped with a stderr notice and successful exit.
- Unit tests cover `cloc` CSV parsing, fallback scanning, path classification, configuration loading, and rendering.
