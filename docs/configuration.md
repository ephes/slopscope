# Configuration

`slopscope` works without configuration for standard Python repositories. Repositories with generated artifacts,
non-standard source or test layouts, multiple projects, or special line-count profiles can configure behavior in
`pyproject.toml`.

The current implementation loads `[tool.slopscope]` for the default single-repository report. It also parses and
validates configured projects and profiles so those sections are ready for later phases, but it does not yet execute
`--project`, `--profile`, YAML total mode, grouped reports, or multi-project reports.

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

Expected commands:

```bash
slopscope --project all
slopscope --project frontend
slopscope --project backend
```

These commands are planned for the multi-project phase. Today, project entries are validated only. Project paths are
resolved relative to the configuration file, and `optional = true` is accepted as config data.

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

This command is planned for the profiles phase. Today, profile entries are validated only. Supported profile fields
are `name`, `include_languages`, `exclude_languages`, `include_globs`, `physical_lines`, `group_by`, and `top`.

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
```

This command is planned for the profiles phase.

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
- The positional path remains the repository inspected by the default single-repository report; configured projects
  are not selected automatically.
- Optional project skipping and required project failures are planned for the multi-project phase.
- Language filtering is applied to parsed `cloc` rows and to fallback file mappings.
- `exclude_dirs` augments fallback default excludes. For `cloc`, configured `exclude_dirs` are applied to parsed
  file rows and language totals are recomputed from the filtered file rows.
- `include_globs` applies to Python fallback discovery by relative path. It does not change `cloc` command input in
  this phase.
