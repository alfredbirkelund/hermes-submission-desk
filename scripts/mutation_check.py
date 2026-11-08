#!/usr/bin/env python3
"""Mutation checks: prove that key tests fail when the behaviour they guard is broken.

The domain core was written before its behavioural tests, so "seen failing first" is shown
here instead: each mutation disables one safeguard, the named test must FAIL, and the file is
restored afterwards (also on error). Run:

    .test-artifacts/venv/bin/python scripts/mutation_check.py [substring-of-mutation-name ...]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T = "tests/"

# (name, file, original text, mutated text, pytest node id)
MUTATIONS = [
    ("review ignores file bytes", "submission_desk/readiness.py",
     'elif latest.get("sha256") != sha:', "elif False:",
     T + "test_core_invariant.py::test_review_goes_stale_when_bytes_change_and_survives_restart"),
    ("review ignores field label version", "submission_desk/readiness.py",
     'latest["field_version_id"] != field["version_id"]', "False",
     T + "test_core_invariant.py::test_mapping_or_requirement_change_invalidates_review_without_erasing_history"),
    ("review ignores mapping version", "submission_desk/readiness.py",
     'if latest["mapping_version_id"] != current["id"]:', "if False:",
     T + "test_core_invariant.py::test_mapping_or_requirement_change_invalidates_review_without_erasing_history"),
    ("review ignores requirement version", "submission_desk/readiness.py",
     'latest["requirement_version_id"] != effective["id"]', "False",
     T + "test_core_invariant.py::test_mapping_or_requirement_change_invalidates_review_without_erasing_history"),
    ("open follows a swapped parent directory", "submission_desk/files.py",
     "    if not _DIR_FD_SUPPORTED:\n", "    if True:\n",
     T + "test_files_identity.py::test_a_directory_swapped_for_a_symlink_mid_open_cannot_redirect_the_read"),
    ("no inode check after open", "submission_desk/files.py",
     "if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):", "if False:",
     T + "test_files_identity.py::test_a_file_swapped_while_it_is_opened_is_not_trusted"),
    ("symlinks resolving outside are followed", "submission_desk/files.py",
     '        return Problem(rel, "outside_root", "The path resolves outside the material folder (symlink escape).")\n',
     "        pass\n",
     T + "test_files_identity.py::test_paths_outside_the_material_folder_are_refused"),
    ("moves are not found by content", "submission_desk/service.py",
     "if len(candidates) == 1:", "if False:",
     T + "test_files_identity.py::test_a_moved_file_keeps_its_identity_and_review"),
    ("agent tools accept forged decision keys", "submission_desk/tools.py",
     "        _guard(name, args)\n", "        pass\n",
     T + "test_agent_boundary.py::test_forged_confirmation_fields_are_refused_and_nothing_is_stored"),
    ("agent proposals stored without verbatim evidence", "submission_desk/service.py",
     "require_evidence=not actor.is_human)", "require_evidence=False)",
     T + "test_agent_boundary.py::test_agent_proposals_need_verbatim_source_and_never_count_as_confirmed"),
    ("slash decision recorded without a picker answer", "submission_desk/commands.py",
     "        return ctx.ask_choice(question, choices)\n", "        return ctx.ask_choice(question, choices) or 'yes'\n",
     T + "test_agent_boundary.py::test_slash_decisions_need_a_real_answer_from_the_applicant"),
    ("review accepted for bytes the applicant did not see", "submission_desk/service.py",
     "            if read.sha256 != sha256:\n", "            if False:\n",
     T + "test_agent_boundary.py::test_review_is_bound_to_the_bytes_the_applicant_saw"),
    ("source drift ignored", "submission_desk/readiness.py",
     'if drift and not drift["found"]:', "if False:",
     T + "test_requirements_sources.py::test_source_change_after_confirmation_makes_the_requirement_stale"),
    ("deadline without time zone treated as known", "submission_desk/readiness.py",
     '        return [_reason(UNKNOWN, "deadline_timezone",', '        return [] or [_reason(READY, "deadline_timezone",',
     T + "test_requirements_sources.py::test_unclear_units_and_untimed_deadlines_are_not_guessed"),
    ("attestation not bound to file bytes", "submission_desk/readiness.py",
     'if obs.get("sha256") != att.get("sha256"):', "if False:",
     T + "test_actions.py::test_signature_action_is_proposed_confirmed_done_and_goes_stale_when_the_file_changes"),
    ("hand-verified check not bound to bytes", "submission_desk/readiness.py",
     'and att["requirement_version_id"] == requirement_version_id and att["sha256"] == sha',
     'and att["requirement_version_id"] == requirement_version_id',
     T + "test_actions.py::test_a_check_verified_by_hand_is_labelled_and_bound_to_the_bytes"),
    ("export not revalidated after writing", "submission_desk/export.py",
     "        changed = _revalidate(ws, manifest)\n", "        changed = []\n",
     T + "test_export.py::test_a_file_changed_during_export_makes_it_snapshot_only"),
    ("empty PDF page tree counted as zero pages", "submission_desk/checks.py",
     "    if not isinstance(pages, int) or pages <= 0:", "    if not isinstance(pages, int):",
     T + "test_checks.py::test_checks_match_the_independent_fixture"),
    ("unresolved word convention certified", "submission_desk/checks.py",
     '    if convention == "unresolved":\n        estimate_reasons.append(', '    if False:\n        estimate_reasons.append(',
     T + "test_checks.py::test_checks_match_the_independent_fixture"),
    ("ambiguous MB treated as binary", "submission_desk/checks.py",
     "    if size <= low:", "    if size <= high:",
     T + "test_checks.py::test_checks_match_the_independent_fixture"),
    ("stale window overwrites newer state", "submission_desk/service.py",
     '        if expected is not None and int(expected) != int(ws["revision"]):', "        if False:",
     T + "test_concurrency.py::test_a_stale_window_cannot_overwrite_a_newer_decision"),
    ("deletion without confirmation token", "submission_desk/service.py",
     "            if confirm_token != token:", "            if False:",
     T + "test_concurrency.py::test_deleting_an_application_needs_confirmation_and_touches_only_its_records"),
    ("missing PDF engine cached as the file's result", "submission_desk/checks.py",
     'transient=status in ("timeout", "crashed", "engine_unavailable")', 'transient=status in ("timeout", "crashed")',
     T + "test_checks.py::test_an_unknown_caused_by_the_environment_is_rechecked_once_the_engine_works"),
    ("cause of an unknown dropped from storage", "submission_desk/service.py",
     '"cause": r["cause"], ', "",
     T + "test_checks.py::test_the_cause_of_an_unknown_survives_storage_and_old_ledgers_upgrade"),
    ("new portal field created outside the mapping transaction", "submission_desk/service.py",
     "            if new_field:\n                # Resolve the requirement first", "            if False:\n                # Resolve the requirement first",
     T + "test_concurrency.py::test_mapping_to_a_new_portal_field_is_one_atomic_request"),
    ("Desktop IPC-wrapped REST errors shown raw", "desktop/plugin.js",
     ".replace(/^Error invoking remote method '[^']*':\\s*/, '')", "",
     T + "test_desktop_js.py::test_rest_errors_are_parsed_through_the_desktop_ipc_wrapper"),
    ("committed change not announced to open windows", "submission_desk/service.py",
     '        self._mark_changed(ws["id"], result["revision"])\n', "",
     T + "test_live_updates.py::test_each_committed_change_is_announced_once_after_commit_and_refusals_are_silent"),
    ("change announced before the transaction commits", "submission_desk/service.py",
     '        if changed is not None:\n            changed[workspace_id] = int(revision)\n',
     '        _announce(workspace_id, revision)\n',
     T + "test_live_updates.py::test_each_committed_change_is_announced_once_after_commit_and_refusals_are_silent"),
    ("gateway loaded into processes that have none", "submission_desk/service.py",
     '    if "tui_gateway.server" not in sys.modules:\n        return\n', "",
     T + "test_live_updates.py::test_a_process_without_a_gateway_announces_nothing_and_keeps_its_stdout"),
    ("Desktop page ignores backend change events", "desktop/plugin.js",
     "ctx.onEvent(LEDGER_EVENT, refresh)", "ctx.onEvent(LEDGER_EVENT, () => {})",
     T + "test_desktop_js.py::test_the_page_registers_and_refreshes_when_the_backend_announces_a_change"),
]


def main(selected: list[str]) -> int:
    failures = 0
    for name, rel, old, new, selector in MUTATIONS:
        if selected and not any(s in name for s in selected):
            continue
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"SKIPPED  {name}: anchor found {original.count(old)} times in {rel}")
            failures += 1
            continue
        path.write_text(original.replace(old, new), encoding="utf-8")
        try:
            proc = subprocess.run([str(ROOT / "scripts" / "test.sh"), selector, "-x", "-q"], cwd=ROOT,
                                  capture_output=True, text=True, timeout=900)
        finally:
            path.write_text(original, encoding="utf-8")
        caught = proc.returncode != 0
        print(f"{'CAUGHT  ' if caught else 'MISSED  '} {name}")
        sys.stdout.flush()
        failures += 0 if caught else 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
