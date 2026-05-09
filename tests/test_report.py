from __future__ import annotations

from pathlib import Path

from slopscope.report import LanguageRow, LanguageSummaryReport


def test_language_summary_report_from_rows_normalizes_path_and_rows() -> None:
    rows = [
        LanguageRow(language="Python", files=2, blank=10, comment=4, code=90),
        LanguageRow(language="SUM", files=2, blank=10, comment=4, code=90),
    ]

    report = LanguageSummaryReport.from_rows(
        engine="cloc",
        path=".",
        language_rows=rows,
    )

    assert report.engine == "cloc"
    assert report.path == Path(".")
    assert report.language_rows == tuple(rows)
