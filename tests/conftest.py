"""Fixtures: an isolated Hermes home with the plugin enabled and a material folder of real files."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import REPO, Host  # noqa: E402

# What a release contains: everything except VCS data, build output and these test artifacts.
_NOT_SHIPPED = shutil.ignore_patterns(".git", ".test-artifacts", "__pycache__", "*.pyc", ".pytest_cache")


@pytest.fixture(scope="session")
def plugin_stage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the plugin, taken once per run, for the test homes to link to.

    Linking the homes to the repository itself would leave links back to the repository inside
    it (.test-artifacts), and anything that copies the plugin folder following links, such as
    `hermes plugins doctor`, would recurse through them until the disk is full.
    """
    stage = tmp_path_factory.mktemp("plugin-stage") / "submission-desk"
    shutil.copytree(REPO, stage, ignore=_NOT_SHIPPED)
    return stage


@pytest.fixture
def host(tmp_path: Path, plugin_stage: Path) -> Host:
    home = tmp_path / "hermes-home"
    (home / "plugins").mkdir(parents=True)
    os.symlink(plugin_stage, home / "plugins" / "submission-desk")
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - submission-desk\n", encoding="utf-8")
    return Host(home)


@pytest.fixture
def material(tmp_path: Path) -> Path:
    root = tmp_path / "material"
    root.mkdir()
    return root
