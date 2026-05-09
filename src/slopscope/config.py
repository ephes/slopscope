"""Configuration loading and validation for slopscope."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from slopscope import classify


class ConfigError(Exception):
    """User-facing configuration error."""


@dataclass(frozen=True)
class ProjectConfig:
    """A named project configured under ``tool.slopscope.projects``."""

    name: str
    path: Path
    optional: bool = False


@dataclass(frozen=True)
class ProfileConfig:
    """A named profile configured under ``tool.slopscope.profiles``."""

    name: str
    include_languages: tuple[str, ...] = ()
    exclude_languages: tuple[str, ...] = ()
    include_globs: tuple[str, ...] = ()
    physical_lines: bool = False
    group_by: str | None = None
    top: int | None = None


@dataclass(frozen=True)
class SlopscopeConfig:
    """Validated ``[tool.slopscope]`` configuration."""

    exclude_languages: tuple[str, ...] = ()
    include_languages: tuple[str, ...] = ()
    exclude_dirs: tuple[str, ...] = ()
    include_globs: tuple[str, ...] = ()
    source_dirs: tuple[str, ...] = classify.DEFAULT_SOURCE_DIRS
    test_dirs: tuple[str, ...] = classify.DEFAULT_TEST_DIRS
    areas: tuple[str, ...] = classify.DEFAULT_NAMED_AREAS
    nested_bucket_dirs: tuple[str, ...] = classify.DEFAULT_NESTED_BUCKET_DIRS
    projects: tuple[ProjectConfig, ...] = ()
    profiles: tuple[ProfileConfig, ...] = ()


_STRING_LIST_FIELDS = {
    "exclude_languages",
    "include_languages",
    "exclude_dirs",
    "include_globs",
    "source_dirs",
    "test_dirs",
    "areas",
    "nested_bucket_dirs",
}
_TOP_LEVEL_FIELDS = _STRING_LIST_FIELDS | {"projects", "profiles"}
_PROFILE_FIELDS = {
    "name",
    "include_languages",
    "exclude_languages",
    "include_globs",
    "physical_lines",
    "group_by",
    "top",
}


def find_default_config_path(repository_path: Path | str) -> Path | None:
    """Return the default ``pyproject.toml`` path when it exists."""

    candidate = Path(repository_path) / "pyproject.toml"
    if candidate.is_file():
        return candidate
    return None


def load_config(path: Path | str | None) -> SlopscopeConfig:
    """Load configuration from a pyproject path, or return defaults for ``None``."""

    if path is None:
        return SlopscopeConfig()
    return load_config_from_pyproject(Path(path))


def load_config_from_pyproject(path: Path | str) -> SlopscopeConfig:
    """Load and validate ``[tool.slopscope]`` from a pyproject-style TOML file."""

    config_path = Path(path)
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {config_path}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read configuration file {config_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        raise ConfigError("[tool] must be a table")

    slopscope_data = tool.get("slopscope")
    if slopscope_data is None:
        return SlopscopeConfig()
    if not isinstance(slopscope_data, dict):
        raise ConfigError("[tool.slopscope] must be a table")

    return parse_config_mapping(slopscope_data, config_path=config_path)


def parse_config_mapping(
    data: Mapping[str, object],
    *,
    config_path: Path | None = None,
) -> SlopscopeConfig:
    """Validate a ``[tool.slopscope]`` mapping."""

    unknown = sorted(set(data) - _TOP_LEVEL_FIELDS)
    if unknown:
        raise ConfigError(f"unknown [tool.slopscope] field: {unknown[0]}")

    include_languages = _string_tuple(data, "include_languages", default=())
    exclude_languages = _string_tuple(data, "exclude_languages", default=())
    _validate_language_filters(include_languages, exclude_languages, context="[tool.slopscope]")

    return SlopscopeConfig(
        exclude_languages=exclude_languages,
        include_languages=include_languages,
        exclude_dirs=_string_tuple(data, "exclude_dirs", default=()),
        include_globs=_string_tuple(data, "include_globs", default=()),
        source_dirs=_string_tuple(
            data,
            "source_dirs",
            default=classify.DEFAULT_SOURCE_DIRS,
        ),
        test_dirs=_string_tuple(data, "test_dirs", default=classify.DEFAULT_TEST_DIRS),
        areas=_string_tuple(data, "areas", default=classify.DEFAULT_NAMED_AREAS),
        nested_bucket_dirs=_string_tuple(
            data,
            "nested_bucket_dirs",
            default=classify.DEFAULT_NESTED_BUCKET_DIRS,
        ),
        projects=_projects_tuple(data, config_path=config_path),
        profiles=_profiles_tuple(data),
    )


def _string_tuple(
    data: Mapping[str, object],
    field: str,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    if field not in data:
        return default

    value = data[field]
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be an array of strings")

    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ConfigError(f"{field}[{index}] must be a non-empty string")
        values.append(item)
    return tuple(values)


def _projects_tuple(
    data: Mapping[str, object],
    *,
    config_path: Path | None,
) -> tuple[ProjectConfig, ...]:
    value = data.get("projects", [])
    if not isinstance(value, list):
        raise ConfigError("projects must be an array of tables")

    projects: list[ProjectConfig] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConfigError(f"projects[{index}] must be a table")
        project = _parse_project(item, index=index, config_path=config_path)
        if project.name in names:
            raise ConfigError(f"duplicate project name: {project.name}")
        names.add(project.name)
        projects.append(project)
    return tuple(projects)


def _parse_project(
    data: Mapping[str, object],
    *,
    index: int,
    config_path: Path | None,
) -> ProjectConfig:
    unknown = sorted(set(data) - {"name", "path", "optional"})
    if unknown:
        raise ConfigError(f"unknown projects[{index}] field: {unknown[0]}")

    name = _required_string(data, "name", context=f"projects[{index}]")
    path_value = _required_string(data, "path", context=f"projects[{index}]")
    optional = _bool_value(data, "optional", default=False, context=f"projects[{index}]")
    return ProjectConfig(
        name=name,
        path=_resolve_config_path(path_value, config_path=config_path),
        optional=optional,
    )


def _profiles_tuple(data: Mapping[str, object]) -> tuple[ProfileConfig, ...]:
    value = data.get("profiles", [])
    if not isinstance(value, list):
        raise ConfigError("profiles must be an array of tables")

    profiles: list[ProfileConfig] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConfigError(f"profiles[{index}] must be a table")
        profile = _parse_profile(item, index=index)
        if profile.name in names:
            raise ConfigError(f"duplicate profile name: {profile.name}")
        names.add(profile.name)
        profiles.append(profile)
    return tuple(profiles)


def _parse_profile(data: Mapping[str, object], *, index: int) -> ProfileConfig:
    unknown = sorted(set(data) - _PROFILE_FIELDS)
    if unknown:
        raise ConfigError(f"unknown profiles[{index}] field: {unknown[0]}")

    context = f"profiles[{index}]"
    include_languages = _string_tuple(data, "include_languages", default=())
    exclude_languages = _string_tuple(data, "exclude_languages", default=())
    _validate_language_filters(include_languages, exclude_languages, context=context)

    return ProfileConfig(
        name=_required_string(data, "name", context=context),
        include_languages=include_languages,
        exclude_languages=exclude_languages,
        include_globs=_string_tuple(data, "include_globs", default=()),
        physical_lines=_bool_value(data, "physical_lines", default=False, context=context),
        group_by=_optional_string(data, "group_by", context=context),
        top=_optional_positive_int(data, "top", context=context),
    )


def _required_string(data: Mapping[str, object], field: str, *, context: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{context}.{field} must be a non-empty string")
    return value


def _optional_string(data: Mapping[str, object], field: str, *, context: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{context}.{field} must be a non-empty string")
    return value


def _bool_value(
    data: Mapping[str, object],
    field: str,
    *,
    default: bool,
    context: str,
) -> bool:
    value = data.get(field, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{context}.{field} must be a boolean")
    return value


def _optional_positive_int(
    data: Mapping[str, object],
    field: str,
    *,
    context: str,
) -> int | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{context}.{field} must be a positive integer")
    return value


def _validate_language_filters(
    include_languages: tuple[str, ...],
    exclude_languages: tuple[str, ...],
    *,
    context: str,
) -> None:
    conflicts = sorted(set(include_languages) & set(exclude_languages))
    if conflicts:
        raise ConfigError(f"{context} cannot include and exclude the same language: {conflicts[0]}")


def _resolve_config_path(value: str, *, config_path: Path | None) -> Path:
    path = Path(value)
    if path.is_absolute() or config_path is None:
        return path
    return (config_path.parent / path).resolve()
