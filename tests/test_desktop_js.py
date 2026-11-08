"""Pure helpers in the Desktop page, run under Node without the Desktop shell.

The page is one ESM file that imports the Desktop SDK, so it cannot be imported here. Pure helper
functions are cut out of the source by name and evaluated on their own. Skipped without Node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_JS = Path(__file__).resolve().parent.parent / "desktop" / "plugin.js"
NODE = shutil.which("node")


def _function_source(name: str) -> str:
    text = PLUGIN_JS.read_text(encoding="utf-8")
    start = text.index(f"function {name}(")
    depth, i = 0, text.index("{", start)
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1


def _run(names: list[str], body: str):
    assert NODE is not None
    script = "\n".join(_function_source(n) for n in names) + "\n" + body
    proc = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_rest_errors_are_parsed_through_the_desktop_ipc_wrapper():
    body = json.dumps({"error": "stale_revision", "message": "This application changed since you loaded it (revision 5 -> 6).",
                       "hint": "Reload and try again.", "details": {"current_revision": 6}})
    cases = {
        # What Electron delivers for a rejected ipcMain.handle('hermes:api') call.
        "wrapped": f"Error invoking remote method 'hermes:api': Error: 409: {body}",
        # What preload rethrows for 404s, and what older or remote transports may deliver.
        "bare": f"409: {body}",
        "plain": "Error invoking remote method 'hermes:api': Error: connect ECONNREFUSED 127.0.0.1:9119",
        "text_body": "Error invoking remote method 'hermes:api': Error: 502: Bad Gateway",
    }
    out = _run(["parseError"], f"const cases = {json.dumps(cases)};\n"
               "const out = {}; for (const [k, v] of Object.entries(cases)) out[k] = parseError(new Error(v));\n"
               "console.log(JSON.stringify(out));")
    for key in ("wrapped", "bare"):
        assert out[key]["status"] == 409 and out[key]["code"] == "stale_revision", out[key]
        assert out[key]["message"].startswith("This application changed") and out[key]["hint"] == "Reload and try again."
        assert out[key]["details"] == {"current_revision": 6}
    assert out["plain"]["code"] == "network" and out["plain"]["message"] == "connect ECONNREFUSED 127.0.0.1:9119"
    assert out["text_body"]["status"] == 502 and out["text_body"]["message"] == "Bad Gateway"
    assert all("invoking remote method" not in v["message"] for v in out.values())
