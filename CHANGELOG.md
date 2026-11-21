# Changelog

All notable changes to `slopscope` will be documented here.

Release workflow: keep new changes under `Unreleased`. At release time, move the relevant bullets into a dated
version section such as `## 0.1.0a1 - 2026-05-09`, leave a fresh `Unreleased` section for the next cycle, and note
any CLI, configuration, output, migration, or publishing compatibility details.

## Unreleased

- Compute source/test, area, and directory totals in one pass without retaining the input file rows.

- Normalize configured paths once per file aggregation call and reuse them across report sections.

- Reuse the list of copies compared against the reference while extending each duplicate group.

- Validate the duplicate-window threshold for direct library calls. A one-token window is supported.

- Distinguish a missing Git executable from other process startup failures in churn reports.
  Startup diagnostics include a short OS error code for failures such as permission errors.

- Reuse precompiled metric paths during composition baseline validation and format field diagnostics
  only for invalid metrics or groups.

- Write size-limit measurements directly from source-ordered definitions, avoiding temporary tuple lists
  and preserving allowlist keys and overload filtering.

- Mark composition baseline comparisons with retired detectors as non-comparable and still report deltas.

- Reject incomplete composition baseline reads before JSON decoding, even when the file metadata is stable.

- Require a JSON integer for the composition baseline schema version. Float, boolean, and string markers
  return exit code 2.

- Compile the `group_by` pattern once per grouped profile report and reuse it for all file rows.

- Release ancestor directory descriptors during source opening for composition and size-limits,
  keeping directory descriptor use constant for deep paths.

- Bound allowlist publication staging to 128 MiB across serialized outputs and recovery bytes.
  Oversized mutations fail before staging or replacing any allowlist.

- Share one 10-second deadline across all allowlist writer locks, releasing earlier locks on timeout
  before planning or publication. A concurrent seed/update now fails with exit code 2 instead of
  waiting when another writer holds a lock for longer than that.

- Size-limits skips composition's semantic detectors while retaining definition spans,
  overload exclusion, and test-file code-line totals.
- Size-limits associates allowlist entries with failed source paths containing `::`,
  preventing false stale-entry reports.
- Size-limits no longer builds duplicate-detection token segments it never reads, so the composition
  `max_total_tokens` and `max_total_anchors` budgets no longer apply to it. Since 0.4.0a1 they did, and
  a project with more than 2,000,000 code tokens (the default) could no longer run `--size-limits` at
  all ("incomplete analysis: whole-size-limits token limit exceeded"). The cumulative
  `max_total_source_bytes` budget still bounds size-limits. No CLI, configuration, or JSON changes.
- Composition budgets now fit large repositories: the default `max_total_tokens` rises from 2,000,000
  to 8,000,000, the default `max_total_anchors` from 500,000 to 2,000,000, and the fixed duplicate
  work budget from 10,000,000 to 100,000,000 units. A Python project of about 3.6 million code
  tokens needs about 575,000 anchors and 27.5 million work units, so the 0.4.0a1 defaults stopped it
  with "incomplete analysis: whole-composition token limit exceeded"; it now runs in about 250 MB.
  Explicitly configured budgets are unchanged; the JSON `settings` show the new defaults.

## 0.4.0a2 - 2026-11-17

Compatibility notes: no CLI, configuration, or JSON schema changes and no new runtime dependencies. Git now refuses a
configured project path that is an embedded bare repository, so reports for such a path change: the Python engine
falls back to filesystem discovery, while `--engine cloc` (and `auto` when cloc is installed) fails that project with
"cloc returned no usable file rows" and exit code 1; use `--engine python` for it.

- Security: Git commands and cloc now run with `safe.bareRepository=explicit`, `core.fsmonitor=false`,
  `log.showSignature=false` (appended through `GIT_CONFIG_COUNT` and, for Git older than 2.31,
  `GIT_CONFIG_PARAMETERS`) and `GIT_NO_LAZY_FETCH=1`. Before, a cloned repository that tracked a directory
  laid out as a bare repository, named as a configured project, could make `slopscope --project all` run an
  arbitrary command through that directory's `core.fsmonitor`; a `.git/config` from an untrusted archive could
  do the same, and `--churn` could run its `gpg.program`. A configured path that is such an embedded bare
  repository now uses filesystem discovery (Python engine). No CLI, configuration, or JSON changes.

## 0.4.0a1 - 2026-11-17

Compatibility notes: report modes and JSON schema versions are unchanged; churn output gains `churn.commit`.
`[tool.slopscope.composition]` accepts four new budget keys (`max_source_bytes`, `max_total_source_bytes`,
`max_total_tokens`, `max_total_anchors`, defaults 16 MiB, 128 MiB, 2,000,000 and 500,000); `0.3.0a1` and earlier
reject them as unknown fields, so repositories that set them need `0.4.0a1` or later. Semantic, placement, Qt,
logging, and data-shape detectors and the analyzer metadata advance their versions, so comparisons with older
baselines warn as non-comparable. `--composition`, `--size-limits`, and allowlist seeding/updating now need POSIX
descriptor-relative, no-follow opening (and `flock` for snapshot publication), so they are unsupported on Windows,
where every source is reported as an incomplete-analysis failure; the default report and profiles still run there.

Several inputs that earlier versions reported on now fail with a diagnostic and exit code 2 instead of partial results
or tracebacks: physical-line reports (`--engine python`, `auto` without cloc, `physical_lines = true` profiles) over
16 MiB per file or 128 MiB in total; composition and size-limits runs over the configurable composition budgets
(by default 16 MiB per Python source); allowlists and composition baselines over 16 MiB or malformed;
cloc and Git discovery output over 16 MiB or running longer than 30 seconds; malformed cloc CSV; discovery and
duplicate-analysis work beyond their ceilings; and configuration files that are not UTF-8. Churn is different: a
churn Git timeout, oversized Git log output, or malformed history skips churn with a notice and keeps the report's
exit code. Source symlinks and linked directories are skipped during discovery. No new runtime dependencies are
required.

- Resolve definition headers and nested lambda defaults once in their lexical environment,
  separating binding inventory from expression traversal. Resolver visits now grow linearly
  with nested-default syntax while preserving default child scopes, walrus bindings, and
  class/lambda/comprehension boundaries. Counting rules and version metadata are unchanged.

- Invalidate nested logger identities when their receiver or an ancestor attribute is
  assigned, deleted, or otherwise rebound (import, nested definition, `except`/`match` capture,
  or `global`/`nonlocal` declaration), including shared evidence across direct methods.
  Preserve intact direct and sibling fields. Logging detector metadata advances to version 4;
  older logging baselines warn as non-comparable. Package and JSON schema versions are unchanged.

- Preserve runtime imports and logger aliases across module/class annotations without values;
  function annotations still shadow locals and valued annotations still invalidate aliases.
- Share logger fields by instance receiver identity across direct undecorated methods, including
  differently named and positional-only receivers. Conflicting writes, rebound receivers,
  decorated methods, and unrelated classes remain excluded from shared evidence.

- Precollect every comprehension iteration target before resolving later iterables, filters,
  and results; keep the first iterable in its enclosing scope and targets confined to the child.
- Class-local imports and logger aliases no longer bind lambda bodies or comprehension parts
  after the first iterable inside a class body, matching Python's class-scope rules.
- Respect explicit generic type parameters in annotation and definition-body scopes, including
  generic class bases and type aliases, without shadowing outer decorators/function defaults.
  Newer syntax is feature-detected; Python 3.11 remains supported.

- Include enclosing-scope writes from definition headers (defaults, decorators, class bases)
  in semantic and overload resolution, while keeping lambda-body writes local to the lambda.

- Inventory only direct signature parameters, excluding child lambdas/comprehensions in defaults,
  for semantic tags, fixture placement, and typing overload exemptions. Semantic and placement
  detectors advance to version 3 and analyzer metadata to version 4: older baselines warn as
  non-comparable. Package and JSON schema versions are unchanged.

- Resolve fixture decorators and test bases through lexical bindings. Rebound names and child
  imports no longer create false fixtures/tests; local unittest inheritance refuses ambiguous
  or modified class names. Test-placement detector version 2 warns on older baselines; pytest
  naming rules and JSON schema version 4 are unchanged.

- Preserve typing overload exemptions after list/set/dict/generator comprehensions whose
  iteration targets shadow the decorator only inside the comprehension. Real enclosing-scope
  assignments and deletions remain conservative; analyzer metadata advances to version 3
  (package and JSON schema versions are unchanged).

- Unwind partial capture-reader startup: close unclaimed pipes once, let started readers
  close their own pipes, and terminate/reap the child with bounded cleanup. Reader startup
  failures now produce a fixed capture diagnostic and CLI exit 2 instead of a traceback;
  interruptions and supervisor SIGTERM retain their exit behavior.

- Bound macOS/Linux capture-group inspection by the cleanup deadline and a 65,536-entry
  ceiling, streaming Linux `/proc` with bounded stat reads. Incomplete listings remain
  unknown evidence, preserving final signalling before reap and refusing unconfirmed cleanup.
  The macOS kqueue post-exit presence check uses its own short budget, so an exit observed
  just inside the capture timeout is not misread as reaped elsewhere.

- Handle supervisor SIGTERM during POSIX main-thread Git/cloc capture through bounded
  owned-tree cleanup, then exit 143. Restore the prior handler on all outcomes and keep
  repeated TERM from interrupting cleanup; worker-thread captures and an ignored SIGTERM
  leave signal policy alone.

- Keep captured POSIX command leaders unreaped through the final process-group termination
  attempt using public waitid/WNOWAIT or kqueue observation. After reap, only harmless absence probes
  are allowed; post-reap cleanup errors never trigger signals against a reusable identity.
  On macOS and Linux the TERM grace ends once only the exited leader remains, so captures
  without lingering descendants do not pay the fixed 0.2-second wait. A leader already reaped
  elsewhere (inherited SIGCHLD=SIG_IGN) yields a bounded capture error instead of an invented
  status 0, after retiring positively listed surviving group members (never signalling an
  observed released ID otherwise); other exit-observation failures are bounded capture errors.

- Escape nonprinting terminal controls in plain/Rich reports and CLI diagnostics, including
  paths, project/profile labels, measured units, and errors. JSON and stored identities remain
  exact; printable Unicode and literal Rich brackets remain readable.

- Synchronize allowlist recovery, publication, restoration, parent creation, and backup-retirement
  directory entries. Post-replacement sync failures report uncertain durability and retain named
  recovery evidence without claiming rollback.

- Refuse allowlist evidence changed in place during bounded reading, including update planning
  and recovery backups, by checking opened-file size, mtime, and ctime.

- Stream cloc CSV records under a fixed 100,000-record ceiling independent of captured bytes;
  overflow refuses complete reports/profiles before constructing excess domain rows.
- Build every normal cloc report section, including language headlines and recomputed SUM,
  from one validated file-summary capture instead of two potentially inconsistent invocations.

- Reject ambiguous cloc CSV headers before consuming records, retaining individual files/filename
  summary aliases and metadata columns.
- Validate normalized cloc file identities before filtering/aggregation in reports and profiles;
  duplicate aliases and paths outside the selected root refuse the complete report. Only parent
  directories are resolved, so file symlinks keep their own path.

- Serialize snapshot writers with an admitted persistent sibling kernel lock; stale preflight
  generations cannot overwrite a newer publication. Requires POSIX flock and no-follow opening.
- Synchronize the snapshot parent directory after replacement; report visible publication with
  unconfirmed durability separately from failures that preserve the prior snapshot.

- Charge duplicate anchor window copying/hashing before the work, under the existing work ceiling.
- Refuse baselines changed in place during their bounded read, while retaining atomic-replacement reads.

- Apply shared composition source-byte, token, and anchor budgets to size-limits measurement,
  configurable through the existing composition settings. Exhaustion returns exit code 2
  without a partial report or allowlist write; exact-limit measurements are unchanged.
- Inspect composition/size-limits discovery stat errors explicitly: missing tracked paths
  (also below a parent replaced by a file) and looping symlinks remain skipped, while permission and I/O errors refuse incomplete analysis and preserve outputs.

- Retire owned POSIX process groups after successful and nonzero capture wrapper exits,
  including redirected descendants, with bounded TERM/KILL confirmation (transient EPERM
  on signal delivery or probing is retried within the deadline). Unconfirmed cleanup
  refuses the result; confirmed cleanup preserves the wrapper's output and return code.
- Normalize allowlist integer-decoder resource failures to bounded, value-free diagnostics in
  every size-limit mode, preserving the refused allowlist bytes.

- Snapshot classification policy iterables at public aggregate boundaries, keeping generator
  policies consistent across every row and report section without buffering standalone rows.
- Normalize baseline integer-decoder resource errors to bounded diagnostics, including integers
  in extension fields; refused baselines preserve prior snapshots.
- Resolve Qt, logging, and data-shape names conservatively by lexical binding scope; exclude
  shadowed/ambiguous names and keep logger instance fields within their own class. These three
  detectors advance to version 2, so comparisons with old counts warn as non-comparable.
- Refuse symlinked, hardlinked, and replaced allowlist lock entries through no-follow opening,
  single-link regular-file admission, and descriptor/name identity checks after waiting.
  Allowlist mutation (`--seed-allowlist`, `--update-allowlist`) now requires `O_NOFOLLOW` and
  therefore no longer runs on Windows; size-limits source analysis was already unsupported there.
- Bound expanded duplicate metadata and traversal work across each project. Exhaustion refuses
  partial reports/snapshots with exit code 2; complete duplicate output is unchanged.
- Compensate multi-project allowlist publication on catchable interruptions as well as ordinary
  errors, including interruptions immediately after rename. Preserve the primary failure and
  retain named recovery backups if compensation fails; process death is outside the guarantee.

- Count physical lines in bounded binary chunks with the existing UTF-8/ignored-error and
  universal-newline semantics; enforce 16 MiB per source and 128 MiB per physical report.
  Compatibility: these physical-line limits and the filesystem discovery limits below are fixed,
  not configurable. A python-engine report (including `auto` without cloc) or a
  `physical_lines = true` profile over them, for example with a large data fixture or minified
  bundle, now exits 2 without output where it previously reported. Exclude such paths or use the
  cloc engine. Limit diagnostics quote and truncate paths.
- Bound composition source bytes, shared code tokens/interner, and potential duplicate anchors
  across the entire repository/project. Configure positive `max_total_source_bytes`,
  `max_total_tokens`, and `max_total_anchors`; overflow refuses partial reports/snapshots and
  preserves prior artifacts. Exact-limit inputs retain existing counting and duplicate detection.

- Analyze working-tree bytes once per exact Git path, including unresolved index stages;
  retain first discovery order and distinct case/Unicode spellings.
- Bound filesystem fallback entry, file, directory, and depth work before counting or publication;
  refuse excess trees with an incomplete-analysis error and preserve snapshots/allowlists.

- Admit serialized allowlists and composition snapshots against their readers' 16 MiB UTF-8
  ceilings before staging; an oversized multi-project allowlist batch preserves every prior
  artifact. Exact-limit payloads remain readable and accepted.

- Validate selected profiles after language-filter inheritance and reject malformed grouping
  patterns before engine selection or counting, with a bounded profile-specific diagnostic.

- Pass absolute root operands to cloc, protecting option-like directory names and keeping
  grouped profile identities consistent for relative and absolute roots.

- Bind allowlist mutation reads and publication to the locked resolved target and parent identity;
  refuse symlink retargets and destination changes, including mismatched opened file descriptors.
- Stage all project allowlists before publication and compensate earlier replacements on handled
  errors, preserving prior bytes, modes, or absence. Failed compensation retains named recovery
  backups for every allowlist that was or may have been replaced and not yet restored;
  process death and power loss remain outside this
  guarantee. Destination rechecks are best-effort: a swap racing the final path-based rename is not
  prevented.

- Bind composition and size-limit source reads to discovery identities using no-follow directory
  descriptors and nonblocking regular-file opens; changed sources make analysis incomplete and
  preserve snapshots and allowlists. Platforms without the required OS APIs refuse source analysis;
  this makes `--composition` and `--size-limits` unsupported on Windows.
- Limit each Python source to 16 MiB before decoding or parsing, with an opened-file size check
  and bounded byte read; configure the positive ceiling with `composition.max_source_bytes`.
  Preserve PEP 263 encodings and newline normalization at the exact limit.

- Pin churn history and older-Git retries to the verified commit; record its SHA as `churn.commit`
  while preserving the requested ref label.
- Convert cloc stdout/stderr locale decoding failures into bounded exit-code-2 diagnostics
  without publishing a report or replacement-decoding filenames.

- Reject duplicate JSON object keys in allowlists and composition baselines at every nesting level,
  with bounded positional diagnostics and preservation of refused inputs and requested outputs.
- Bind snapshot publication to the destination approved during input preflight; recheck destination
  and parent identity before replacement, refusing changed targets and symlink retargets.

- Bound cloc and Git file discovery to 16 MiB of combined stdout/stderr with 64 KiB reads and
  owned process-tree cleanup, sharing the existing churn capture implementation. Overflow returns
  exit code 2 without partial reports, snapshots, allowlist writes, or discovery fallback.

- Limit duplicate-extension overlap work to positions within each segment and expand nested subgroups
  without Python recursion, preserving block order and intervals. Detector resource exhaustion marks
  composition analysis incomplete and prevents snapshot publication.

- Render size-limit and composition labels and diagnostics literally in Rich tables, including
  bracketed filenames, while retaining program-supplied styles and the dependency-free fallback.
- Protect size-limit state columns, unknown baseline deltas and warnings, duplicate location
  truncation, and signed monthly churn with focused synthetic renderer regressions.

- Validate churn history framing, month markers, counters, and rename operands; malformed history
  skips the whole optional churn result instead of silently publishing partial or zero totals.
- Bound each churn Git command to 16 MiB of combined stdout/stderr, with 64 KiB reads and bounded
  process-group/tree and reader cleanup on overflow. Source analysis and snapshot policy are unchanged.

- Fail filesystem discovery visibly on traversal errors across Python reports, profiles, composition,
  and size-limits, preserving existing outputs and deliberate directory pruning.
- Reject malformed cloc CSV records and invalid nonnegative counters with a bounded diagnostic and
  exit code 2 instead of publishing partial totals or exposing parser exceptions.

- Resolve size-limit overload exemptions through scoped `typing`/`typing_extensions` imports, including aliases.
  Custom, shadowed, and ambiguous decorators count normally. Analyzer metadata advances to version 2; the
  composition JSON schema remains version 4 and structural line counts are unchanged.
- Report token/semantic traversal recursion and memory exhaustion as per-file analysis failures, preserving
  snapshots and allowlists while continuing independent files.
- Limit composition baseline input to regular files of at most 16 MiB, validating the opened descriptor and
  bounding the read before JSON decoding. Regular-file symlinks remain supported; refusals preserve outputs.
- Build and smoke both distributions on Python 3.11 and 3.13 in CI, checking both installed commands without
  Rich or source-tree imports against a synthetic fixture.

- Serialize allowlist seed/update reads, planning, and publication with OS-held per-target locks, including
  multi-project runs. Concurrent updates cannot restore a larger baseline; concurrent seeds publish once.
  Report mode stays read-only. Mutation locks leave persistent private sibling `.lock` files.

- Limit allowlist input to regular files of at most 16 MiB, validating the opened descriptor before a bounded
  read. Named pipes are refused without waiting for a writer; regular-file symlinks remain supported.

- Publish composition snapshots only after complete analysis. Read, decode, tokenization, or parse failures in
  any selected project preserve the previous snapshot (or leave a new destination absent), while the partial
  report and diagnostics remain available with exit code 1.

- Refuse composition snapshot destinations that alias selected Python files, the loaded configuration, or the
  baseline, including symlink and hardlink aliases, with exit code 2 before analysis. Distinct snapshots inside
  the repository remain supported; refreshing a baseline now requires a separate snapshot destination.

- Reject excessively nested composition baseline JSON with a concise filename diagnostic and exit code 2,
  preserving the baseline and any requested snapshot output.

- Fail Python reports and physical-line profiles with exit code 2 and a file diagnostic when an included
  mapped regular file exists but cannot be read at counting time, rather than silently reporting incomplete
  totals. Missing tracked paths (deletions, sparse checkouts) and non-regular paths are still skipped.

- Bound Git file discovery to a 30-second timeout plus bounded process-tree cleanup
  (up to 6 extra seconds on POSIX, 11 on Windows; Windows tree termination is best-effort and untested). On timeout, return exit code 2 with a diagnostic
  and no report, snapshot, allowlist write, or filesystem fallback; discard partial output. Git startup failures
  and nonzero exits continue to use filesystem discovery.

- Report invalid UTF-8 configuration files with a filename diagnostic and exit code 2 instead of a traceback.
- Bound each cloc invocation to 30 seconds; timeout and startup failures return a diagnostic and exit code 2
  without producing a report or falling back from auto mode. Terminate the process group on POSIX and
  bound post-timeout cleanup; Windows process-tree termination is best-effort and untested.

- Report excessively nested allowlist JSON as a concise domain error without modifying the allowlist.
- Skip source symlink files and linked directories in Git and filesystem discovery, including contained links.
  Python reports, physical-line profiles, composition, and size-limits exclude these links from their totals.
  The cloc engine and allowlist-output symlink handling are unchanged.

- Bound each churn Git command to 30 seconds and terminate its process tree on expiry.
  Bound post-termination pipe draining to one second and abandon busy pipes without blocking cleanup.
  Preserve KeyboardInterrupt and SystemExit raised during post-timeout pipe draining.
  Windows process-tree termination is best-effort and untested on Windows.
  Skip optional churn without failing the composition report or retrying a timed-out log.
- Write composition snapshots atomically, preserving existing bytes and permissions on failure.
  Symlinked snapshots update their target; new snapshots use mode 0600.
  Reject special-file destinations; atomic publication requires a replaceable file in a writable directory.

- Preserve embedded newlines, commas, tabs, and boundary whitespace when parsing cloc CSV filenames.
  Subprocess text decoding still normalizes carriage returns.
- Apply configured `include_globs` to default cloc reports, rebuilding language totals from selected files
  consistently with source/test, area, and directory summaries.

- Reject malformed composition baseline identities, duplicate names, and invalid comparison counts
  with field-specific diagnostics and exit code 2 instead of crashing or silently collapsing entries.

- Reject missing or non-directory repository roots with exit code 2 in every CLI report mode.
  Configured project paths that point at files also fail, including optional projects.

- Write size allowlists atomically, preserving the previous baseline and permissions on failure.
  Symlinked allowlists update their target. Newly seeded files use mode 0600 to keep temporary and final
  contents private by default; use chmod to share a new baseline with other local users.

- Add a GitHub Actions CI workflow that runs the `just check` steps (pytest, ruff check, ruff format check, mypy) on
  Python 3.11 and 3.13 with the locked dependencies. Actions are pinned by commit SHA, the uv cache is enabled, the
  workflow token is read-only, and no secrets are used.

## 0.3.0a1 - 2026-09-18

Compatibility notes: the default report, profiles, multi-project reports, the composition report, and their JSON shapes
are unchanged. `--size-limits` is a new, opt-in mode with its own JSON shape (`report_type: "size_limits"`,
`schema_version` 1). The new `[tool.slopscope.size_limits]` configuration section is rejected by `0.2.0a1` and earlier,
so repositories that add it need `0.3.0a1` or later. No new runtime dependencies are required.

- Add `slopscope --size-limits`, a local check that holds oversized units at their current size. Functions and
  methods (default 150 span lines), classes (1,500 span lines), and test files (3,000 code lines) are measured with
  the composition report's span and code-line definitions. Units over a limit must be recorded in a JSON allowlist
  keyed as `path::Qualified.name`, where they may shrink but not grow. The report lists new offenders, grown and
  shrunk allowlisted units, and stale entries; `--strict` exits 1 on new or grown offenders; `--seed-allowlist` writes
  the first allowlist and refuses to overwrite one; `--update-allowlist` lowers and drops entries but never adds or
  raises one. Configure limits and the allowlist path in `[tool.slopscope.size_limits]`; with `--project`, each
  project has its own allowlist. JSON output uses `report_type: "size_limits"` with `schema_version: 1`.
- Split duplicate-block assembly out of the composition report builder, which the new check flagged as a function
  over 150 span lines. Composition output is unchanged.

## 0.2.0a1 - 2026-09-18

Compatibility notes: the default report, profiles, multi-project reports, and their JSON shapes are unchanged. The
composition report is opt-in through `--composition` and has its own JSON shape (`schema_version` 4). The new
`[tool.slopscope.composition]` configuration section is rejected by `0.1.0a1`, so repositories that add it need
`0.2.0a1` or later. `--help` lists new options and shows `--engine`'s `auto` default. `--churn` runs `git log` only
when requested; no new runtime dependencies are required.

- Add a `rich` optional extra (`slopscope[rich]`) so downstream projects without Rich as a runtime dependency can opt
  into colored Rich tables; without the extra, the default `--format rich` continues to fall back to plain output.
- Update post-release documentation now that `0.1.0a1` is published on the package index and GitHub.
- Add an opt-in Python composition report, `slopscope --composition [PATH]`. It assigns every physical line of every
  discovered Python file one structural category (`blank`, `comment`, `docstring`, `import`, `definition`,
  `assertion`, `error_handling`, `literal_data`, `other_code`) using the standard library `ast` and `tokenize`
  modules, and reports statement counts, continuation lines, code lines per statement, per source/test and area
  totals, and the largest modules, classes, and functions. It supports `--config`, `--project`, `--format`, and
  `--no-color`; `--limit N` sets the largest-item list size. Read, decode, and parse failures are reported on stderr
  and exit with code 1. JSON output uses a new, separate shape with `report_type: "composition"` (or
  `"composition_projects"` with `--project`); its first released `schema_version` is 4, because the shape changed
  while the report was developed.
- Reject `--engine`, `--profile`, `--total-only`, and `--top` together with `--composition`, and `--limit`,
  `--snapshot`, `--baseline`, and `--churn` without it, with argparse usage errors. The default report, profiles, and existing JSON shapes are unchanged; `--help` now
  lists the new options and shows `--engine`'s `auto` default in its help text.
- Add semantic tags to the composition report as a separate, overlapping dimension over code lines: `qt` for names
  bound from PySide/PyQt/qtpy imports, `logging` for the `logging` module and loggers assigned from
  `logging.getLogger()`, and `data_shape` for fields of dataclass, attrs, enum, NamedTuple, TypedDict, and pydantic
  classes, all resolved through imports. Add a `compat` wording marker, construct counts, and test-file placement into
  `test`, `fixture`, `setup`, `module_level`, and `helper_or_unknown` with conservative pytest and unittest rules. JSON
  output lists every detector with its version.
- Add token-based duplicate detection to the composition report: exact token windows anchored at line starts are
  verified token by token, extended while every copy agrees, and merged into per-file duplicated code lines and a list
  of the largest duplicate blocks with all their copies.
- Add `--snapshot PATH` to write the composition JSON to a file and `--baseline PATH` to compare with an earlier
  snapshot. Baselines with a different report type or schema version are rejected; analyzer or detector version
  differences are reported as warnings.
- Add a `[tool.slopscope.composition]` configuration section with `limit`, `min_duplicate_tokens`, extra `qt_modules`,
  opt-in `logging_patterns`, `compat_markers`, `churn_branch`, and `churn_months`. Baselines report differing counting
  settings as warnings.
- Add `--churn` to the composition report: Python lines added and removed per month on the configured branch's
  first-parent history, limited to the analyzed path, following renames, and skipped with a notice outside Git.
- Amend the "deep language parsing" non-goal to allow the opt-in, Python-only composition report.

## 0.1.0a1 - 2026-05-10

- Prepare first pre-release package metadata with version `0.1.0a1`, MIT license text, alpha classifiers, and
  synchronized package version metadata.
- Add the initial installable Python package scaffold with `src/slopscope`.
- Add `slopscope` and `count-lines-of-code` console scripts.
- Implement the first `cloc` language-summary slice with CSV parsing and clear unavailable-engine errors.
- Add an internal language-summary report model used by the CLI rendering path.
- Add internal `cloc --by-file --csv --quiet` command support and file-row CSV parsing.
- Add internal pure-Python fallback file discovery using Git file lists or filesystem traversal.
- Implement pure-Python fallback language summaries with default excludes, filename and suffix language mapping,
  physical-line counting, and explicit physical-line output labeling.
- Add internal source/test, area, and directory classification plus deterministic aggregation over file-level rows.
- Add pure-Python fallback file-level rows using mapped languages and physical line counts.
- Add default report rendering for language, source/test, area, and directory sections with `--format rich|plain|json`
  and `--no-color`.
- Add `[tool.slopscope]` configuration loading from `pyproject.toml` and `--config PATH`.
- Apply configured excludes, language filters, fallback include globs, source/test dirs, named areas, and nested
  directory buckets to the default single-repository report.
- Parse and validate named projects, optional projects, and named profiles for later execution phases.
- Execute configured named profiles with `--profile NAME`.
- Add `--total-only` for profile totals, including YAML physical-line totals compatible with `wc -l`-style recipes.
- Add grouped profile reports for patterns such as `roles/*`, with configured `top` values and `--top N` overrides.
- Add profile JSON output for total and grouped reports.
- Execute configured projects with `--project NAME`, repeatable `--project`, and `--project all`.
- Add multi-project plain, Rich, and JSON reports with project snapshots, per-project default reports, and skipped
  optional projects.
- Skip missing optional project paths with a concise stderr notice and fail missing required project paths clearly.
- Accept `cloc` language summary CSV files that use `files` instead of `filename` for the file-count column.
- Add synthetic migration fixture coverage for standard Python, Django-style, infrastructure YAML, grouped YAML,
  multi-project, and desktop-style repository layouts.
- Add pytest, Ruff, mypy, and `just` developer commands.
- Add Rich to the development dependency group and add a `just loc` dogfood recipe for the local repository report.
- Start public project documentation and initial product requirements.
- Clarify migration, configuration, and roadmap docs after initial review.
- Add installation and release workflow documentation for first pre-release readiness.
- Expand the migration guide with prerequisites, validation commands, semantic-difference notes, and representative
  migration tracking before `1.0`.
- Record the first public-safe representative migration tracking entry, including the expected count difference from
  removing a project-local counter implementation.
- Complete the public-safe representative migration tracker for pre-`1.0` with real package, raw shell recipe, and
  multi-project workspace migrations.
- Record small-tooling pre-release consumption evidence for the sibling-checkout `just loc` recipe without counting it
  as a replacement migration.
- Normalize absolute `cloc --by-file` paths before classification so configured project reports keep source/test and
  directory buckets relative to each selected project.
