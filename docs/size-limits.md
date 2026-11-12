# Size Limits

`slopscope --size-limits` holds oversized Python units at their current size. Functions, methods, and classes over a
limit must be recorded in an allowlist, where they may shrink but not grow; everything else must stay within its limit.
The check reuses the [composition report](composition.md) analyzer, so it needs no setup beyond a local checkout.
It also uses `[tool.slopscope.composition].max_source_bytes` (16 MiB by default). Source reads
validate discovery identities and refuse links below the root or nonregular replacements before
reading, using descriptor-relative no-follow and nonblocking OS APIs. If those APIs are unavailable
(for example on Windows, where size-limits is therefore unsupported), source analysis fails explicitly. An unreadable, changed, or oversized source prevents allowlist
seed/update publication; report mode lists failures and exits nonzero.

Git file discovery has a 30-second execution timeout plus bounded cleanup (up to 6 extra seconds on POSIX,
11 on Windows). A discovery timeout returns exit code 2 with the root path and no report, snapshot, or allowlist
write; partial output is discarded without filesystem fallback. Missing/nonzero Git retains filesystem fallback.
Windows process-tree termination is best-effort and untested. Filesystem traversal errors also return exit code 2 with a bounded path diagnostic,
preserving snapshots and allowlists. Deliberately excluded directories stay pruned.
See [README](../README.md) for discovery details.

```bash
slopscope --size-limits                    # report, exit 0
slopscope --size-limits --strict           # exit 1 when a unit is new over its limit or grew
slopscope --size-limits --seed-allowlist   # write the first allowlist from all current offenders
slopscope --size-limits --update-allowlist # lower recorded sizes, drop entries that are gone or fit
slopscope --size-limits --format json
slopscope --size-limits --project all
```

## Units And Limits

| Unit | Measured as | Default limit |
|---|---|---|
| Function or method | Span: physical lines from the first decorator to the end of the body | 150 |
| Class | Span, as above | 1500 |
| Test file | Code lines: physical lines that are not blank, comment, or docstring lines | 3000 |

Spans and code lines use the composition report's definitions, so the largest allowlist entries match its Largest
Functions and Largest Classes tables. Nested definitions count toward the units that enclose them and are also
checked on their own: a method's lines count toward its class, and a helper function inside a method is a unit too.

Every discovered Python file is checked, including functions and classes in test files and tooling scripts. Discovery,
excludes, and include globs are the same as for the composition report.

A test file is any file that the composition report classifies as tests: a file below a configured `test_dirs` entry
(default `tests`), or a file named `test_*`, `*_test.py`, `*.test.*`, or `*.spec.*`. Support modules and `conftest.py`
inside a test directory are therefore test files too and get the test-file limit, while a helper module outside the
test directories is not a test file unless its name matches one of those patterns.

## Unit Keys

Allowlist keys identify units by path, relative to the analyzed project root:

- Functions and classes: `path::Qualified.name`, for example `src/pkg/views.py::Panel.refresh`. Nested functions and
  classes are dotted (`Outer.method.helper`). A definition inside an `if`, `try`, `with`, or loop block keeps the
  enclosing prefix, as if the block were not there.
- Functions whose decorator resolves through imports to `typing.overload` or `typing_extensions.overload`
  are skipped, so the implementation keeps its plain name. Qualified imports and aliases are supported.
  Unrelated decorators named `overload`, decorator calls, and shadowed or ambiguous bindings count normally.
  Resolution is conservative within each scope: any assignment, deletion, parameter, or other rebinding of the
  imported name invalidates its exemption throughout that scope, regardless of statement order.
  Side effects from calls and rebinding from other scopes (including `global`/`nonlocal` assignments) are
  not propagated into the affected scope. Dynamic rebinding cannot be resolved statically.
- A name that repeats in the same file, such as a property getter and setter or two conditional definitions, gets
  `#2`, `#3`, and so on in source order: `Box.size`, `Box.size#2`.
- Test files: the path alone, for example `tests/test_views.py`.

Keys change when a unit is renamed or moved, so a renamed unit shows up as new over its limit plus a stale entry for
its old key. Moving the entry to the new key in the allowlist, with its recorded size, resolves both.

## Allowlist

The allowlist is a JSON file with one object per kind, mapping keys to recorded sizes. slopscope writes it with
sorted keys and a trailing newline, so diffs stay small:

```json
{
  "classes": {
    "src/pkg/views.py::Panel": 1830
  },
  "functions": {
    "src/pkg/views.py::Panel.refresh": 212
  },
  "test_files": {
    "tests/test_views.py": 3410
  }
}
```

The default path is `size-limits.json` in the analyzed project root. Configure another path in
`[tool.slopscope.size_limits]` (relative to the project root, or absolute), or pass `--allowlist PATH` for one run.
Without an allowlist file, nothing is allowlisted. An invalid file (not JSON, unknown sections, sizes that are not
positive integers, duplicate object keys at any nesting level, or decoder recursion exhaustion from deeply nested JSON) fails with a concise `slopscope:`
error and exit code 2 before any allowlist write. Duplicate keys are refused even when their values
agree, with a bounded key diagnostic and object member index. Existing allowlist bytes are preserved.
Allowlist inputs must be regular files (symlinks to regular files are supported), at most 16 MiB
(16,777,216 bytes). The opened descriptor is checked before a bounded read; special files such as
named pipes are refused without waiting for a writer.

## Report

The report compares every measured unit with its limit and the allowlist, and lists four groups:

- **New over limit:** units over their limit that the allowlist does not contain.
- **Grown past allowlist:** allowlisted units now larger than their recorded size, with the delta.
- **Shrunk below allowlist:** allowlisted units now smaller than their recorded size but still over the limit.
  `--update-allowlist` records the smaller size.
- **Stale:** allowlist entries whose unit is missing (gone, renamed, or moved) or now within its limit.

The report exits 0. With `--strict` it exits 1 when the first or second group is not empty. Files that cannot be read
or parsed are listed as failures, their allowlist entries are neither checked nor reported as stale, and the command
exits 1.

## Updating And Seeding

- `--seed-allowlist` writes a new allowlist with every unit that is currently over its limit, at its current size. It
  refuses to overwrite an existing file.
- `--update-allowlist` lowers recorded sizes to current sizes for units that shrank, and drops entries that are
  missing or now within the limit. It never adds an entry and never raises a recorded size, so growth and new
  offenders still show up afterwards. It needs an existing allowlist.
- Both refuse to run while files fail to parse, because unmeasured units would look missing.
- With `--project`, every selected project is measured and validated before any allowlist is written, so a refusal
  in one project leaves all of them unchanged. Seeding or updating is refused when two projects resolve to the same
  allowlist file.

Seed and update hold an OS lock from the allowlist read through planning and atomic publication.
Concurrent updates re-read the latest baseline before measuring, so a later update cannot restore a
larger size; concurrent seeds admit only the first publisher. Multi-project mutations acquire all
locks in sorted resolved-target order before planning any project. Report mode remains read-only.
Locks use persistent sibling files named `.<allowlist-name>.lock`, created with mode 0600; do not
delete these files while mutations may be running. Mutations can create these files and their parent
directories even if planning later refuses the operation (for example, updating a missing allowlist).
Add `.size-limits.json.lock` to your project's `.gitignore` when using the default allowlist path. The OS releases a lock when its process exits,
including interruption. Lock acquisition failures return exit code 2. On Windows, the OS lock call
retries for about 10 seconds before failing if the target remains busy. These advisory locks
coordinate slopscope mutations on filesystems supporting OS locks; manual writers must coordinate
separately. Multi-project publication is still atomic per file, not a multi-file transaction.

## Projects

With `--project NAME` or `--project all`, each configured project is checked on its own, against its own allowlist at
the configured path relative to that project's root. `--allowlist` cannot be combined with `--project`. Missing
optional projects are skipped as in the other reports.

## Configuration

```toml
[tool.slopscope.size_limits]
max_function_lines = 150
max_class_lines = 1500
max_test_file_code_lines = 3000
allowlist = "size-limits.json"
```

All fields are optional. Unknown fields and invalid values fail with a `slopscope:` error and exit code 2. `0.2.0a1`
and earlier reject this section as an unknown field; use `0.3.0a1` or later.

## JSON Shape

```json
{
  "report_type": "size_limits",
  "schema_version": 1,
  "path": ".",
  "action": "report",
  "limits": {"functions": 150, "classes": 1500, "test_files": 3000},
  "allowlist": {"path": "size-limits.json", "existed": true,
                "entries": {"functions": 1, "classes": 1, "test_files": 1}},
  "units": {"functions": 420, "classes": 60, "test_files": 25},
  "check_failed": true,
  "over_limit": [{"kind": "functions", "key": "src/pkg/io.py::load", "size": 171, "limit": 150,
                  "recorded": null, "delta": null, "reason": null}],
  "grown": [],
  "shrunk": [],
  "stale": [{"kind": "functions", "key": "src/pkg/old.py::parse", "size": null, "limit": 150,
             "recorded": 190, "delta": null, "reason": "missing"}],
  "seeded": [],
  "lowered": [],
  "dropped": [],
  "failures": []
}
```

- `action` is `report`, `seed`, or `update`. `seeded`, `lowered`, and `dropped` list what the action changed.
- `units` counts every measured unit, not only offenders. `allowlist.entries` counts entries after the action.
- `reason` is `missing` or `within limit` for stale and dropped entries.
- With `--project`, the top level has `report_type: "size_limits_projects"`, `schema_version`, a `projects` array
  whose items contain `name`, `path`, and a full `report` object, and `skipped_projects`.

Allowlist writes use a flushed and fsynced sibling temporary file before atomic replacement.
Failed writes leave the previous baseline intact and remove the temporary file. Existing file
permissions are preserved, and symlinks continue to point at the updated target. New allowlists use
mode 0600 to keep temporary and final contents private by default; use chmod if other local users
need access. Cleanup is best effort if the filesystem also refuses temporary-file deletion.

## Rendering checks

Plain and Rich reports preserve literal unit paths and diagnostics, including bracketed filenames.
Rich uses explicit table styles rather than interpreting labels as markup. Without Rich, output
falls back to plain text.

`tests/test_size_limits_render.py` checks all seven entry groups in JSON and the report/seed/update
human tables, with distinct recorded/current sizes, signed deltas, missing sizes and reasons, and
failure-only reports. Run it with `uv run --extra rich pytest tests/test_size_limits_render.py`
to exercise actual Rich rendering as well as plain text and JSON.
