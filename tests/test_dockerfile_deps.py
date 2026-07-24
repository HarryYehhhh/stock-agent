"""Reproduce and verify the fix for a production-breaking Dockerfile dependency drift.

Root cause: `Dockerfile`'s `pip install` list had drifted from `pyproject.toml`'s
`[project].dependencies` — it installed the unused `google-genai` package (no usage
under `src/`) but never installed `groq`, even though `src/agent/agent.py` does a
top-level `from groq import Groq`. Any Lambda whose handler imports `agent.agent`
(processor, scheduler) crashed at import time in production with
`Runtime.ImportModuleError: No module named 'groq'`.

This test parses both the Dockerfile's `pip install` package list and pyproject.toml's
dependency list, and asserts they name the exact same packages with the exact same
version specifiers, so this drift can't silently reoccur.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _parse_dockerfile_pip_packages() -> set[str]:
    """Extract the quoted package specs from the Dockerfile's `pip install` RUN block."""
    text = DOCKERFILE.read_text()
    match = re.search(r"RUN pip install --no-cache-dir\s*\\\n(.*?)\n\n", text, re.DOTALL)
    assert match, "could not find `pip install --no-cache-dir` block in Dockerfile"
    return {
        spec
        for spec in re.findall(r'"([^"]+)"', match.group(1))
    }


def _parse_pyproject_dependencies() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    return set(data["project"]["dependencies"])


def test_dockerfile_pip_packages_match_pyproject_dependencies():
    dockerfile_packages = _parse_dockerfile_pip_packages()
    pyproject_packages = _parse_pyproject_dependencies()

    assert dockerfile_packages == pyproject_packages, (
        f"Dockerfile pip install list drifted from pyproject.toml dependencies.\n"
        f"Only in Dockerfile: {dockerfile_packages - pyproject_packages}\n"
        f"Only in pyproject.toml: {pyproject_packages - dockerfile_packages}"
    )
