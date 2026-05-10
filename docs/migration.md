# Migration Guide

This guide describes how repositories should replace local line-count logic with `slopscope`.

## Prerequisites

Use Python 3.11 or newer. Until the package is published, install from a source checkout or use the checkout directly
with `uv run`.

For a repository that wants to depend on a local checkout before publication:

```bash
uv add --dev --editable ../slopscope
```

After a pre-release is published to a package index:

```bash
uv add --dev --prerelease allow slopscope
```

`cloc` is optional. The default `--engine auto` uses `cloc` when the binary is available on `PATH` and otherwise
uses the Python fallback. Use `--engine python` when you want deterministic physical-line behavior without relying
on a `cloc` binary. Use `--engine cloc` when you want the command to fail clearly if `cloc` is unavailable.

Rich is optional. Human-readable output defaults to `--format rich`, but the command falls back to plain output when
Rich is not installed. Use `--format plain` or `--format json` for dependency-free output.

## Standard Python Repository

Before:

```just
loc:
    uv run count-lines-of-code
```

After, compatibility mode:

```just
loc:
    uv run count-lines-of-code
```

The Justfile recipe can stay the same in compatibility mode. The migration is that `slopscope` provides the
`count-lines-of-code` console script, so the repository-local implementation and any local `count-lines-of-code`
entry point should be removed.

After, preferred command:

```just
loc:
    uv run slopscope
```

## Infrastructure Repository With YAML Totals

Before:

```just
yaml-lines:
    @rg --files -0 -g '*.yml' -g '*.yaml' . | xargs -0 wc -l | awk 'BEGIN {sum=0} !/^ *[0-9]+ total$/ {sum += $1} END {print sum}'
```

After:

```just
yaml-lines:
    uv run slopscope --profile yaml --total-only
```

Use a profile with `physical_lines = true` when compatibility with `wc -l` totals matters.

## Grouped YAML Report

Before:

```just
stats-roles:
    @for dir in roles/*/; do \
        role=$(basename "$dir"); \
        count=$(cloc --include-lang=YAML --csv --quiet "$dir" 2>/dev/null | tail -1 | cut -d',' -f5); \
        printf "%-35s %6s\n" "$role" "${count:-0}"; \
    done | sort -k2 -rn | head -20
```

After:

```just
stats-roles:
    uv run slopscope --profile roles
```

A profile such as `group_by = "roles/*"` displays groups as `roles/<name>`. Use `top = 20` in the profile or
`--top 20` on the command line to limit rendered rows.

## Multi-Project Workspace

Configured multi-project selection can replace workspace-level recipes that shell out to several project-local line
count commands.

```just
loc:
    uv run slopscope --project all

loc-frontend:
    uv run slopscope --project frontend

loc-backend:
    uv run slopscope --project backend
```

Use `optional = true` for projects that are not always checked out. Missing optional projects are reported on stderr
and included in JSON output as skipped projects. Missing required projects fail the command.

`--project all` runs configured projects in config order. Repeated named selections are also supported:

```just
loc-apps:
    uv run slopscope --project frontend --project backend
```

Project-specific profiles are not implemented yet; keep profile commands separate from workspace project reports.

## Validation

Run the existing recipe and the replacement once before deleting local implementation code. Compare broad behavior,
not exact numbers, unless the old and new commands use the same counting semantics.

Useful validation commands:

```bash
uv run slopscope --engine python --format plain
uv run slopscope --engine python --format json
uv run slopscope --profile yaml --total-only
uv run slopscope --profile roles --top 20
uv run slopscope --project all
```

Expected differences to document:

- `cloc` reports code lines, comments, and blanks according to `cloc` language rules.
- The Python fallback reports physical lines for mapped text-like files.
- Physical-line profiles intentionally preserve `wc -l`-style totals.
- Configured `exclude_dirs` are applied to fallback discovery and to parsed `cloc` file rows.

## Migration Checklist

1. Add `slopscope` to development dependencies.
2. Remove repository-local line-count implementation code.
3. Remove any local `count-lines-of-code` console-script entry point so it does not shadow the one from `slopscope`.
4. Keep the Justfile recipe invoking `count-lines-of-code` during the transition; `slopscope` now provides that
   command.
5. Add `[tool.slopscope]` configuration only for repo-specific behavior.
6. Run the old and new commands once and compare broad output shape.
7. Prefer documenting semantic differences, especially physical lines versus `cloc` code lines, instead of forcing
   exact equality where the old command was already inconsistent.

## Representative Migration Tracking Before 1.0

Phase 8 added synthetic migration fixture coverage for common repository shapes. That coverage is useful regression
evidence, but it does not complete the pre-1.0 requirement to migrate at least three representative repositories.

Track real, authorized migrations here using public-safe descriptions only:

- [x] Standard package repository: replaced a project-local line-count implementation with a `slopscope`-backed
  `just loc` recipe; added `[tool.slopscope]` excludes; validated the line-count command plus lint, typecheck, and
  tests; preserved `cloc` semantics when available; noted the expected total-count drop from removing the old local
  counter implementation; used a sibling checkout during the `slopscope` pre-release phase.
- [ ] Infrastructure or configuration repository: YAML total or grouped profile migration, validation command, and
  physical-line versus `cloc` semantics.
- [ ] Multi-project or generated-artifact repository: project configuration or excludes added, validation command,
  and skipped optional project behavior if applicable.

Do not record private repository names, private filesystem paths, private domains, internal hosts, or private
provenance. If a real migration cannot be described safely, summarize only the generic repository shape and keep the
private details outside this public repository.
