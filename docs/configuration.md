# Configuration

`slopscope` works without configuration for standard Python repositories. Repositories with generated artifacts,
non-standard source or test layouts, multiple projects, or special line-count profiles can configure behavior in
`pyproject.toml`.

The current implementation loads `[tool.slopscope]` for the default single-repository report, executes configured
named profiles, and executes configured projects for multi-project workspace reports.

## No Configuration

Standard Python repositories should not need a `[tool.slopscope]` block. The default behavior should:

- ignore common generated paths such as `.git`, `.venv`, `node_modules`, `build`, and `dist`
- treat `src/` as source
- treat `tests/` and common test filename patterns as tests
- classify docs, examples, specs, scripts, and root tooling into useful default areas

If the inspected path has no `pyproject.toml`, or if `pyproject.toml` has no `[tool.slopscope]` table, these defaults
are used without an error.

## Loading Configuration

By default, `slopscope` looks for `pyproject.toml` in the inspected path:

```bash
slopscope path/to/repository
```

Use `--config PATH` to load a specific TOML file:

```bash
slopscope --config path/to/pyproject.toml path/to/repository
```

When `--config` is supplied, the file must exist and must contain valid TOML. Invalid TOML, unknown fields, invalid
field types, duplicate project or profile names, and conflicting language filters fail with a `slopscope:` error on
stderr and exit code 2.

## Basic Configuration

```toml
[tool.slopscope]
exclude_languages = ["JSON", "Markdown"]
exclude_dirs = [".git", ".venv", "node_modules", "build", "dist"]
source_dirs = ["src"]
test_dirs = ["tests"]
areas = ["src", "tests", "docs", "examples", "specs", "scripts", "tooling"]
nested_bucket_dirs = ["packages"]
```

Supported top-level fields in this phase:

- `include_languages`: array of language names to keep
- `exclude_languages`: array of language names to remove
- `exclude_dirs`: array of directory names or relative path prefixes to exclude
- `include_globs`: array of relative path globs for Python fallback file discovery
- `source_dirs`: array of source directory paths
- `test_dirs`: array of test directory paths
- `areas`: array of named area directory paths
- `nested_bucket_dirs`: array of directory paths whose next child should become a bucket

If both `include_languages` and `exclude_languages` are configured, a language may not appear in both lists.
`include_languages` limits rows first, then `exclude_languages` removes rows from the remaining set.

## Multi-Project Workspace

```toml
[tool.slopscope]
exclude_languages = ["JSON", "Markdown"]

[[tool.slopscope.projects]]
name = "frontend"
path = "."

[[tool.slopscope.projects]]
name = "backend"
path = "../backend"
optional = true
```

Available commands:

```bash
slopscope --project all
slopscope --project frontend
slopscope --project backend
slopscope --project frontend --project backend
```

Project paths are resolved relative to the configuration file. `--project all` runs configured projects in config
order. Repeated `--project NAME` selections also render in config order, and duplicate explicit selections are a
usage error. `--project all` cannot be mixed with named project selections.

Top-level filters and classification fields apply to every selected project. Project-specific overrides are not
implemented yet.

Required project paths must exist. Missing required projects fail with a `slopscope:` error and a non-zero exit.
Missing optional projects are skipped with a concise stderr notice and appear in JSON output under
`skipped_projects`. If all selected projects are optional and missing, `slopscope` still renders a successful
multi-project report with no project rows and the skipped project list.

`--project` cannot be combined with `--profile` in the current implementation.

## YAML Total Profile

```toml
[[tool.slopscope.profiles]]
name = "yaml"
include_languages = ["YAML"]
include_globs = ["*.yml", "*.yaml"]
physical_lines = true
```

Expected command:

```bash
slopscope --profile yaml --total-only
```

This command prints one integer plus a newline, with no headings or engine metadata. Use `physical_lines = true` when
compatibility with `wc -l`-style YAML recipes matters. Physical-line profiles use Python fallback discovery and
physical-line counting even if `--engine cloc` is selected.

With `physical_lines = false`, profiles use normal engine semantics: `--engine cloc` and available `--engine auto`
use `cloc` code counts from file-level rows, while `--engine python` or unavailable `cloc` in `auto` mode use Python
physical-line rows.

Supported profile fields are `name`, `include_languages`, `exclude_languages`, `include_globs`, `physical_lines`,
`group_by`, and `top`.

## Grouped Report Profile

```toml
[[tool.slopscope.profiles]]
name = "roles"
include_languages = ["YAML"]
group_by = "roles/*"
top = 20
```

Expected command:

```bash
slopscope --profile roles
slopscope --profile roles --top 10
```

Grouped reports support plain, Rich, and JSON output. Rich remains optional and falls back to plain output when Rich
is unavailable or disabled. A pattern such as `roles/*` groups matching files by `roles/<name>`. Files that do not
match the group pattern are ignored for grouped rows and grouped totals. Rows are sorted by code descending, files
descending, then name.

The configured `top` value limits rendered rows. `--top N` overrides the configured value. `--total-only` with a
grouped profile prints the total across all matched grouped rows, not only the visible top-N rows.

Example JSON output:

```json
{
  "engine": "python",
  "path": ".",
  "profile": "roles",
  "group_by": "roles/*",
  "top": 20,
  "total": 123,
  "physical_lines": false,
  "rows": [{"name": "roles/web", "files": 2, "code": 80}]
}
```

## Generated Artifact Excludes

Desktop, frontend, or documentation-heavy repositories often need path-specific excludes:

```toml
[tool.slopscope]
exclude_dirs = [
  ".git",
  ".venv",
  "node_modules",
  "build",
  "dist",
  "docs/_build",
  "htmlcov",
  "shells/electron/dist",
  "shells/electron/node_modules",
  "shells/tauri/src-tauri/target",
]
```

## Notes

- Project paths are resolved relative to the configuration file.
- The positional path remains the repository inspected by the default single-repository report and the location used
  for default config discovery when `--config` is not supplied. Configured projects are selected only when
  `--project` is supplied.
- Language filtering is applied to parsed `cloc` rows and to fallback file mappings. For profiles, non-empty profile
  language filters replace top-level language filters. If a profile leaves a language filter empty, the matching
  top-level filter is used.
- `exclude_dirs` augments fallback default excludes. For `cloc`, configured `exclude_dirs` are applied to parsed
  file rows and language totals are recomputed from the filtered file rows. Top-level `exclude_dirs` always apply to
  profiles.
- For the default report, `include_globs` applies to Python fallback discovery by relative path and does not change
  `cloc` command input. For profiles, non-empty profile `include_globs` replace top-level include globs, and profile
  include globs also filter parsed `cloc` file rows by relative path.
