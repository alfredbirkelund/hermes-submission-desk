"""Drive the plugin through the real Hermes host code in a fresh process.

Reads a JSON list of operations on stdin and prints one JSON object per operation on stdout.
Each invocation is a separate process, so two invocations are a host restart. HERMES_HOME must
point at an isolated test home whose config enables the plugin.

Operations:
  {"op": "plugins"}                                   loaded plugins (real discovery + register())
  {"op": "tool", "name": ..., "args": {...}}          registry.dispatch, the call path the agent uses
  {"op": "slash", "line": "status", "answers": [...]} the registered /submission-desk handler, with
       the host's ask_choice binding answered by a SIMULATED picker: each answer is a 0-based index
       into the offered labels, or null for "nobody can answer"; unbound when answers is absent
  {"op": "api", "method": "GET"|"POST", "path": "/workspaces", "json": {...}}
       the dashboard router mounted under /api/plugins/submission-desk like the host mounts it
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN = "submission-desk"


def main() -> int:
    ops = json.load(sys.stdin)
    from hermes_cli.plugins import get_plugin_command_handler, get_plugin_manager
    from tools.registry import registry

    manager = get_plugin_manager()
    manager.discover_and_load()
    client = None
    out = []
    for op in ops:
        kind = op["op"]
        if kind == "plugins":
            out.append({"plugins": [{k: p.get(k) for k in ("name", "key", "enabled", "loaded", "error", "tools", "commands")}
                                    for p in manager.list_plugins()]})
        elif kind == "tool":
            raw = registry.dispatch(op["name"], op.get("args", {}), scope=manager.scope_key,
                                    session_id=op.get("session_id", "test-session"), task_id="test-task")
            out.append({"result": json.loads(raw) if isinstance(raw, str) else raw})
        elif kind == "slash":
            from hermes_cli.plugin_command_dispatch import invoke_plugin_command

            handler = get_plugin_command_handler(PLUGIN)
            if handler is None:
                out.append({"error": "slash command not registered"})
                continue
            asked: list[dict] = []
            context = {"session_id": "test-session", "platform": op.get("platform", "cli"), "source": None}
            if "answers" in op:
                from hermes_cli.plugin_choices import bind_asker

                answers = list(op["answers"])

                def asker(question, labels, answers=answers, asked=asked):
                    asked.append({"question": question, "labels": labels})
                    if not answers:
                        return None
                    pick = answers.pop(0)
                    return None if pick is None else labels[pick]

                with bind_asker(asker):
                    text = invoke_plugin_command(handler, op.get("line", ""), **context)
            else:
                text = invoke_plugin_command(handler, op.get("line", ""), **context)
            out.append({"text": text, "asked": asked})
        elif kind == "api":
            if client is None:
                client = _client(manager)
            method = op.get("method", "GET").upper()
            response = client.request(method, f"/api/plugins/{PLUGIN}{op['path']}",
                                      json=op.get("json") if method != "GET" else None)
            try:
                body = response.json()
            except ValueError:
                body = response.text
            out.append({"status": response.status_code, "body": body})
        else:
            out.append({"error": f"unknown op {kind}"})
    json.dump(out, sys.stdout, default=str)
    return 0


def _client(manager):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    plugin_dir = None
    for plugin in manager.list_plugins():
        if plugin.get("name") == PLUGIN:
            plugin_dir = plugin.get("path") or plugin.get("dir")
    base = Path(plugin_dir) if plugin_dir else Path(__file__).resolve().parent.parent
    api_path = base / "dashboard" / "plugin_api.py"
    # Same import style as hermes_cli.web_server_dashboard._mount_plugin_api_routes.
    module_name = f"hermes_dashboard_plugin_{PLUGIN}"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router, prefix=f"/api/plugins/{PLUGIN}")
    return TestClient(app)


if __name__ == "__main__":
    raise SystemExit(main())
