"""Environment for Git processes that read repositories slopscope did not create.

Repository-local Git configuration can name programs that Git runs while it lists files or
walks history: ``core.fsmonitor`` for index reads, and ``log.showSignature`` with
``gpg.program`` for signed commits. A cloned repository cannot ship its own ``.git/config``,
but it can track a directory laid out as a bare repository; ``git -C`` into that directory
(for example a configured project) would use the tracked ``config`` file. Command-line scope
configuration overrides every repository file, and ``safe.bareRepository`` is honored only from
protected scopes such as this one, so these settings are appended through ``GIT_CONFIG_COUNT``
and ``GIT_CONFIG_PARAMETERS``. Child processes inherit them, which also covers Git run by cloc.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

HARDENED_GIT_CONFIG: tuple[tuple[str, str], ...] = (
    ("safe.bareRepository", "explicit"),
    ("core.fsmonitor", "false"),
    ("log.showSignature", "false"),
)


def hardened_git_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``base`` (default: ``os.environ``) with the hardening settings appended."""

    env = dict(os.environ if base is None else base)
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = -1
    if count < 0:
        # Git rejects this count and every command would fail; replace it with ours.
        count = 0
    for offset, (key, value) in enumerate(HARDENED_GIT_CONFIG):
        env[f"GIT_CONFIG_KEY_{count + offset}"] = key
        env[f"GIT_CONFIG_VALUE_{count + offset}"] = value
    env["GIT_CONFIG_COUNT"] = str(count + len(HARDENED_GIT_CONFIG))
    # Git before 2.31 ignores GIT_CONFIG_COUNT. GIT_CONFIG_PARAMETERS (what ``git -c`` exports)
    # works on every supported Git and is read after the COUNT entries, so ours stay last.
    parameters = " ".join(f"'{key}={value}'" for key, value in HARDENED_GIT_CONFIG)
    existing = env.get("GIT_CONFIG_PARAMETERS", "").strip()
    env["GIT_CONFIG_PARAMETERS"] = f"{existing} {parameters}" if existing else parameters
    # Deny every transport, including on Git that still attempts a lazy fetch. The environment
    # allowlist takes precedence over repository protocol settings and is inherited by child Git.
    env["GIT_ALLOW_PROTOCOL"] = ""
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env
