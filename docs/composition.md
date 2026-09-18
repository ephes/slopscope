# Composition Report

The default report answers "how many lines". The composition report answers "lines of what": it puts every physical
line of every discovered Python file into exactly one structural category, reports statement counts that do not depend
on formatting, and lists the largest modules, classes, and functions.

```bash
slopscope --composition
slopscope --composition path/to/repository
slopscope --composition --format plain
slopscope --composition --format json > composition.json
slopscope --composition --limit 25
slopscope --composition --project all
slopscope --composition --config path/to/pyproject.toml
```

The mode is opt-in, Python-only, and uses only the standard library `ast` and `tokenize` modules. It does not change
the default report, profiles, or any existing JSON shape. `count-lines-of-code --composition` behaves the same.

## Options

- `--composition` selects the composition report.
- `--limit N` sets the number of rows in the largest modules, classes, and functions lists. The default is 10. `--limit`
  is a usage error without `--composition`.
- `PATH`, `--config PATH`, `--project NAME` (repeatable, or `all`), `--format rich|plain|json`, and `--no-color` work
  as they do for the default report.
- `--engine`, `--profile`, `--total-only`, and `--top` cannot be combined with `--composition`; the command fails with
  a usage error and exit code 2 instead of silently ignoring them.

## Discovered Files

The composition report uses the same discovery as the Python engine: `git ls-files` inside a Git repository, a
filesystem walk otherwise, the default excludes for caches, virtual environments, dependency and build directories,
plus configured `exclude_dirs` and `include_globs`. Of the discovered files, those that map to `Python` (`.py` and
`.pyi`) are analyzed, each exactly once, in sorted path order.

- Untracked files are not analyzed inside a Git repository, as in the default report.
- Tracked paths that are missing from the working tree, or that are not regular files, are skipped, as in the default
  report.
- `include_languages` and `exclude_languages` do not apply: the report is Python-only by definition.
- Files are classified as `source`, `tests`, or `other` and into areas with the same `source_dirs`, `test_dirs`, and
  `areas` rules as the default report.

Each file is opened with `tokenize.open()`, which honors PEP 263 encoding declarations and a UTF-8 BOM. `\r\n` and
`\r` line endings are read as `\n`.

## Failures

A file that cannot be read (I/O error), decoded (bad bytes or an unknown encoding declaration), tokenized, or parsed
(syntax error) is reported as a failure. Failures:

- are printed to stderr as `slopscope: could not analyze PATH: ERROR`, so stdout stays valid JSON;
- are listed in the report (a `Failures` section in plain and Rich output, `failures` in JSON);
- are excluded from all counts, while every other file is still analyzed;
- make the command exit with code 1 after the report is written.

`ast` accepts only syntax that the running interpreter supports. The report records the Python version it used, so a
file that uses newer syntax than that interpreter fails with a syntax error rather than being dropped.

## Line Categories

Every physical line gets exactly one of these categories, so the categories of a file add up to its physical line
count, and report totals add up to the sum over files.

| Category | Lines |
|---|---|
| `blank` | Whitespace-only lines outside string literals. |
| `comment` | Lines whose only token is a comment. A code line with an inline comment stays code. |
| `docstring` | The first statement of a module, class, or function body when it is a plain string literal, over all of its lines. |
| `import` | `import` and `from ... import` statements. |
| `definition` | `def`, `async def`, and `class` header lines: decorators, multi-line signatures, bases, and type parameters. |
| `assertion` | `assert` statements. |
| `error_handling` | `raise` statements and `except`/`except*` header lines. |
| `literal_data` | Assignment, annotated assignment, augmented assignment, `return`, and expression statements whose value is a dict, list, tuple, set, `str`, or `bytes` literal spanning at least 2 physical lines. |
| `other_code` | Every other line: other statements, compound statement headers, and clause lines. |

`blank`, `comment`, and `docstring` are non-code lines. `code` is the physical line count minus those three.

### Counting Rules

- Blank and comment-only lines are assigned first, including those inside brackets of a multi-line statement.
- Statements are then visited from the outside in and in source order. A line keeps the first category it gets, so a
  line shared by several statements or clauses belongs to the leftmost one: `def f(): return 1` is a `definition`
  line, `import os; x = 1` is an `import` line, and `except ValueError: pass` is an `error_handling` line.
- All lines of a statement belong to it, including closing brackets and backslash continuation lines.
- Lines inside a multi-line string that is not a docstring belong to the statement containing the string, even when
  they are empty or start with `#`.
- A docstring must be a plain string (`bytes` and f-strings are never docstrings). A string statement anywhere else is
  a normal expression statement: `literal_data` when it spans at least 2 lines, `other_code` otherwise.
- A literal's element contents are not inspected: a multi-line list of calls is still `literal_data`. A multi-line call
  that has a literal argument is not.
- Handler bodies keep their own structural category: an `import` inside `except ImportError:` is an `import` line.
- Clause lines take the category of their compound statement, which is `other_code`: `try:`, `else:`, `finally:`,
  `elif ...:`, `case ...:`, and the header of `if`, `for`, `while`, `with`, `match`, including async variants. The
  exception is `except`/`except*` headers, which are `error_handling`.
- An empty file has no lines. A file without a final newline still counts its last line.

## Statements And Continuation Lines

- `statements` is the number of `ast.stmt` nodes. It measures size independently of formatting. Docstrings are
  expression statements and count. `elif` counts as a nested `if` statement because that is how `ast` represents it.
  `except` handlers and `case` clauses are not statements.
- `continuation_lines` counts code lines on which no statement, decorator, or clause (`except`, `else`, `finally`,
  `case`) begins. This measures formatter layout, such as one argument per line, but it also counts the inner lines of
  multi-line literals, multi-line strings that are not docstrings, and closing brackets. Read it as "code lines that
  are not the first line of something", not as pure formatter inflation.
- `code_per_statement` is code lines divided by statements.

## Largest Items

- Largest modules are ranked by code lines.
- Largest classes and functions are ranked by span: physical lines from the first decorator to the last line of the
  body, including blank lines, comments, and docstrings.
- Names are qualified with the file path and enclosing definitions: `src/pkg/module.py:Outer.Inner.method`. Nested
  function names use plain dots, without `<locals>`. The first line of each definition is reported to tell apart
  definitions with the same qualified name, such as a property getter and setter.
- Spans of nested definitions overlap: a method's lines also count toward its class, and an inner class's lines count
  toward its outer class.
- A class's `methods` count only includes `def` and `async def` statements directly in the class body, not ones nested
  inside `if` blocks or inner classes.
- Rankings cover all analyzed files, with the file's source/test kind shown next to each row. Ties sort by path, line,
  and name.

## Output

Plain and Rich output contain these sections:

- Line Categories: every category with total, share of physical lines, and source/tests/other columns.
- Statements: files, physical, code, statements, code per statement, and continuation lines for source, tests, other,
  and total.
- Areas: the same columns per repository area.
- Largest Modules, Largest Classes, and Largest Functions.
- Failures, when any file failed.

Rich is optional. Without Rich, or with `--no-color`, the plain renderer is used.

## JSON Shape

`--format json` writes a distinct top-level shape, separate from the repository and profile JSON. Redirect it to a
file to keep a snapshot.

```json
{
  "report_type": "composition",
  "schema_version": 1,
  "analyzer": {"name": "slopscope.composition", "version": 1, "python_version": "3.13.1"},
  "path": ".",
  "settings": {
    "language": "Python",
    "limit": 10,
    "excluded_paths": [".git", ".venv", "build", "dist"],
    "include_globs": [],
    "source_dirs": ["src"],
    "test_dirs": ["tests"],
    "areas": ["scripts", "examples", "specs"]
  },
  "categories": ["blank", "comment", "docstring", "import", "definition", "assertion", "error_handling",
                 "literal_data", "other_code"],
  "total": {
    "name": "total",
    "files": 2,
    "physical": 40,
    "code": 31,
    "statements": 17,
    "continuation_lines": 12,
    "code_per_statement": 1.82,
    "categories": {"blank": 6, "comment": 1, "docstring": 2, "import": 3, "definition": 5, "assertion": 0,
                   "error_handling": 1, "literal_data": 4, "other_code": 18}
  },
  "kinds": [{"name": "source", "files": 1, "...": "same fields as total"}],
  "areas": [{"name": "src", "files": 1, "...": "same fields as total"}],
  "largest_modules": [{"path": "src/pkg/app.py", "kind": "source", "code": 25, "physical": 32, "statements": 14}],
  "largest_classes": [{"path": "src/pkg/app.py", "name": "App", "qualified_name": "src/pkg/app.py:App", "line": 8,
                       "lines": 20, "methods": 3, "kind": "source"}],
  "largest_functions": [{"path": "src/pkg/app.py", "name": "App.run", "qualified_name": "src/pkg/app.py:App.run",
                         "line": 12, "lines": 9, "kind": "source"}],
  "files": [{"path": "src/pkg/app.py", "kind": "source", "area": "src", "...": "same count fields as total"}],
  "failures": [{"path": "src/pkg/broken.py", "error": "syntax error: invalid syntax (line 3)"}]
}
```

- Every category is present in every `categories` object, even when its count is zero.
- `kinds` always contains `source`, `tests`, and `other`, in that order. `areas` contains the areas that have files,
  sorted by code lines, files, then name.
- `code_per_statement` is rounded to two decimals and is `null` without statements.
- `schema_version` changes when the JSON shape changes. `analyzer.version` changes when the counting rules change, so
  numbers from different analyzer versions should not be compared directly.

With `--project`, the top level has `report_type: "composition_projects"`, `schema_version`, `analyzer`, a `projects`
array whose items contain `name`, `path`, and a full `report` object as above, and `skipped_projects` for missing
optional projects.

## Known Limits

- Categories are structural only. They do not say whether code is framework glue, logging, or compatibility code;
  semantic tags are planned as a separate, overlapping dimension.
- Parsing is limited to the syntax supported by the running interpreter.
- Files are analyzed sequentially.
- There is no configuration section for the composition report yet; defaults are used for everything not listed under
  [Discovered Files](#discovered-files).
- Snapshots are plain JSON redirects; there is no baseline comparison yet.
