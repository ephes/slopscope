# Round 84 source-admission resume

Parked on `fix/r84-source-admission`; nothing from this bundle was merged.
Base: `main` at `b1f0e25fb532d53e06d1aff2880ab02d6574d779`.

Report order and current status:

1. `slopscope-churn-resolved-commit-snapshot`: already fixed on main; skipped.
2. `slopscope-source-open-binds-discovery-admission`: implemented with discovery identity
   admission, descriptor-relative no-follow directory traversal, and nonblocking regular-file reads.
3. `slopscope-composition-source-byte-admission`: implemented with configurable positive
   `composition.max_source_bytes` (16 MiB default), opened-file size check, bounded byte read,
   PEP 263 decoding and newline normalization. Size-limits inherits the same ceiling.
4. `slopscope-cloc-output-decoding-failure-contract`: already fixed on main; skipped.

The remaining two slices share one bundle. README, composition/configuration/size-limit docs,
and Unreleased changelog are synchronized. Source failures preserve snapshots and allowlists.
The selected root may be a symlink; links below it are refused. Platforms without
descriptor-relative open, O_NOFOLLOW, O_DIRECTORY, or O_NONBLOCK fail source analysis explicitly.
Default Python physical-line reports, profiles, and cloc source reading are unchanged.

Validation completed locally on Python 3.11.13 and 3.13.16:

- 759 tests passed on each interpreter, including the 10 existing regressions for the skipped slices.
- All 22 new admission regressions fail against the original main production files and pass with
  the fixes. Synthetic fixtures cover leaf/parent/regular replacements, an injected nonregular
  descriptor, output preservation, exact-limit encoded sources, and growth after the size check.
- Ruff lint, Ruff format check, and strict mypy pass.
- Wheel and sdist builds plus isolated installed-command smoke checks pass on each interpreter.

Review attempted using claude-review-loop, `--model claude-opus-5-5 --effort high`.
It exited 2, state `PROVIDER_ERROR`: OAuth session expired and could not be refreshed.
There was no completed review or verdict, no findings, and no reviewer tool calls.
The review copy was removed; there were no skipped files, truncations, or redactions.
Local evidence is in the workspace's `.cache/review-round1/result.json` and `events.jsonl`;
check logs and the reusable local-check script are alongside that directory.

Resume after restoring the installed reviewer's authentication. Run a fresh whole-bundle review
of the implementation commit relative to the base above (the harness normally reviews uncommitted
changes, so prepare the corresponding uncommitted delta in a fresh local checkout). Drop this
parking note before merging. Continue repairing and re-reviewing while progress is made, at most
four rounds, and stop if the same required finding returns. No merge without a passing review.
Fetch immediately before merging; if origin moved, rebase onto current main and rerun local checks.
After a clean review, commit, merge locally with `--no-ff`, rerun all local checks, push only main,
and verify its SHA with `git ls-remote`. Use `[skip ci]`; do not start or wait for GitHub Actions.

No releases, tags, deployments, host access, pull requests, work-app writes, or Actions were run.
This branch push is solely the owner's explicit exception for parking work awaiting review.
