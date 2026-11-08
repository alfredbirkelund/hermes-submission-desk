"""Pure helpers in the Desktop page, run under Node without the Desktop shell.

The page is one ESM file that imports the Desktop SDK, so it cannot be imported here. Pure helper
functions are cut out of the source by name and evaluated on their own. Skipped without Node.
"""

from __future__ import annotations

import json
import re
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


# Areas as the Desktop SDK defines them (window.__HERMES_PLUGIN_SDK__ in Desktop 2026.9.7).
SDK_CONSTANTS = {"ROUTES_AREA": "routes", "SIDEBAR_NAV_AREA": "sidebar.nav", "PALETTE_AREA": "palette"}


def _load_plugin(tmp_path: Path, ctx_has_on_event: bool) -> dict:
    """Run the real plugin.js register() against stand-ins for the Desktop SDK and React."""
    source = PLUGIN_JS.read_text(encoding="utf-8")
    block = re.search(r"import \{([^}]*)\} from '@hermes/plugin-sdk'", source)
    assert block, "plugin.js must import from @hermes/plugin-sdk"
    names = [n.strip() for n in block.group(1).split(",") if n.strip()]
    lines = ["export const calls = { invalidate: [], hostEvents: [] }"]
    for name in names:
        if name in SDK_CONSTANTS:
            lines.append(f"export const {name} = {json.dumps(SDK_CONSTANTS[name])}")
        elif name == "host":
            lines.append("export const host = { navigate() {}, onEvent(type, fn) { calls.hostEvents.push([type, fn]); return () => {} } }")
        elif name == "queryClient":
            lines.append("export const queryClient = { invalidateQueries(arg) { calls.invalidate.push(arg) } }")
        else:
            lines.append(f"export const {name} = () => null")
    sdk = tmp_path / "node_modules" / "@hermes" / "plugin-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({"name": "@hermes/plugin-sdk", "type": "module", "exports": "./index.js"}))
    (sdk / "index.js").write_text("\n".join(lines) + "\n")
    react = tmp_path / "node_modules" / "react"
    react.mkdir(parents=True)
    (react / "package.json").write_text(json.dumps({"name": "react", "type": "module", "exports": "./index.js"}))
    (react / "index.js").write_text(
        "const noop = () => null\nexport default { createElement: noop }\n"
        "export const useCallback = noop, useEffect = noop, useMemo = noop, useRef = noop, useState = noop\n")
    (tmp_path / "plugin.mjs").write_text(source, encoding="utf-8")
    (tmp_path / "run.mjs").write_text(
        "import plugin from './plugin.mjs'\n"
        "import { calls } from '@hermes/plugin-sdk'\n"
        "const reg = { items: [], events: [] }\n"
        "const ctx = { rest: async () => ({}), storage: {}, onDispose() {}, registerMany(items) { reg.items.push(...items) } }\n"
        f"if ({'true' if ctx_has_on_event else 'false'}) ctx.onEvent = (type, fn) => {{ reg.events.push([type, fn]) }}\n"
        "plugin.register(ctx)\n"
        "const all = [...reg.events, ...calls.hostEvents]\n"
        "for (const [type, fn] of all) if (type === 'plugin.submission-desk.ledger.changed') fn({ payload: { workspace: 'w1', revision: 4 } })\n"
        "console.log(JSON.stringify({ id: plugin.id, items: reg.items.map(i => ({ id: i.id, area: i.area, path: i.data && i.data.path })),\n"
        "  ctxEvents: reg.events.map(e => e[0]), hostEvents: calls.hostEvents.map(e => e[0]), invalidated: calls.invalidate }))\n")
    proc = subprocess.run([NODE, "run.mjs"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("ctx_has_on_event", [True, False], ids=["ctx.onEvent", "older Desktop: host.onEvent"])
def test_the_page_registers_and_refreshes_when_the_backend_announces_a_change(tmp_path, ctx_has_on_event):
    out = _load_plugin(tmp_path, ctx_has_on_event)
    assert out["id"] == "submission-desk"
    assert {(i["area"], i.get("path")) for i in out["items"]} >= {("routes", "/submission-desk"), ("sidebar.nav", "/submission-desk")}
    subscribed = out["ctxEvents"] if ctx_has_on_event else out["hostEvents"]
    assert subscribed == ["plugin.submission-desk.ledger.changed"], out
    if ctx_has_on_event:
        assert out["hostEvents"] == []  # one subscription, tracked by the plugin context
    assert out["invalidated"] == [{"queryKey": ["submission-desk"]}], out  # every query of this plugin refetches
