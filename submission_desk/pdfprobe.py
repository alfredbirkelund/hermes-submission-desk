"""PDF inspection in a separate, time- and memory-limited Python process.

Untrusted PDFs can make parsers loop or allocate without bound. Parsing happens in a child
interpreter (the same Python that runs Hermes) with a CPU and address-space limit, killed on a
wall-clock timeout. The parent only ever sees a small JSON result. A failure is reported as a
failure: it never becomes "0 pages" or "0 words".

Run directly as a script; it imports nothing from the plugin package.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROBE_TIMEOUT_S = 25
MAX_TEXT_CHARS = 2_000_000
_SCRIPT = Path(__file__).resolve()


def run_probe(data: bytes, *, want_text: bool, timeout: float = PROBE_TIMEOUT_S) -> dict:
    """Inspect PDF bytes. Returns a dict with ``status`` in
    ok | encrypted_locked | parse_error | engine_unavailable | timeout | crashed."""
    env = {
        "SD_PROBE_PATH": json.dumps([p for p in sys.path if p and os.path.isdir(p)]),
        "SD_PROBE_TEXT": "1" if want_text else "0",
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(_SCRIPT)],
            input=data,
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "detail": f"PDF inspection exceeded {timeout:.0f} s and was stopped."}
    except OSError as exc:
        return {"status": "crashed", "detail": f"Could not start the PDF inspector: {exc}"}
    try:
        result = json.loads(proc.stdout.decode("utf-8") or "{}")
    except ValueError:
        result = {}
    if proc.returncode != 0 or not isinstance(result, dict) or "status" not in result:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        return {"status": "crashed", "detail": f"PDF inspector exited with code {proc.returncode}. {tail[0][:200]}".strip()}
    return result


def _limit_resources() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (20, 22))
        two_gb = 2 * 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (two_gb, two_gb))
    except Exception:
        pass


def _main() -> int:
    _limit_resources()
    extra = json.loads(os.environ.get("SD_PROBE_PATH", "[]") or "[]")
    sys.path[:0] = [p for p in extra if isinstance(p, str)]
    want_text = os.environ.get("SD_PROBE_TEXT") == "1"
    data = sys.stdin.buffer.read()
    try:
        import io

        import pypdf
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - depends on host environment
        print(json.dumps({"status": "engine_unavailable", "detail": f"pypdf is not installed ({type(exc).__name__})."}))
        return 0
    out: dict = {"engine": f"pypdf {getattr(pypdf, '__version__', '?')}"}
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        encrypted = bool(reader.is_encrypted)
        out["encrypted"] = encrypted
        if encrypted:
            try:
                opened = bool(reader.decrypt(""))
            except Exception as exc:
                opened = False
                out["decrypt_error"] = type(exc).__name__
            out["opens_without_password"] = opened
            if not opened:
                out["status"] = "encrypted_locked"
                out["detail"] = "Encrypted with a password; pages and text cannot be read."
                print(json.dumps(out))
                return 0
        pages = len(reader.pages)
        out["pages"] = pages
        if want_text:
            texts: list[str] = []
            total = 0
            truncated = False
            for page in reader.pages:
                try:
                    text = page.extract_text() or ""
                except Exception as exc:
                    out.setdefault("page_errors", []).append(type(exc).__name__)
                    text = ""
                if total + len(text) > MAX_TEXT_CHARS:
                    text = text[: max(0, MAX_TEXT_CHARS - total)]
                    truncated = True
                texts.append(text)
                total += len(text)
                if truncated:
                    break
            out["texts"] = texts
            out["text_truncated"] = truncated
        out["status"] = "ok"
    except Exception as exc:
        out["status"] = "parse_error"
        out["detail"] = f"{type(exc).__name__}: {str(exc)[:240]}"
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
