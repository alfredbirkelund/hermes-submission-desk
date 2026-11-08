"""Fixtures: an isolated Hermes home with the plugin enabled and a material folder of real files."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import REPO, Host  # noqa: E402

sys.path.insert(0, str(REPO))
from submission_desk import files  # noqa: E402

# pytest.ini puts the base temp directory in .test-artifacts/, which a fresh clone does not have yet.
(REPO / ".test-artifacts").mkdir(exist_ok=True)

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


def _accepted_as_material(parent: Path) -> bool:
    probe = parent / "sd-material-probe"
    probe.mkdir(parents=True, exist_ok=True)
    try:
        files.resolve_root(str(probe))
        return True
    except ValueError:
        return False
    finally:
        probe.rmdir()


@pytest.fixture(scope="session")
def material_base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Where the tests' material folders live.

    The plugin refuses folders inside hidden directories of the home folder, so a checkout under one
    (for example ~/.cache/...) gets its material folders in the system temp directory instead.
    """
    base = tmp_path_factory.mktemp("material")
    if _accepted_as_material(base):
        yield base
        return
    for parent in ("/tmp", "/var/tmp"):
        if os.path.isdir(parent) and os.access(parent, os.W_OK):
            alt = Path(tempfile.mkdtemp(prefix="sd-material-", dir=parent))
            if _accepted_as_material(alt):
                yield alt
                shutil.rmtree(alt, ignore_errors=True)
                return
            shutil.rmtree(alt, ignore_errors=True)
    pytest.exit(f"No usable place for test material folders: {base} is inside a hidden directory of your home, which "
                "Submission Desk refuses, and /tmp is not usable. Pass --basetemp=<an ordinary folder>.", returncode=2)


@pytest.fixture
def material(material_base: Path, request: pytest.FixtureRequest) -> Path:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", request.node.name)[:40]
    return Path(tempfile.mkdtemp(prefix=name + "-", dir=material_base))
