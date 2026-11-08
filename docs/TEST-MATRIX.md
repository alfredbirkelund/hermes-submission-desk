# Test matrix

What is verified, how, and what is not. Commands run from the repository root.

```bash
scripts/test.sh                                            # behaviour suite
.test-artifacts/venv/bin/python scripts/mutation_check.py  # safeguards must be load-bearing
hermes plugins validate /path/to/clean/checkout            # catalog admission gate
```

`scripts/test.sh` builds `.test-artifacts/venv` from the Hermes checkout's own `uv.lock` plus
`pypdf`, `pytest`, `reportlab` and `httpx`, and runs every host test in a separate process with
an isolated `HERMES_HOME`: the real plugin loader, tool registry dispatch, slash-command handler
and Desktop REST router. Nothing is installed into a Hermes install or profile. CI
(`.github/workflows/tests.yml`) runs the same suite and the mutation check against Hermes v0.23.0.

## Automated gates

| Gate | Exercised by | Result |
|---|---|---|
| A review goes stale when the file's bytes change, and stays stale after a restart | `test_core_invariant.py` (2) | pass |
| Changing a requirement, its field label or its mapping makes earlier reviews stale; history is kept | `test_core_invariant.py` | pass |
| Agent tools cannot confirm, review, attest or resolve; forged decision fields are refused and nothing is stored | `test_agent_boundary.py` (5) | pass |
| Agent proposals need a verbatim quote from a saved source; revisions of confirmed requirements wait for the applicant | `test_agent_boundary.py` | pass |
| Slash-command decisions need a real answer from Hermes' picker; unattended runs record nothing | `test_agent_boundary.py` | pass |
| A review covers exactly the bytes the applicant saw | `test_agent_boundary.py` | pass |
| Checks match an independent expected-results fixture: real format from content, size with decimal/binary MB, PDF pages, two word conventions, encryption, empty page tree, scans without text | `test_checks.py` + `tests/fixtures/expected_results.json` | pass |
| Page counts agree with poppler's `pdfinfo` | `test_checks.py` (skipped when poppler is absent) | pass |
| A missing PDF engine is *not checked* with a concrete fix, is not cached for the file revision, and its cause survives storage and a schema upgrade | `test_checks.py` (3) | pass |
| File identity is the SHA-256 of the bytes: moved files keep their review, same name with new bytes is a new revision, one file in two fields keeps two reviews | `test_files_identity.py` | pass |
| Reads stay inside the material folder: `..`, absolute paths and symlinks out are refused; unsafe roots (home, hidden folders, the Hermes home) are refused | `test_files_identity.py` | pass |
| A file swapped while it is opened, or a directory swapped for a symlink between the check and the open, is not trusted | `test_files_identity.py` (2) | pass |
| A saved instruction that changes after confirmation makes the requirement stale | `test_requirements_sources.py` | pass |
| Conflicting instructions stay visible until the applicant resolves them; both sides must be quoted | `test_requirements_sources.py` | pass |
| Ambiguous units are refused; a deadline without a time zone stays unknown | `test_requirements_sources.py` | pass |
| Manual entry works without any model and is marked unsourced | `test_requirements_sources.py` | pass |
| Signatures and eligibility statements are the applicant's attestations, bound to file bytes where relevant; hand-verified checks are labelled and bound | `test_actions.py` (3) | pass |
| Export names the checked revisions, renders hostile text inertly, says SNAPSHOT ONLY when a file changes during export, and leaves no partial output when interrupted | `test_export.py` (3) | pass |
| Retried requests apply once; a stale window cannot overwrite a newer decision; mapping to a new portal field is one atomic request | `test_concurrency.py` | pass |
| Profiles keep separate ledgers; a failed save leaves no partial state; a killed recheck leaves a consistent ledger; deletion needs confirmation and touches only that application | `test_concurrency.py` | pass |
| Desktop REST errors are parsed through Electron's IPC wrapper (stale-revision and delete-confirmation flows depend on it) | `test_desktop_js.py` (Node) | pass |

Latest local run: **39 passed** (Hermes checkout 2bd378e, v0.23.0 line, Python 3.14, Linux).

## Mutation check

`scripts/mutation_check.py` removes one safeguard at a time and runs the test that guards it; the
test must fail. Latest run: **26 of 26 mutations caught**, including review binding (bytes, field
label, mapping, requirement version), symlink and swap defences, forged agent fields, verbatim
evidence, picker answers, source drift, untimed deadlines, attestation binding, export
revalidation, empty page trees, unresolved word conventions, ambiguous MB, stale windows,
delete confirmation, engine-missing caching, stored cause, atomic field creation and Desktop
error parsing.

## Catalog and install gates

| Gate | Result |
|---|---|
| `hermes plugins validate` on a clean `git archive` of HEAD (Hermes 0.23.0, without the dependency installed) | 16/16 checks, security scan **safe**, desktop surface and no-core-override pass |
| The same validation with `pypdf` present (what CI's `--install-deps` produces) | 16/16 |
| `hermes plugins install file://<repo> --enable` and `hermes plugins update` into an isolated home | installed, install scan clean, dependency consent prompt shown |
| `node --check desktop/plugin.js` | parses |

## Live Desktop verification (manual, recorded with screenshots)

Hermes Desktop 2026.9.7 (packaged build, Electron 40) with its backend from Hermes v0.23.0
source, in an isolated `HERMES_HOME`, driven through the real UI:

- enabled the Desktop half under Settings → Plugins; the page appears in the sidebar;
- created a workspace; refused a folder inside a hidden home directory;
- saved three instruction copies (HTML call, pasted-portal text, older FAQ);
- added a requirement by hand with a verbatim quote; mapped it to a new portal field and file
  (found and fixed: creating the field and mapping in two requests made the second stale);
- without `pypdf` in the backend, pages showed *not checked* with the fix (found and fixed: that
  result was cached for the file revision and survived installing the engine);
- with `pypdf`: pages counted, reviewed the revision, then edited the file: the page check failed
  and the review went stale, naming both revisions;
- marked a signature action done (attestation); verified a word count by hand;
- agent proposals (five requirements, four fields, four mappings, one conflict) arrived through
  the real tool registry with the agent actor; confirming each activated its mapping and
  rechecked; resolved the conflict; exported the checklist and manifest; deleted a second
  workspace through the confirmation dialog (files on disk untouched).

## Not tested

- A full chat session with a live model driving the tools inside Desktop (the isolated home has
  no model provider). The agent path is covered through the real tool registry with the agent
  actor, which is what a model's tool calls reach.
- Desktop builds 2026.9.21 and later. The build used has the route bug fixed upstream in
  2026.9.21 ([issue #109063](https://github.com/NousResearch/hermes-agent/issues/109063)); the
  page was opened as a split tile and, after a restart, in the main view.
- macOS and Windows, remote backends, and Hermes' package manager admitting `pypdf` into a real
  profile's environment (that changes the machine's Hermes install, so it was not run here).
