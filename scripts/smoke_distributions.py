"""Install built artifacts separately and smoke their public commands outside the checkout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def smoke(artifact: Path) -> None:
    artifact = artifact.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="slopscope-package-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(environment)], cwd=root, check=True
        )
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), "--no-deps", str(artifact)],
            cwd=root,
            env=env,
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import importlib.util, pathlib, slopscope, sys; "
                "assert pathlib.Path(slopscope.__file__).is_relative_to(sys.prefix); "
                "assert importlib.util.find_spec('rich') is None",
            ],
            cwd=root,
            env=env,
            check=True,
        )
        fixture = root / "fixture"
        fixture.mkdir()
        (fixture / "example.py").write_text("answer = 42\nprint(answer)\n", encoding="utf-8")
        for name in ("slopscope", "count-lines-of-code"):
            command = scripts / (f"{name}.exe" if os.name == "nt" else name)
            subprocess.run(
                [str(command), "--help"], cwd=root, env=env, check=True, capture_output=True
            )
            result = subprocess.run(
                [str(command), "--engine", "python", "--format", "json", str(fixture)],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(result.stdout)
            assert report["engine"] == "python", report
            assert report["language_rows"] == [
                {"language": language, "files": 1, "blank": 0, "comment": 0, "code": 2}
                for language in ("Python", "SUM")
            ], report
        print(f"Installed commands passed: {artifact.name}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: smoke_distributions.py ARTIFACT [ARTIFACT ...]")
    for argument in sys.argv[1:]:
        smoke(Path(argument))
