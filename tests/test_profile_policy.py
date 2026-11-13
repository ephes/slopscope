"""Effective profile policy is admitted before either engine starts work."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from slopscope import cli, config, profile


@pytest.mark.parametrize("engine", ["cloc", "python"])
@pytest.mark.parametrize(
    "global_fields,profile_fields",
    [
        ({"include_languages": ("Python",)}, {"exclude_languages": ("Python",)}),
        ({"exclude_languages": ("Python",)}, {"include_languages": ("Python",)}),
        ({}, {"group_by": "roles"}),
        ({}, {"group_by": "roles/*/*"}),
        ({}, {"group_by": "roles/**"}),
        ({}, {"group_by": "/roles/*"}),
        ({}, {"group_by": "../roles/*"}),
        ({}, {"group_by": "roles?/pkg/*"}),
    ],
)
def test_invalid_effective_profile_does_no_counting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    global_fields: dict[str, object],
    profile_fields: dict[str, object],
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid policy reached counting/discovery")

    monkeypatch.setattr("slopscope.cloc.run_file_summary", unexpected)
    monkeypatch.setattr("slopscope.fallback.build_file_rows", unexpected)
    selected = config.ProfileConfig(name="selected", **profile_fields)  # type: ignore[arg-type]
    settings = config.SlopscopeConfig(**global_fields)  # type: ignore[arg-type]
    with pytest.raises(profile.ProfileError, match="profile 'selected'"):
        profile.build_profile_report(
            path=tmp_path, slopscope_config=settings, selected_profile=selected, engine=engine
        )


def test_cli_inherited_conflict_is_one_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.slopscope]\ninclude_languages = ["Python"]\n'
        '[[tool.slopscope.profiles]]\nname = "selected"\nexclude_languages = ["Python"]\n',
        encoding="utf-8",
    )

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid policy reached subprocess or filesystem discovery")

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", unexpected)
    monkeypatch.setattr("slopscope.fallback.discover_filesystem_files", unexpected)
    monkeypatch.setattr("slopscope.cloc.run_file_summary", unexpected)
    out, err = io.StringIO(), io.StringIO()
    assert cli.run([str(tmp_path), "--profile", "selected"], stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "profile 'selected'" in err.getvalue()
    assert len(err.getvalue().splitlines()) == 1


def test_effective_overrides_and_empty_repository_stay_valid(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("one\ntwo\n", encoding="utf-8")
    settings = config.SlopscopeConfig(include_languages=("Python",), exclude_languages=("YAML",))
    selected = config.ProfileConfig(
        name="docs", include_languages=("Markdown",), exclude_languages=("Python",)
    )
    assert (
        profile.build_profile_report(
            path=tmp_path, slopscope_config=settings, selected_profile=selected, engine="python"
        ).total
        == 2
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    assert (
        profile.build_profile_report(
            path=empty, slopscope_config=settings, selected_profile=selected, engine="python"
        ).total
        == 0
    )
