"""Fixtures: an isolated Hermes home with the plugin enabled and a material folder of real files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import REPO, Host  # noqa: E402


@pytest.fixture
def host(tmp_path: Path) -> Host:
    home = tmp_path / "hermes-home"
    (home / "plugins").mkdir(parents=True)
    os.symlink(REPO, home / "plugins" / "submission-desk")
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - submission-desk\n", encoding="utf-8")
    return Host(home)


@pytest.fixture
def material(tmp_path: Path) -> Path:
    root = tmp_path / "material"
    root.mkdir()
    return root
