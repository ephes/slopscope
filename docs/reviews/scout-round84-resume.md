# Scout round 84 resume

Parked bundle B17 on `fix/r84-external-results`; do not merge until the required review completes.

- `slopscope-churn-resolved-commit-snapshot`: reproduced on main `db561ad6fbc7a84b870a843a9e0bea9d68b21c4a` with a synthetic branch move. Both normal and unsupported-option retry tests failed before the fix. All history commands now use the resolved SHA; JSON adds `churn.commit` while retaining `churn.ref`.
- `slopscope-cloc-output-decoding-failure-contract`: reproduced on the same main with synthetic child output and an explicitly constrained ASCII decoder. Eight stdout/stderr, malformed-byte/non-ASCII-path, report/profile cases raised UnicodeDecodeError before the fix. They now return exit 2 without a report.
- README, composition documentation, renderer expectations, and Unreleased notes match the changes.
- Local validation: 737 tests passed on each of Python 3.11 and 3.13; Ruff lint/format and mypy passed on both. Wheel/sdist build and isolated installed-command smoke checks passed on Python 3.13. The ten new regression cases failed against original main before implementation.
- Required `claude-opus-5-5`, high-effort review through claude-review-loop failed before reviewing: `PROVIDER_ERROR`, "OAuth session expired and could not be refreshed". This is not a passing review. No findings were produced and no merge was made.

After restoring Claude authentication, fetch origin, rebase this branch on current origin/main, re-run local checks, then run a fresh whole-bundle Opus 5.5 high-effort review. Fix/re-review findings under the owner's four-round or recurring-finding stop rule. Merge only after a passing gate; fetch immediately before merging, rebase and re-test if main moved, merge with --no-ff, run full local checks again, push only main and verify with git ls-remote. Use [skip ci] in commit messages to avoid paid Actions. Do not open a PR, deploy, publish, tag, or delete remote branches.

Next, in report order, implement B18: `slopscope-source-open-binds-discovery-admission`, then `slopscope-composition-source-byte-admission`. Both remain unimplemented. Inspection still shows composition reopening discovered paths with tokenize.open and reading source without a byte ceiling. Re-verify on the then-current default branch and read both full descriptions and validation notes in scout-round84.md. Use portable synthetic fixtures, require regressions to fail on the old code, and update docs/Unreleased. No work under ~/projects, host access, or work-app writes.
