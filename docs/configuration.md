# Configuration

`slopscope` should work without configuration for standard Python repositories. Repositories with generated
artifacts, multiple projects, or special line-count profiles can configure behavior in `pyproject.toml`.

## No Configuration

Standard Python repositories should not need a `[tool.slopscope]` block. The default behavior should:

- ignore common generated paths such as `.git`, `.venv`, `node_modules`, `build`, and `dist`
- treat `src/` as source
- treat `tests/` and common test filename patterns as tests
- classify docs, examples, specs, scripts, and root tooling into useful default areas

## Basic Configuration

```toml
[tool.slopscope]
exclude_languages = ["JSON", "Markdown"]
exclude_dirs = [".git", ".venv", "node_modules", "build", "dist"]
source_dirs = ["src"]
test_dirs = ["tests"]
areas = ["src", "tests", "docs", "examples", "specs", "scripts", "tooling"]
```

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

- Project paths should be resolved relative to the configuration file.
- Optional projects should be skipped when missing.
- Required selected projects should fail when missing.
- Language filtering should apply to `cloc` directly and be approximated by fallback file mappings.
