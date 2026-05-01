from __future__ import annotations

import tomllib
from pathlib import Path


def test_pyproject_uses_spdx_license_string() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert payload["project"]["license"] == "MIT"
