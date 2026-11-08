#!/usr/bin/env python3
"""Mutation checks: prove that key tests fail when the behaviour they guard is broken.

The domain core was written before its behavioural tests, so "seen failing first" is shown
here instead: each mutation disables one safeguard, the named tests must FAIL, and the file is
restored afterwards (also on error). Run: .test-artifacts/venv/bin/python scripts/mutation_check.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (id, file, original text, mutated text, pytest selector)
MUTATIONS = [
    ("review ignores file bytes", "submission_desk/readiness.py",
     'elif latest.get("sha256") != sha:', 'elif False:',
     "tests/test_core_invariant.py::test_review_goes_stale_when_bytes_change_and_survives_restart"),
    ("review ignores field label version", "submission_desk/readiness.py",
     'latest["field_version_id"] != field["version_id"]', 'False',
     "tests/test_core_invariant.py::test_mapping_or_requirement_change_invalidates_review_without_erasing_history"),
    ("review ignores mapping version", "submission_desk/readiness.py",
     'if latest["mapping_version_id"] != current["id"]:', 'if False:',
     "tests/test_core_invariant.py::test_mapping_or_requirement_change_invalidates_review_without_erasing_history"),
    ("review ignores requirement version", "submission_desk/readiness.py",
     'latest["requirement_version_id"] != effective["id"]', 'False',
     "tests/test_core_invariant.py::test_mapping_or_requirement_change_invalidates_review_without_erasing_history"),
]


def main(selected: list[str]) -> int:
    failures = 0
    for ident, rel, old, new, selector in MUTATIONS:
        if selected and not any(s in ident for s in selected):
            continue
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"SKIPPED  {ident}: anchor not found exactly once in {rel}")
            failures += 1
            continue
        path.write_text(original.replace(old, new), encoding="utf-8")
        try:
            proc = subprocess.run([str(ROOT / "scripts" / "test.sh"), selector, "-x", "-q"], cwd=ROOT,
                                  capture_output=True, text=True, timeout=900)
        finally:
            path.write_text(original, encoding="utf-8")
        caught = proc.returncode != 0
        print(f"{'CAUGHT  ' if caught else 'MISSED  '} {ident}  ({selector.split('::')[-1]})")
        failures += 0 if caught else 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
