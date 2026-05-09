from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from slopscope import cli, fallback


def disable_git_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "slopscope.fallback.run_git_ls_files",
        lambda _path: fallback.GitLsFilesResult(returncode=128, stdout=b""),
    )


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(args, stdout=stdout, stderr=stderr)

    return exit_code, stdout.getvalue(), stderr.getvalue()


def run_cli_json(args: list[str]) -> tuple[dict[str, Any], str]:
    exit_code, stdout, stderr = run_cli(args)

    assert exit_code == 0
    return json.loads(stdout), stderr


def row_by_name(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for row in rows:
        if row.get("name") == name:
            return row
    raise AssertionError(f"missing row named {name!r}: {rows}")


def language_row(rows: list[dict[str, Any]], language: str) -> dict[str, Any]:
    for row in rows:
        if row.get("language") == language:
            return row
    raise AssertionError(f"missing language row {language!r}: {rows}")


def test_standard_python_src_package_fixture_reports_default_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    write_lines(tmp_path / "src" / "example_pkg" / "__init__.py", ["from .core import add"])
    write_lines(
        tmp_path / "src" / "example_pkg" / "core.py",
        ["def add(left: int, right: int) -> int:", "    return left + right"],
    )
    write_lines(
        tmp_path / "tests" / "test_core.py",
        [
            "from example_pkg.core import add",
            "",
            "def test_add() -> None:",
            "    assert add(1, 2) == 3",
        ],
    )
    write_lines(tmp_path / "README.md", ["# Example package", "", "Local test fixture."])

    data, stderr = run_cli_json(["--engine", "python", "--format", "json", str(tmp_path)])

    assert data["engine"] == "python"
    assert language_row(data["language_rows"], "Python") == {
        "language": "Python",
        "files": 3,
        "blank": 0,
        "comment": 0,
        "code": 7,
    }
    assert data["source_test_summary"] == {
        "source_files": 2,
        "source_code": 3,
        "test_files": 1,
        "test_code": 4,
    }
    assert row_by_name(data["area_rows"], "src") == {"name": "src", "files": 2, "code": 3}
    assert row_by_name(data["area_rows"], "tests") == {
        "name": "tests",
        "files": 1,
        "code": 4,
    }
    assert row_by_name(data["directory_rows"], "src/example_pkg") == {
        "name": "src/example_pkg",
        "files": 2,
        "code": 3,
    }
    assert row_by_name(data["directory_rows"], "tests") == {
        "name": "tests",
        "files": 1,
        "code": 4,
    }
    assert stderr == ""


def test_django_style_fixture_counts_python_templates_docs_and_static_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    write_lines(
        tmp_path / "manage.py",
        [
            "from django.core.management import execute_from_command_line",
            "execute_from_command_line()",
        ],
    )
    write_lines(tmp_path / "siteapp" / "__init__.py", [])
    write_lines(
        tmp_path / "siteapp" / "settings.py",
        ["SECRET_KEY = 'fixture'", "ROOT_URLCONF = 'siteapp.urls'", "INSTALLED_APPS = ['siteapp']"],
    )
    write_lines(
        tmp_path / "siteapp" / "views.py",
        [
            "from django.shortcuts import render",
            "",
            "def index(request):",
            "    return render(request, 'siteapp/index.html')",
        ],
    )
    write_lines(
        tmp_path / "tests" / "test_views.py",
        [
            "def test_index(client):",
            "    response = client.get('/')",
            "    assert response.status_code == 200",
            "",
        ],
    )
    write_lines(
        tmp_path / "templates" / "siteapp" / "index.html",
        [
            "<!doctype html>",
            "<html>",
            "<body>",
            "<h1>Fixture</h1>",
            "</body>",
            "</html>",
        ],
    )
    write_lines(tmp_path / "docs" / "usage.md", ["# Usage", "", "Run the fixture app."])
    write_lines(
        tmp_path / "static" / "siteapp" / "app.js",
        ["const root = document.querySelector('#root');", "root.dataset.ready = 'true';", ""],
    )
    write_lines(
        tmp_path / "static" / "siteapp" / "site.css",
        ["body {", "  color: #222;", "}"],
    )

    data, stderr = run_cli_json(["--engine", "python", "--format", "json", str(tmp_path)])

    languages = {row["language"] for row in data["language_rows"]}
    assert {"Python", "HTML", "Markdown", "JavaScript", "CSS"}.issubset(languages)
    assert data["source_test_summary"]["test_files"] == 1
    assert data["source_test_summary"]["test_code"] == 4
    assert row_by_name(data["area_rows"], "tests") == {
        "name": "tests",
        "files": 1,
        "code": 4,
    }
    assert row_by_name(data["area_rows"], "docs") == {"name": "docs", "files": 1, "code": 3}
    assert row_by_name(data["directory_rows"], "templates") == {
        "name": "templates",
        "files": 1,
        "code": 6,
    }
    assert row_by_name(data["directory_rows"], "static") == {
        "name": "static",
        "files": 2,
        "code": 6,
    }
    assert row_by_name(data["directory_rows"], "docs") == {
        "name": "docs",
        "files": 1,
        "code": 3,
    }
    assert stderr == ""


def test_infrastructure_yaml_total_profile_fixture_prints_integer_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    write_lines(tmp_path / "inventory.yml", ["all:", "  hosts:", "    app:"])
    write_lines(tmp_path / "vars" / "app.yaml", ["image: sample", "replicas: 2"])
    write_lines(
        tmp_path / "playbooks" / "site.yml",
        ["- hosts: all", "  tasks:", "    - debug:", "        msg: ready"],
    )
    write_lines(tmp_path / "README.md", ["# Infrastructure", "", "Ignored by YAML profile."])
    write_lines(tmp_path / "scripts" / "deploy.sh", ["#!/usr/bin/env sh", "echo deploy"])
    write_lines(
        tmp_path / "pyproject.toml",
        [
            "[tool.slopscope]",
            "",
            "[[tool.slopscope.profiles]]",
            'name = "yaml"',
            'include_languages = ["YAML"]',
            'include_globs = ["*.yml", "*.yaml", "**/*.yml", "**/*.yaml"]',
            "physical_lines = true",
        ],
    )

    exit_code, stdout, stderr = run_cli(
        ["--engine", "cloc", "--profile", "yaml", "--total-only", str(tmp_path)]
    )

    assert exit_code == 0
    assert stdout == "9\n"
    assert stderr == ""


def test_role_like_grouped_yaml_profile_fixture_sorts_and_totals_matched_roles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    write_lines(tmp_path / "roles" / "web" / "tasks.yml", ["one", "two", "three", "four", "five"])
    write_lines(tmp_path / "roles" / "web" / "handlers.yml", ["one", "two"])
    write_lines(
        tmp_path / "roles" / "api" / "tasks.yml",
        ["one", "two", "three", "four", "five", "six", "seven"],
    )
    write_lines(tmp_path / "roles" / "db" / "tasks.yml", ["one", "two", "three", "four"])
    write_lines(tmp_path / "playbook.yml", ["one", "two", "three", "four", "five"])
    write_lines(tmp_path / "docs" / "roles.md", ["# Roles", "", "Ignored by YAML profile."])
    write_lines(
        tmp_path / "pyproject.toml",
        [
            "[tool.slopscope]",
            "",
            "[[tool.slopscope.profiles]]",
            'name = "roles"',
            'include_languages = ["YAML"]',
            'group_by = "roles/*"',
            "top = 2",
        ],
    )

    data, stderr = run_cli_json(
        ["--engine", "python", "--profile", "roles", "--format", "json", str(tmp_path)]
    )

    assert data["profile"] == "roles"
    assert data["group_by"] == "roles/*"
    assert data["top"] == 2
    assert data["total"] == 18
    assert data["rows"] == [
        {"name": "roles/web", "files": 2, "code": 7},
        {"name": "roles/api", "files": 1, "code": 7},
    ]
    assert stderr == ""


def test_multi_project_frontend_backend_workspace_fixture_reports_projects_and_skip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    write_lines(
        tmp_path / "frontend" / "src" / "App.tsx",
        ["export function App() {", "  return <main>Fixture</main>;", "}", ""],
    )
    write_lines(
        tmp_path / "frontend" / "src" / "main.ts",
        ["import { App } from './App';", "console.log(App);", ""],
    )
    write_lines(tmp_path / "frontend" / "src" / "styles.css", ["main {", "  display: grid;", "}"])
    write_lines(
        tmp_path / "frontend" / "tests" / "app.spec.ts",
        ["import { App } from '../src/App';", "console.log(App);", ""],
    )
    write_lines(
        tmp_path / "backend" / "src" / "service" / "app.py",
        ["def handler() -> str:", "    return 'ok'", ""],
    )
    write_lines(
        tmp_path / "backend" / "tests" / "test_app.py",
        [
            "from service.app import handler",
            "",
            "def test_handler() -> None:",
            "    assert handler() == 'ok'",
        ],
    )
    write_lines(tmp_path / "backend" / "README.md", ["# Backend", "Fixture service."])
    write_lines(
        tmp_path / "pyproject.toml",
        [
            "[tool.slopscope]",
            "",
            "[[tool.slopscope.projects]]",
            'name = "frontend"',
            'path = "frontend"',
            "",
            "[[tool.slopscope.projects]]",
            'name = "backend"',
            'path = "backend"',
            "",
            "[[tool.slopscope.projects]]",
            'name = "docs"',
            'path = "docs"',
            "optional = true",
        ],
    )

    data, stderr = run_cli_json(
        [
            "--config",
            str(tmp_path / "pyproject.toml"),
            "--project",
            "all",
            "--engine",
            "python",
            "--format",
            "json",
        ]
    )

    assert [project["name"] for project in data["projects"]] == ["frontend", "backend"]
    assert [row["name"] for row in data["snapshot_rows"]] == ["frontend", "backend"]
    assert data["snapshot_rows"][0]["source_code"] == 10
    assert data["snapshot_rows"][0]["test_code"] == 3
    assert data["snapshot_rows"][1]["source_code"] == 3
    assert data["snapshot_rows"][1]["test_code"] == 4
    assert data["projects"][0]["report"]["directory_rows"]
    assert data["projects"][1]["report"]["language_rows"]
    assert data["skipped_projects"] == [
        {
            "name": "docs",
            "path": str((tmp_path / "docs").resolve()),
            "reason": "missing optional project path",
        }
    ]
    assert stderr.startswith("slopscope: skipping optional project docs:")
    assert len(stderr.splitlines()) == 1


def test_desktop_style_fixture_excludes_generated_app_shell_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disable_git_discovery(monkeypatch)
    write_lines(
        tmp_path / "apps" / "desktop" / "src" / "main.ts",
        ["export function start() {", "  return 'desktop';", "}", ""],
    )
    write_lines(tmp_path / "apps" / "desktop" / "src" / "app.css", ["body {", "  margin: 0;", "}"])
    write_lines(
        tmp_path / "shells" / "electron" / "main.js",
        ["const path = require('node:path');", "console.log(path);", ""],
    )
    write_lines(tmp_path / "shells" / "electron" / "preload.js", ["window.app = {};", ""])
    write_lines(
        tmp_path / "shells" / "tauri" / "src-tauri" / "tauri.conf.json",
        ["{", '  "productName": "Fixture Desktop",', '  "version": "0.1.0"', "}"],
    )
    write_lines(
        tmp_path / "shells" / "electron" / "build-output" / "bundle.js",
        ["generated"] * 99,
    )
    write_lines(
        tmp_path / "shells" / "electron" / "vendor-cache" / "package" / "index.js",
        ["generated"] * 99,
    )
    write_lines(
        tmp_path / "shells" / "tauri" / "src-tauri" / "target" / "generated.js",
        ["generated"] * 99,
    )
    write_lines(
        tmp_path / "pyproject.toml",
        [
            "[tool.slopscope]",
            'include_languages = ["TypeScript", "JavaScript", "CSS", "JSON"]',
            "exclude_dirs = [",
            '  "shells/electron/build-output",',
            '  "shells/electron/vendor-cache",',
            '  "shells/tauri/src-tauri/target",',
            "]",
            'nested_bucket_dirs = ["apps", "shells"]',
        ],
    )

    data, stderr = run_cli_json(["--engine", "python", "--format", "json", str(tmp_path)])

    assert language_row(data["language_rows"], "SUM") == {
        "language": "SUM",
        "files": 5,
        "blank": 0,
        "comment": 0,
        "code": 16,
    }
    assert language_row(data["language_rows"], "JavaScript") == {
        "language": "JavaScript",
        "files": 2,
        "blank": 0,
        "comment": 0,
        "code": 5,
    }
    assert row_by_name(data["directory_rows"], "apps/desktop") == {
        "name": "apps/desktop",
        "files": 2,
        "code": 7,
    }
    assert row_by_name(data["directory_rows"], "shells/electron") == {
        "name": "shells/electron",
        "files": 2,
        "code": 5,
    }
    assert row_by_name(data["directory_rows"], "shells/tauri") == {
        "name": "shells/tauri",
        "files": 1,
        "code": 4,
    }
    assert stderr == ""
