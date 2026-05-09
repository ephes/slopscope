from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO

import pytest

from slopscope import render
from slopscope.report import (
    AreaRow,
    DirectoryRow,
    GroupedProfileReport,
    GroupedRow,
    LanguageRow,
    MultiProjectReport,
    ProfileTotalReport,
    ProjectReport,
    ProjectSnapshotRow,
    RepositoryReport,
    SkippedProject,
    SourceTestSummary,
)


def sample_report(*, engine: str = "cloc") -> RepositoryReport:
    return RepositoryReport(
        engine=engine,
        path=Path("."),
        language_rows=(
            LanguageRow(language="Python", files=2, blank=1, comment=2, code=30),
            LanguageRow(language="Markdown", files=1, blank=0, comment=0, code=5),
            LanguageRow(language="SUM", files=3, blank=1, comment=2, code=35),
        ),
        source_test_summary=SourceTestSummary(
            source_files=1,
            source_code=20,
            test_files=1,
            test_code=10,
        ),
        area_rows=(
            AreaRow(name="src", files=1, code=20),
            AreaRow(name="tests", files=1, code=10),
            AreaRow(name="docs", files=1, code=5),
        ),
        directory_rows=(
            DirectoryRow(name="src/slopscope", files=1, code=20),
            DirectoryRow(name="tests", files=1, code=10),
            DirectoryRow(name=".", files=1, code=5),
        ),
    )


def test_plain_renderer_includes_all_default_sections() -> None:
    output = render.render_plain(sample_report())

    assert "Slopscope Report" in output
    assert "Language Summary" in output
    assert "Source vs Tests" in output
    assert "Repository Areas" in output
    assert "Directory Buckets" in output


def test_plain_renderer_keeps_language_row_content() -> None:
    output = render.render_plain(sample_report())

    assert "Python               2        1        2       30" in output
    assert "SUM                  3        1        2       35" in output


def test_plain_renderer_identifies_python_physical_line_semantics() -> None:
    output = render.render_plain(sample_report(engine="python"))

    assert "Engine: python (physical lines)" in output


def test_json_renderer_emits_stable_report_data() -> None:
    data = json.loads(render.render_json(sample_report()))

    assert data["engine"] == "cloc"
    assert data["path"] == "."
    assert data["language_rows"][0] == {
        "language": "Python",
        "files": 2,
        "blank": 1,
        "comment": 2,
        "code": 30,
    }
    assert data["source_test_summary"] == {
        "source_files": 1,
        "source_code": 20,
        "test_files": 1,
        "test_code": 10,
    }
    assert data["area_rows"][0] == {"name": "src", "files": 1, "code": 20}
    assert data["directory_rows"][0] == {
        "name": "src/slopscope",
        "files": 1,
        "code": 20,
    }


def test_multi_project_plain_renderer_includes_snapshot_and_project_reports() -> None:
    output = render.render_plain(
        MultiProjectReport(
            engine="python",
            projects=(ProjectReport(name="frontend", report=sample_report(engine="python")),),
            snapshot_rows=(
                ProjectSnapshotRow(
                    name="frontend",
                    path=Path("frontend"),
                    engine="python",
                    files=3,
                    code=35,
                    source_code=20,
                    test_code=10,
                ),
            ),
            skipped_projects=(
                SkippedProject(
                    name="docs",
                    path=Path("docs"),
                    reason="missing optional project path",
                ),
            ),
        )
    )

    assert "Slopscope Projects" in output
    assert "Project Snapshot" in output
    assert "frontend" in output
    assert "Skipped Projects" in output
    assert "Project: frontend" in output
    assert "Slopscope Report" in output


def test_multi_project_json_renderer_emits_stable_report_data() -> None:
    output = render.render_json(
        MultiProjectReport(
            engine="python",
            projects=(ProjectReport(name="frontend", report=sample_report(engine="python")),),
            snapshot_rows=(
                ProjectSnapshotRow(
                    name="frontend",
                    path=Path("frontend"),
                    engine="python",
                    files=3,
                    code=35,
                    source_code=20,
                    test_code=10,
                ),
            ),
            skipped_projects=(
                SkippedProject(
                    name="docs",
                    path=Path("docs"),
                    reason="missing optional project path",
                ),
            ),
        )
    )

    assert json.loads(output) == {
        "engine": "python",
        "projects": [
            {
                "name": "frontend",
                "path": ".",
                "report": {
                    "engine": "python",
                    "path": ".",
                    "language_rows": [
                        {
                            "language": "Python",
                            "files": 2,
                            "blank": 1,
                            "comment": 2,
                            "code": 30,
                        },
                        {
                            "language": "Markdown",
                            "files": 1,
                            "blank": 0,
                            "comment": 0,
                            "code": 5,
                        },
                        {
                            "language": "SUM",
                            "files": 3,
                            "blank": 1,
                            "comment": 2,
                            "code": 35,
                        },
                    ],
                    "source_test_summary": {
                        "source_files": 1,
                        "source_code": 20,
                        "test_files": 1,
                        "test_code": 10,
                    },
                    "area_rows": [
                        {"name": "src", "files": 1, "code": 20},
                        {"name": "tests", "files": 1, "code": 10},
                        {"name": "docs", "files": 1, "code": 5},
                    ],
                    "directory_rows": [
                        {"name": "src/slopscope", "files": 1, "code": 20},
                        {"name": "tests", "files": 1, "code": 10},
                        {"name": ".", "files": 1, "code": 5},
                    ],
                },
            }
        ],
        "snapshot_rows": [
            {
                "name": "frontend",
                "path": "frontend",
                "engine": "python",
                "files": 3,
                "code": 35,
                "source_code": 20,
                "test_code": 10,
            }
        ],
        "skipped_projects": [
            {
                "name": "docs",
                "path": "docs",
                "reason": "missing optional project path",
            }
        ],
    }


def test_profile_total_plain_renderer() -> None:
    output = render.render_plain(
        ProfileTotalReport(
            profile="yaml",
            engine="python",
            path=Path("."),
            total=12,
            physical_lines=True,
        )
    )

    assert "Slopscope Profile" in output
    assert "Engine: python (physical lines)" in output
    assert "Profile: yaml" in output
    assert "Total: 12" in output


def test_grouped_profile_json_renderer() -> None:
    output = render.render_json(
        GroupedProfileReport(
            profile="roles",
            engine="cloc",
            path=Path("."),
            group_by="roles/*",
            rows=(GroupedRow(name="roles/web", files=2, code=80),),
            total=123,
            top=20,
            physical_lines=False,
        )
    )

    assert json.loads(output) == {
        "engine": "cloc",
        "path": ".",
        "profile": "roles",
        "group_by": "roles/*",
        "top": 20,
        "total": 123,
        "physical_lines": False,
        "rows": [{"name": "roles/web", "files": 2, "code": 80}],
    }


def test_grouped_profile_plain_renderer() -> None:
    output = render.render_plain(
        GroupedProfileReport(
            profile="roles",
            engine="python",
            path=Path("."),
            group_by="roles/*",
            rows=(GroupedRow(name="roles/web", files=2, code=80),),
            total=123,
            top=None,
            physical_lines=False,
        )
    )

    assert "Slopscope Profile" in output
    assert "Top: all" in output
    assert "roles/web                    2       80" in output


def test_rich_renderer_falls_back_to_plain_when_rich_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import_module = importlib.import_module

    def fake_import_module(name: str) -> object:
        if name.startswith("rich."):
            raise ImportError(name)
        return real_import_module(name)

    monkeypatch.setattr("slopscope.render.importlib.import_module", fake_import_module)

    assert render.render_rich(sample_report()) == render.render_plain(sample_report())


def test_rich_renderer_uses_plain_output_when_color_is_disabled() -> None:
    assert render.render_rich(sample_report(), color=False) == render.render_plain(sample_report())


def test_multi_project_rich_renderer_falls_back_to_plain_when_rich_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import_module(name: str) -> object:
        if name.startswith("rich."):
            raise ImportError(name)
        raise AssertionError(name)

    report = MultiProjectReport(
        engine="python",
        projects=(ProjectReport(name="frontend", report=sample_report(engine="python")),),
        snapshot_rows=(
            ProjectSnapshotRow(
                name="frontend",
                path=Path("frontend"),
                engine="python",
                files=3,
                code=35,
                source_code=20,
                test_code=10,
            ),
        ),
        skipped_projects=(),
    )
    monkeypatch.setattr("slopscope.render.importlib.import_module", fake_import_module)

    assert render.render_rich(report) == render.render_plain(report)


def test_rich_renderer_can_use_imported_rich_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeText:
        def __init__(self, value: str, *, style: str = "") -> None:
            self.value = value
            self.style = style

        def __str__(self) -> str:
            return self.value

    class FakeTable:
        def __init__(self, *, title: str, title_style: str = "") -> None:
            self.title = title
            self.title_style = title_style
            self.rows: list[tuple[str, ...]] = []

        def add_column(self, *_args: object, **_kwargs: object) -> None:
            return None

        def add_row(self, *values: str) -> None:
            self.rows.append(values)

        def __str__(self) -> str:
            return self.title

    class FakeConsole:
        def __init__(self, *, file: TextIO, **_kwargs: object) -> None:
            self.file = file

        def print(self, value: object = "") -> None:
            print(value, file=self.file)

    def fake_import_module(name: str) -> object:
        if name == "rich.console":
            return SimpleNamespace(Console=FakeConsole)
        elif name == "rich.table":
            return SimpleNamespace(Table=FakeTable)
        elif name == "rich.text":
            return SimpleNamespace(Text=FakeText)
        raise ImportError(name)

    monkeypatch.setattr("slopscope.render.importlib.import_module", fake_import_module)

    output = render.render_rich(sample_report())

    assert "Slopscope Report" in output
    assert "Language Summary" in output
    assert "Directory Buckets" in output
    assert "Engine: cloc" in output
