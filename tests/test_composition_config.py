from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pytest

from slopscope import cli, composition, config
from slopscope.composition_semantics import SemanticSettings


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = cli.run(argv, stdout=stdout, stderr=stderr)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


def test_composition_section_is_parsed() -> None:
    parsed = config.parse_config_mapping(
        {
            "composition": {
                "limit": 5,
                "min_duplicate_tokens": 30,
                "qt_modules": ["app.qt"],
                "logging_patterns": ["audit_log"],
                "compat_markers": ["deprecated"],
                "churn_branch": "trunk",
                "churn_months": 6,
            }
        }
    )

    assert parsed.composition == config.CompositionConfig(
        limit=5,
        min_duplicate_tokens=30,
        qt_modules=("app.qt",),
        logging_patterns=("audit_log",),
        compat_markers=("deprecated",),
        churn_branch="trunk",
        churn_months=6,
    )


def test_missing_composition_section_uses_defaults() -> None:
    assert config.parse_config_mapping({}).composition == config.CompositionConfig()


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ("not a table", "composition must be a table"),
        ({"unknown": 1}, "unknown [tool.slopscope.composition] field: unknown"),
        ({"limit": 0}, "composition.limit must be a positive integer"),
        ({"min_duplicate_tokens": True}, "composition.min_duplicate_tokens must be"),
        ({"qt_modules": "PySide6"}, "qt_modules must be an array of strings"),
        ({"logging_patterns": [""]}, "logging_patterns[0] must be a non-empty string"),
        ({"churn_branch": ""}, "composition.churn_branch must be a non-empty string"),
        ({"churn_months": -1}, "composition.churn_months must be a positive integer"),
    ],
)
def test_invalid_composition_section_is_rejected(section: object, message: str) -> None:
    with pytest.raises(config.ConfigError, match=None) as exc_info:
        config.parse_config_mapping({"composition": section})

    assert message in str(exc_info.value)


def test_empty_compat_markers_disable_the_marker() -> None:
    parsed = config.parse_config_mapping({"composition": {"compat_markers": []}})

    assert parsed.composition.compat_markers == ()
    analysis = composition.analyze_source(
        "legacy_value = fallback()\n",
        settings=SemanticSettings.configured(compat_markers=()),
    )
    assert analysis.counts.marker("compat") == 0


def test_configured_limit_and_min_tokens_apply_and_cli_limit_wins(tmp_path: Path) -> None:
    for index in range(4):
        write(tmp_path, f"src/m{index}.py", f"def f{index}():\n    return {index}\n")
    write(
        tmp_path,
        "pyproject.toml",
        """
        [tool.slopscope.composition]
        limit = 2
        min_duplicate_tokens = 7
        """,
    )

    _code, stdout, _err = run_cli(["--composition", "--format", "json", str(tmp_path)])
    data = json.loads(stdout)
    _code, overridden, _err = run_cli(
        ["--composition", "--limit", "3", "--format", "json", str(tmp_path)]
    )

    assert data["settings"]["limit"] == 2
    assert data["settings"]["min_duplicate_tokens"] == 7
    assert len(data["largest_modules"]) == 2
    assert len(json.loads(overridden)["largest_modules"]) == 3


def test_configured_qt_modules_include_wrapper_packages() -> None:
    source = """
        import app.qt
        from app.qt import widgets
        from app.qt.core import Signal

        label = widgets.Label()
        other = app.qt.Button()
        changed = Signal()
        unrelated = app.models.Row()
        """
    text = textwrap.dedent(source).lstrip("\n")

    default = composition.analyze_source(text)
    configured = composition.analyze_source(
        text, settings=SemanticSettings.configured(qt_modules=["app.qt"])
    )

    assert default.counts.tag("qt") == 0
    # The three imports and the three statements that use app.qt names.
    assert configured.counts.tag("qt") == 6


def test_logging_patterns_are_opt_in_substrings() -> None:
    source = "audit_log.write('x')\nrecord_audit_log('y')\nprint('z')\n"

    default = composition.analyze_source(source)
    configured = composition.analyze_source(
        source, settings=SemanticSettings.configured(logging_patterns=["audit_log"])
    )

    assert default.counts.tag("logging") == 0
    assert configured.counts.tag("logging") == 2


def test_settings_appear_in_json(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "x = 1\n")
    write(
        tmp_path,
        "pyproject.toml",
        """
        [tool.slopscope.composition]
        qt_modules = ["app.qt"]
        logging_patterns = ["audit"]
        compat_markers = ["old"]
        """,
    )

    _code, stdout, _err = run_cli(["--composition", "--format", "json", str(tmp_path)])
    settings = json.loads(stdout)["settings"]

    assert settings["qt_modules"] == ["PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "app.qt"]
    assert settings["logging_patterns"] == ["audit"]
    assert settings["compat_markers"] == ["old"]


def test_baseline_with_different_counting_settings_is_not_comparable(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "x = 1\n")
    snapshot = tmp_path / "before.json"
    run_cli(["--composition", "--snapshot", str(snapshot), str(tmp_path)])
    write(tmp_path, "pyproject.toml", "[tool.slopscope.composition]\nmin_duplicate_tokens = 20\n")

    _code, stdout, stderr = run_cli(
        ["--composition", "--baseline", str(snapshot), "--format", "json", str(tmp_path)]
    )

    assert json.loads(stdout)["baseline"]["comparable"] is False
    assert "setting min_duplicate_tokens differs: baseline 50, current 20" in stderr


def test_changing_only_the_limit_keeps_the_baseline_comparable(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "x = 1\n")
    snapshot = tmp_path / "before.json"
    run_cli(["--composition", "--snapshot", str(snapshot), str(tmp_path)])

    _code, stdout, stderr = run_cli(
        [
            "--composition",
            "--limit",
            "3",
            "--baseline",
            str(snapshot),
            "--format",
            "json",
            str(tmp_path),
        ]
    )

    assert json.loads(stdout)["baseline"]["comparable"] is True
    assert stderr == ""


def test_churn_months_has_an_upper_bound() -> None:
    with pytest.raises(config.ConfigError, match="churn_months must be at most 1200"):
        config.parse_config_mapping({"composition": {"churn_months": 1201}})
