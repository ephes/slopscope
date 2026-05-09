from __future__ import annotations

from pathlib import Path

import pytest

from slopscope import classify, config


def test_find_default_config_path_returns_pyproject_when_present(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.slopscope]\n", encoding="utf-8")

    assert config.find_default_config_path(tmp_path) == pyproject


def test_find_default_config_path_returns_none_when_absent(tmp_path: Path) -> None:
    assert config.find_default_config_path(tmp_path) is None


def test_load_config_defaults_without_path() -> None:
    assert config.load_config(None) == config.SlopscopeConfig()


def test_load_config_defaults_when_pyproject_has_no_slopscope_table(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "sample"\n', encoding="utf-8")

    assert config.load_config_from_pyproject(pyproject) == config.SlopscopeConfig()


def test_load_valid_slopscope_config(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[tool.slopscope]
include_languages = ["Python", "Markdown"]
exclude_languages = ["JSON"]
exclude_dirs = ["generated", "docs/_build"]
include_globs = ["*.py", "docs/*.md"]
source_dirs = ["lib"]
test_dirs = ["checks"]
areas = ["docs", "infra"]
nested_bucket_dirs = ["packages"]

[[tool.slopscope.projects]]
name = "app"
path = "packages/app"

[[tool.slopscope.projects]]
name = "docs"
path = "../docs"
optional = true

[[tool.slopscope.profiles]]
name = "yaml"
include_languages = ["YAML"]
include_globs = ["*.yml", "*.yaml"]
physical_lines = true
group_by = "roles/*"
top = 20
""".strip(),
        encoding="utf-8",
    )

    loaded = config.load_config_from_pyproject(pyproject)

    assert loaded.include_languages == ("Python", "Markdown")
    assert loaded.exclude_languages == ("JSON",)
    assert loaded.exclude_dirs == ("generated", "docs/_build")
    assert loaded.include_globs == ("*.py", "docs/*.md")
    assert loaded.source_dirs == ("lib",)
    assert loaded.test_dirs == ("checks",)
    assert loaded.areas == ("docs", "infra")
    assert loaded.nested_bucket_dirs == ("packages",)
    assert loaded.projects == (
        config.ProjectConfig(name="app", path=(tmp_path / "packages/app").resolve()),
        config.ProjectConfig(name="docs", path=(tmp_path / "../docs").resolve(), optional=True),
    )
    assert loaded.profiles == (
        config.ProfileConfig(
            name="yaml",
            include_languages=("YAML",),
            include_globs=("*.yml", "*.yaml"),
            physical_lines=True,
            group_by="roles/*",
            top=20,
        ),
    )


def test_default_classification_values_match_classify_defaults() -> None:
    loaded = config.parse_config_mapping({})

    assert loaded.source_dirs == classify.DEFAULT_SOURCE_DIRS
    assert loaded.test_dirs == classify.DEFAULT_TEST_DIRS
    assert loaded.areas == classify.DEFAULT_NAMED_AREAS
    assert loaded.nested_bucket_dirs == classify.DEFAULT_NESTED_BUCKET_DIRS


def test_missing_explicit_config_path_is_error(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="configuration file not found"):
        config.load_config_from_pyproject(tmp_path / "missing.toml")


def test_invalid_toml_is_error(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.slopscope\n", encoding="utf-8")

    with pytest.raises(config.ConfigError, match="invalid TOML"):
        config.load_config_from_pyproject(pyproject)


def test_invalid_field_type_is_error() -> None:
    with pytest.raises(config.ConfigError, match="exclude_dirs must be an array of strings"):
        config.parse_config_mapping({"exclude_dirs": "build"})


def test_unknown_top_level_field_is_error() -> None:
    with pytest.raises(config.ConfigError, match="unknown \\[tool\\.slopscope\\] field: unknown"):
        config.parse_config_mapping({"unknown": True})


def test_invalid_string_list_item_is_error() -> None:
    with pytest.raises(config.ConfigError, match=r"include_languages\[1\]"):
        config.parse_config_mapping({"include_languages": ["Python", ""]})


def test_conflicting_language_filters_are_error() -> None:
    with pytest.raises(config.ConfigError, match="cannot include and exclude"):
        config.parse_config_mapping(
            {"include_languages": ["Python"], "exclude_languages": ["Python"]}
        )


def test_projects_validate_required_fields() -> None:
    with pytest.raises(config.ConfigError, match=r"projects\[0\]\.path"):
        config.parse_config_mapping({"projects": [{"name": "app"}]})


def test_projects_reject_unknown_fields() -> None:
    with pytest.raises(config.ConfigError, match=r"unknown projects\[0\] field: extra"):
        config.parse_config_mapping({"projects": [{"name": "app", "path": ".", "extra": True}]})


def test_projects_reject_duplicate_names() -> None:
    with pytest.raises(config.ConfigError, match="duplicate project name"):
        config.parse_config_mapping(
            {"projects": [{"name": "app", "path": "."}, {"name": "app", "path": "other"}]}
        )


def test_profiles_validate_fields() -> None:
    with pytest.raises(config.ConfigError, match=r"profiles\[0\]\.top"):
        config.parse_config_mapping({"profiles": [{"name": "yaml", "top": 0}]})


def test_profiles_reject_unknown_fields() -> None:
    with pytest.raises(config.ConfigError, match=r"unknown profiles\[0\] field: extra"):
        config.parse_config_mapping({"profiles": [{"name": "yaml", "extra": True}]})


def test_profiles_reject_duplicate_names() -> None:
    with pytest.raises(config.ConfigError, match="duplicate profile name"):
        config.parse_config_mapping({"profiles": [{"name": "yaml"}, {"name": "yaml"}]})
