"""Profile execution helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from slopscope import cloc, fallback, paths
from slopscope import config as config_module
from slopscope.report import FileRow, GroupedProfileReport, GroupedRow, ProfileTotalReport


class ProfileError(Exception):
    """User-facing profile configuration or selection error."""


class ProfileCountError(Exception):
    """Counting engine error while building a profile report."""

    def __init__(self, message: str, *, returncode: int) -> None:
        super().__init__(message)
        self.returncode = returncode


ProfileReport = ProfileTotalReport | GroupedProfileReport


def find_profile(
    slopscope_config: config_module.SlopscopeConfig,
    name: str,
) -> config_module.ProfileConfig:
    """Return a configured profile by name, or raise a user-facing error."""

    for configured_profile in slopscope_config.profiles:
        if configured_profile.name == name:
            return configured_profile
    raise ProfileError(f"no configured profile named {name!r}")


def build_profile_report(
    *,
    path: Path,
    slopscope_config: config_module.SlopscopeConfig,
    selected_profile: config_module.ProfileConfig,
    engine: str,
    top: int | None = None,
) -> ProfileReport:
    """Build a total or grouped report for one configured profile."""

    file_rows = _build_profile_file_rows(
        path=path,
        slopscope_config=slopscope_config,
        selected_profile=selected_profile,
        engine=engine,
    )
    effective_top = top if top is not None else selected_profile.top

    if selected_profile.group_by is not None:
        return _build_grouped_report(
            path=path,
            selected_profile=selected_profile,
            engine=_report_engine(selected_profile=selected_profile, engine=engine),
            file_rows=file_rows,
            top=effective_top,
        )

    return ProfileTotalReport(
        profile=selected_profile.name,
        engine=_report_engine(selected_profile=selected_profile, engine=engine),
        path=path,
        total=sum(row.code for row in file_rows),
        physical_lines=selected_profile.physical_lines,
    )


def _build_profile_file_rows(
    *,
    path: Path,
    slopscope_config: config_module.SlopscopeConfig,
    selected_profile: config_module.ProfileConfig,
    engine: str,
) -> tuple[FileRow, ...]:
    if selected_profile.physical_lines or engine == "python":
        return tuple(
            fallback.build_file_rows(
                path,
                excluded_paths=_effective_fallback_excludes(slopscope_config),
                include_globs=_effective_include_globs(
                    slopscope_config=slopscope_config,
                    selected_profile=selected_profile,
                ),
                include_languages=_effective_include_languages(
                    slopscope_config=slopscope_config,
                    selected_profile=selected_profile,
                ),
                exclude_languages=_effective_exclude_languages(
                    slopscope_config=slopscope_config,
                    selected_profile=selected_profile,
                ),
            )
        )

    result = cloc.run_file_summary(path)
    if result.returncode != 0:
        message = result.stderr.strip() or "cloc file summary failed without stderr output"
        raise ProfileCountError(message, returncode=result.returncode)

    return _filter_cloc_file_rows(
        cloc.parse_file_summary_csv(result.stdout),
        path=path,
        slopscope_config=slopscope_config,
        selected_profile=selected_profile,
    )


def _filter_cloc_file_rows(
    file_rows: Sequence[FileRow],
    *,
    path: Path,
    slopscope_config: config_module.SlopscopeConfig,
    selected_profile: config_module.ProfileConfig,
) -> tuple[FileRow, ...]:
    include_languages = _effective_include_languages(
        slopscope_config=slopscope_config,
        selected_profile=selected_profile,
    )
    exclude_languages = _effective_exclude_languages(
        slopscope_config=slopscope_config,
        selected_profile=selected_profile,
    )
    include_globs = _effective_include_globs(
        slopscope_config=slopscope_config,
        selected_profile=selected_profile,
    )

    rows: list[FileRow] = []
    for row in file_rows:
        relative_path = paths.row_filter_path(row.path, root=path)
        if include_languages and row.language not in include_languages:
            continue
        if row.language in exclude_languages:
            continue
        if slopscope_config.exclude_dirs and fallback.is_excluded_path(
            relative_path, slopscope_config.exclude_dirs
        ):
            continue
        if include_globs and not fallback.matches_include_globs(relative_path, include_globs):
            continue
        rows.append(row)
    return tuple(rows)


def _build_grouped_report(
    *,
    path: Path,
    selected_profile: config_module.ProfileConfig,
    engine: str,
    file_rows: Sequence[FileRow],
    top: int | None,
) -> GroupedProfileReport:
    group_by = selected_profile.group_by
    if group_by is None:
        raise AssertionError("grouped profile report requires group_by")
    _group_wildcard_index(group_by)

    totals: dict[str, tuple[int, int]] = {}
    for row in file_rows:
        relative_path = paths.row_filter_path(row.path, root=path)
        group_name = _group_name(relative_path, group_by)
        if group_name is None:
            continue
        files, code = totals.get(group_name, (0, 0))
        totals[group_name] = (files + 1, code + row.code)

    rows = [GroupedRow(name=name, files=files, code=code) for name, (files, code) in totals.items()]
    rows.sort(key=lambda row: (-row.code, -row.files, row.name))
    visible_rows = tuple(rows[:top]) if top is not None else tuple(rows)
    return GroupedProfileReport(
        profile=selected_profile.name,
        engine=engine,
        path=path,
        group_by=group_by,
        rows=visible_rows,
        total=sum(row.code for row in rows),
        top=top,
        physical_lines=selected_profile.physical_lines,
    )


def _group_name(path: Path | str, pattern: str) -> str | None:
    path_parts = _path_parts(path)
    pattern_parts = _path_parts(pattern)
    wildcard_index = _group_wildcard_index(pattern)
    prefix = pattern_parts[:wildcard_index]
    suffix = pattern_parts[wildcard_index + 1 :]
    if len(path_parts) <= wildcard_index:
        return None
    if prefix and path_parts[: len(prefix)] != prefix:
        return None
    if suffix:
        suffix_start = wildcard_index + 1
        if len(path_parts) < suffix_start + len(suffix):
            return None
        if path_parts[suffix_start : suffix_start + len(suffix)] != suffix:
            return None

    return "/".join(path_parts[: wildcard_index + 1])


def _group_wildcard_index(pattern: str) -> int:
    pattern_parts = _path_parts(pattern)
    wildcard_indexes = [index for index, part in enumerate(pattern_parts) if part == "*"]
    if len(wildcard_indexes) != 1:
        raise ProfileError(f"group_by must contain exactly one '*' path segment: {pattern}")
    return wildcard_indexes[0]


def _effective_include_languages(
    *,
    slopscope_config: config_module.SlopscopeConfig,
    selected_profile: config_module.ProfileConfig,
) -> tuple[str, ...]:
    if selected_profile.include_languages:
        return selected_profile.include_languages
    return slopscope_config.include_languages


def _effective_exclude_languages(
    *,
    slopscope_config: config_module.SlopscopeConfig,
    selected_profile: config_module.ProfileConfig,
) -> tuple[str, ...]:
    if selected_profile.exclude_languages:
        return selected_profile.exclude_languages
    return slopscope_config.exclude_languages


def _effective_include_globs(
    *,
    slopscope_config: config_module.SlopscopeConfig,
    selected_profile: config_module.ProfileConfig,
) -> tuple[str, ...]:
    if selected_profile.include_globs:
        return selected_profile.include_globs
    return slopscope_config.include_globs


def _effective_fallback_excludes(
    slopscope_config: config_module.SlopscopeConfig,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (*sorted(fallback.DEFAULT_EXCLUDED_PATH_SEGMENTS), *slopscope_config.exclude_dirs)
        )
    )


def _report_engine(
    *,
    selected_profile: config_module.ProfileConfig,
    engine: str,
) -> str:
    if selected_profile.physical_lines:
        return "python"
    return engine


def _path_parts(path: Path | str) -> tuple[str, ...]:
    file_path = Path(path)
    return tuple(part for part in file_path.parts if part not in ("", ".", file_path.anchor))
