from __future__ import annotations

from pathlib import Path

import pytest

from slopscope import cloc, config, fallback, profile
from slopscope.report import FileRow, GroupedProfileReport, GroupedRow, ProfileTotalReport


def disable_git_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "slopscope.fallback.run_git_ls_files",
        lambda _path: fallback.GitLsFilesResult(returncode=128, stdout=b""),
    )


def test_find_profile_returns_configured_profile() -> None:
    selected = config.ProfileConfig(name="yaml")
    slopscope_config = config.SlopscopeConfig(profiles=(selected,))

    assert profile.find_profile(slopscope_config, "yaml") is selected


def test_find_profile_rejects_missing_profile() -> None:
    with pytest.raises(profile.ProfileError, match="no configured profile named 'yaml'"):
        profile.find_profile(config.SlopscopeConfig(), "yaml")


def test_physical_yaml_profile_counts_yml_and_yaml_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    (tmp_path / "a.yml").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("one\n\nthree\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("one\n", encoding="utf-8")
    selected = config.ProfileConfig(
        name="yaml",
        include_languages=("YAML",),
        include_globs=("*.yml", "*.yaml"),
        physical_lines=True,
    )

    report = profile.build_profile_report(
        path=tmp_path,
        slopscope_config=config.SlopscopeConfig(profiles=(selected,)),
        selected_profile=selected,
        engine="cloc",
    )

    assert report == ProfileTotalReport(
        profile="yaml",
        engine="python",
        path=tmp_path,
        total=5,
        physical_lines=True,
    )


def test_profile_language_filters_affect_fallback_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    (tmp_path / "a.yml").write_text("one\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("one\ntwo\n", encoding="utf-8")
    selected = config.ProfileConfig(name="docs", exclude_languages=("YAML",), physical_lines=True)

    report = profile.build_profile_report(
        path=tmp_path,
        slopscope_config=config.SlopscopeConfig(profiles=(selected,)),
        selected_profile=selected,
        engine="python",
    )

    assert isinstance(report, ProfileTotalReport)
    assert report.total == 2


def test_profile_include_globs_affect_fallback_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "main.yml").write_text("one\n", encoding="utf-8")
    (tmp_path / "root.yml").write_text("one\ntwo\n", encoding="utf-8")
    selected = config.ProfileConfig(
        name="roles",
        include_languages=("YAML",),
        include_globs=("roles/*.yml",),
        physical_lines=True,
    )

    report = profile.build_profile_report(
        path=tmp_path,
        slopscope_config=config.SlopscopeConfig(profiles=(selected,)),
        selected_profile=selected,
        engine="python",
    )

    assert isinstance(report, ProfileTotalReport)
    assert report.total == 1


def test_profile_top_level_exclude_dirs_still_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "skip.yml").write_text("one\n", encoding="utf-8")
    (tmp_path / "keep.yml").write_text("one\ntwo\n", encoding="utf-8")
    selected = config.ProfileConfig(name="yaml", include_languages=("YAML",), physical_lines=True)

    report = profile.build_profile_report(
        path=tmp_path,
        slopscope_config=config.SlopscopeConfig(exclude_dirs=("generated",), profiles=(selected,)),
        selected_profile=selected,
        engine="python",
    )

    assert isinstance(report, ProfileTotalReport)
    assert report.total == 2


def test_cloc_profile_total_uses_file_code_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_file_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout=(
                "language,filename,blank,comment,code\n"
                "YAML,playbook.yml,1,2,7\n"
                "YAML,roles/web/tasks.yml,0,1,11\n"
                "Markdown,README.md,0,0,5\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("slopscope.profile.cloc.run_file_summary", fake_run_file_summary)
    selected = config.ProfileConfig(
        name="yaml",
        include_languages=("YAML",),
        include_globs=("*.yml", "roles/**/*.yml"),
    )

    report = profile.build_profile_report(
        path=tmp_path,
        slopscope_config=config.SlopscopeConfig(profiles=(selected,)),
        selected_profile=selected,
        engine="cloc",
    )

    assert report == ProfileTotalReport(
        profile="yaml",
        engine="cloc",
        path=tmp_path,
        total=18,
        physical_lines=False,
    )


def test_cloc_grouped_profile_normalizes_absolute_file_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_file_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout=(
                "language,filename,blank,comment,code\n"
                f"YAML,{tmp_path / 'roles/web/tasks.yml'},0,1,11\n"
                f"YAML,{tmp_path / 'roles/db/tasks.yml'},0,1,7\n"
                f"YAML,{tmp_path / 'playbook.yml'},0,1,20\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("slopscope.profile.cloc.run_file_summary", fake_run_file_summary)
    selected = config.ProfileConfig(
        name="roles",
        include_languages=("YAML",),
        group_by="roles/*",
    )

    report = profile.build_profile_report(
        path=tmp_path,
        slopscope_config=config.SlopscopeConfig(profiles=(selected,)),
        selected_profile=selected,
        engine="cloc",
    )

    assert report == GroupedProfileReport(
        profile="roles",
        engine="cloc",
        path=tmp_path,
        group_by="roles/*",
        rows=(
            GroupedRow(name="roles/web", files=1, code=11),
            GroupedRow(name="roles/db", files=1, code=7),
        ),
        total=18,
        top=None,
        physical_lines=False,
    )


def test_cloc_profile_failure_is_preserved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "slopscope.profile.cloc.run_file_summary",
        lambda _path: cloc.ClocResult(returncode=7, stdout="", stderr="cloc exploded\n"),
    )
    selected = config.ProfileConfig(name="yaml", include_languages=("YAML",))

    with pytest.raises(profile.ProfileCountError) as exc_info:
        profile.build_profile_report(
            path=tmp_path,
            slopscope_config=config.SlopscopeConfig(profiles=(selected,)),
            selected_profile=selected,
            engine="cloc",
        )

    assert str(exc_info.value) == "cloc exploded"
    assert exc_info.value.returncode == 7


def test_grouped_profile_sorts_and_limits_rows() -> None:
    selected = config.ProfileConfig(name="roles", group_by="roles/*", top=2)

    report = profile._build_grouped_report(
        path=Path("."),
        selected_profile=selected,
        engine="python",
        file_rows=(
            FileRow(language="YAML", path="roles/api/tasks.yml", blank=0, comment=0, code=5),
            FileRow(language="YAML", path="roles/web/tasks.yml", blank=0, comment=0, code=8),
            FileRow(language="YAML", path="roles/web/meta.yml", blank=0, comment=0, code=2),
            FileRow(language="YAML", path="roles/db/tasks.yml", blank=0, comment=0, code=5),
            FileRow(language="YAML", path="playbook.yml", blank=0, comment=0, code=20),
        ),
        top=None,
    )

    assert report == GroupedProfileReport(
        profile="roles",
        engine="python",
        path=Path("."),
        group_by="roles/*",
        rows=(
            GroupedRow(name="roles/web", files=2, code=10),
            GroupedRow(name="roles/api", files=1, code=5),
            GroupedRow(name="roles/db", files=1, code=5),
        ),
        total=20,
        top=None,
        physical_lines=False,
    )


def test_grouped_profile_total_is_all_matched_rows_not_visible_top() -> None:
    selected = config.ProfileConfig(name="roles", group_by="roles/*", top=1)

    report = profile._build_grouped_report(
        path=Path("."),
        selected_profile=selected,
        engine="python",
        file_rows=(
            FileRow(language="YAML", path="roles/web/tasks.yml", blank=0, comment=0, code=8),
            FileRow(language="YAML", path="roles/db/tasks.yml", blank=0, comment=0, code=5),
            FileRow(language="YAML", path="playbook.yml", blank=0, comment=0, code=20),
        ),
        top=1,
    )

    assert report.rows == (GroupedRow(name="roles/web", files=1, code=8),)
    assert report.total == 13


def test_invalid_group_by_pattern_is_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("slopscope.profile.fallback.build_file_rows", lambda *_args, **_kwargs: [])
    selected = config.ProfileConfig(name="bad", group_by="roles")

    with pytest.raises(profile.ProfileError, match="exactly one"):
        profile.build_profile_report(
            path=tmp_path,
            slopscope_config=config.SlopscopeConfig(profiles=(selected,)),
            selected_profile=selected,
            engine="python",
        )
