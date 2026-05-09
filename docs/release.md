# Release Workflow

This page captures the lightweight release workflow for `slopscope` until issue tracking and release automation are
set up.

## Pre-Release Metadata

The first pre-release version is `0.1.0a1`. The `0.1.0` base marks the first usable package line after the initial
implementation phases, and the `a1` suffix keeps the release clearly pre-1.0 and pre-stable.

Before publishing, confirm that:

- `pyproject.toml` and `src/slopscope/__init__.py` use the same version.
- `uv.lock` is in sync with the local project version.
- package classifiers still match the release maturity and supported Python versions.
- both console scripts remain present: `slopscope` and `count-lines-of-code`.
- runtime dependencies remain empty unless a release note documents why a dependency became required.
- project URLs are added only after public documentation, issue, and source URLs are known.

## Release Checks

Run the normal checks from a clean worktree:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run slopscope --help
uv run count-lines-of-code --help
uv run slopscope --format plain --engine python .
uv run slopscope --format json --engine python .
```

Build the package locally:

```bash
uv build
```

Inspect the generated artifacts before publishing:

```bash
uv run python -m zipfile -l dist/slopscope-*.whl
uv run python -m tarfile -l dist/slopscope-*.tar.gz
```

The wheel should include `slopscope` package modules and metadata, including both console entry points. The source
distribution should include the project metadata, README, license, docs, source package, tests, and lockfile.

Remove local build artifacts after validation unless the release policy changes:

```bash
rm -rf dist
```

## Publishing

Publishing is an external side effect. Do not publish until the release owner has confirmed:

- final package version
- target registry, such as TestPyPI or PyPI
- account and credentials
- whether the current changelog entries are ready to move from `Unreleased` to a versioned section

Once those are confirmed, a typical publish flow is:

```bash
uv build
uv publish dist/*
```

Use the appropriate `uv publish` registry options for non-default indexes. Keep generated `dist/` artifacts out of
source control unless a future policy explicitly requires checking them in.

## Changelog Steps

For each release:

1. Move relevant bullets from `Unreleased` into a versioned section such as `## 0.1.0a1 - 2026-05-09`.
2. Keep an empty `## Unreleased` section at the top for the next change.
3. Add compatibility notes for CLI, config, output, or migration behavior changes.
4. Mention skipped release work explicitly, such as pending real representative migrations or deferred publishing.
5. Tag the release only after checks pass and the package artifacts have been validated.

## Pre-1.0 Migration Evidence

Synthetic fixture coverage proves the supported migration shapes continue to work in tests. It does not replace
real representative migrations before `1.0`.

Before declaring the migration-readiness item complete, record at least three authorized, public-safe representative
migrations. Each record should describe only the repository shape, command replaced, configuration added, validation
run, and any semantic difference found. Do not include private repository names, private paths, private domains, or
internal provenance.
