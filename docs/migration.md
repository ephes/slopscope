# Migration Guide

This guide describes how repositories should replace local line-count logic with `slopscope`.

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

Multi-project selection is planned but not implemented yet. The configuration can be added and validated now, while
the commands below remain future behavior.

```just
loc:
    uv run slopscope --project all

loc-frontend:
    uv run slopscope --project frontend

loc-backend:
    uv run slopscope --project backend
```

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
