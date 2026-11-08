# Round 85 B27 — resume after review authentication repair

Status: implemented and locally validated; **not reviewed and not authorized for merge yet**.
The required Claude Opus 5.5 high-effort review failed before reviewing code:
`Failed to authenticate: OAuth session expired and could not be refreshed`.
The harness returned `PROVIDER_ERROR`, not a passing verdict. No findings were produced.
The feature branch is parked at the owner's explicit instruction; main remains unchanged.

Bundle, in report order:

1. `slopscope-allowlist-publish-target-binding`: locked resolved destination and parent identity
   carried into planning/publication, opened descriptor checked, destination changes refused.
2. `slopscope-multiproject-allowlist-error-compensation`: stage all outputs and prior bytes/modes,
   restore earlier replacements on handled errors, retain named backups if restoration fails.

Base: `origin/main` at `b1f0e25fb532d53e06d1aff2880ab02d6574d779`.
Branch: `fix/r85-allowlist-publication`.
Source report: `~/workspaces/ws-work-planning/reviews/scout-round85.md`, slices 73–74, B27.
Both issues were still present on that default-branch tip; neither was skipped.
README, size-limits documentation and Unreleased notes have been updated. Process death and
power loss are explicitly outside the handled-error compensation guarantee.

Local validation on Python 3.13.16 with locked dependencies:

- `just check`: **756 passed**, Ruff lint and format checks passed, mypy passed (51 files).
- `uv build`: wheel and sdist built; isolated installed-command smoke tests passed for both.
- 19 new synthetic CLI regression cases fail against the original implementation and pass with
  the fix. Existing separate-process spawn tests still pass for writer serialization and locks.
- No Actions, deployments, tags, releases or PRs were run or created. Parking commit includes
  `[skip ci]` to prevent the feature-branch push from running Actions.

Resume:

1. Repair Claude Code OAuth authentication outside this worktree. Do not substitute another
   model/provider or waive the review gate.
2. Fetch origin, re-verify main and rebase this branch if it moved. Rerun local checks after
   rebasing. Use `UV_CACHE_DIR` under the owning workspace's `.cache` if the default is blocked.
3. Review the committed bundle against the base (a plain HEAD worktree review would be empty):

   ```sh
   python3 ~/projects/agent-stuff/codex/skills/claude-review-loop/bin/claude-review-loop \
     --repo "$PWD" --run-dir ../review-resume-1 \
     --baseline-ref b1f0e25fb532d53e06d1aff2880ab02d6574d779 \
     --model claude-opus-5-5 --effort high --record-baseline \
     --slice-id r85-slopscope-b27 \
     --ledger-dir ../.cache/review-ledger --lock-dir ../.cache/review-locks
   ```

   Use a fresh run directory and caller-authored context. Follow the required skill's repair
   delta review process; stop at four valid rounds or a recurring Critical/Warning.
4. Only after a clean or suggestion-only verdict with dispositions, fetch again immediately
   before merging. If origin moved, rebase and rerun checks. Merge locally with `--no-ff` into
   main, run full local checks and package smoke checks again, push only main and verify its SHA
   with `git ls-remote`. Include `[skip ci]` in commits and merge messages. Do not run Actions.
