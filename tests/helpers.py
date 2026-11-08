"""Test helpers shared by the test modules (imported by path via conftest's sys.path entry)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HERMES_ROOT = Path(os.environ.get("SD_HERMES_ROOT", Path.home() / ".hermes" / "hermes-agent"))
DRIVER = Path(__file__).resolve().parent / "host_driver.py"


class Host:
    """One isolated Hermes home. Every ``run`` is a separate process: a host restart."""

    def __init__(self, home: Path):
        self.home = home
        self.processes = 0

    def run(self, ops: list[dict]) -> list[dict]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HERMES_", "SUBMISSION_DESK"))}
        env.update(HERMES_HOME=str(self.home), PYTHONPATH=str(HERMES_ROOT), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run([sys.executable, str(DRIVER)], input=json.dumps(ops), capture_output=True,
                              text=True, env=env, timeout=240, check=False)
        self.processes += 1
        if proc.returncode != 0:
            raise AssertionError(f"driver failed ({proc.returncode}):\n{proc.stderr[-4000:]}")
        try:
            return json.loads(proc.stdout)
        except ValueError:
            raise AssertionError(f"driver printed non-JSON:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}") from None

    # Convenience wrappers; each is its own process unless batched with run().
    def tool(self, name: str, args: dict) -> dict:
        return self.run([{"op": "tool", "name": name, "args": args}])[0]["result"]

    def api(self, method: str, path: str, body: dict | None = None) -> dict:
        return self.run([{"op": "api", "method": method, "path": path, "json": body or {}}])[0]

    def slash(self, line: str, answers: list | None = None, platform: str = "cli") -> dict:
        op = {"op": "slash", "line": line, "platform": platform}
        if answers is not None:
            op["answers"] = answers
        return self.run([op])[0]


def ws_of(result: dict) -> str:
    return result["workspace"]["id"]


def item(readiness: dict, code: str) -> dict:
    for row in readiness["items"]:
        if row["code"] == code:
            return row
    raise KeyError(code)
