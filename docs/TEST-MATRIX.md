# Test matrix

What is verified, how, and what is not. Commands run from the repository root.

```bash
scripts/test.sh                                            # behaviour suite
.test-artifacts/venv/bin/python scripts/mutation_check.py  # safeguards must be load-bearing
hermes plugins validate /path/to/clean/checkout            # catalog admission gate (validate a clean
                                                           # copy: .test-artifacts/ holds a whole venv)
```

`scripts/test.sh` builds `.test-artifacts/venv` from the Hermes checkout's own `uv.lock` plus
`pypdf`, `pytest`, `reportlab` and `httpx`, and runs every host test in a separate process with
an isolated `HERMES_HOME`: the real plugin loader, tool registry dispatch, slash-command handler
and Desktop REST router. Nothing is installed into a Hermes install or profile. CI
(`.github/workflows/tests.yml`) runs the same suite and the mutation check against Hermes v0.23.0,
and a second job runs the catalog's own admission command, `hermes plugins validate
--install-deps`, on a clean checkout with Hermes prepared the way the catalog CI prepares it.

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
| Open Desktop windows refresh when the ledger changes; processes without a gateway announce nothing | `test_live_updates.py` (2), `test_desktop_js.py` | pass |
| **Independent review, round 1.** Instructions replaced by content that cannot be read as text make their requirements stale; an instruction file that cannot be read is reported on every read until it can | `test_honest_states.py` (2) | pass |
| A rule on a requirement kind that never evaluates it (a deadline on a document) is refused | `test_honest_states.py` | pass |
| A failed check cannot be verified by hand; a deadline cannot be settled by an attestation | `test_honest_states.py` (2) | pass |
| Rechecks and exports name the files they could not read and never claim to have verified them (*NOT CURRENT*) | `test_honest_states.py` | pass |
| Text that only mentions `%PDF-` is not a PDF | `test_honest_states.py` | pass |
| An agent revision of a withdrawn requirement waits for the applicant on the page, in `/submission-desk pending` and in the check tool, and can be confirmed there | `test_honest_states.py`, `test_desktop_contract.py` | pass |
| Nothing in a folder the agent chose is read until the applicant confirms that exact folder; only a person can confirm (picker or page) | `test_folder_choice.py` (4) | pass |
| Malformed numbers are client errors (422), not internal errors; a request key only deduplicates the same request on the same route | `test_desktop_contract.py` (2) | pass |
| Sources and the folder say who added or chose them, on the page and in the checklist | `test_desktop_contract.py` | pass |
| Untrusted input stays bounded: a 60,000-entry ZIP (< 4 MB allocated), 12 MB of HTML (< 60 MB), 12 MB of text (< 6 MB); names with edge spaces and `..data/` are addressed exactly | `test_untrusted_input.py` (5) | pass |
| A quote is located in a saved instruction revision once, not on every page poll | `test_untrusted_input.py` | pass |
| Replacing an environment-dependent result does not write the revision's final results again | `test_checks.py` | pass |
| Errors are real `Error` objects (host dialogs show their message); the web dashboard finds a bundle that points to Desktop | `test_desktop_js.py` (2) | pass |

Latest local run: **68 passed** in 258 s (Hermes checkout 2bd378e, v0.23.0 line, Python 3.14, Linux).
A checkout inside a hidden folder runs too: the tests then put their material folders in `/tmp`.

## Mutation check

`scripts/mutation_check.py` removes one safeguard at a time and runs the test that guards it; the
test must fail. It holds **51 mutations**: review binding (bytes, field label, mapping, requirement
version), symlink and swap defences, forged agent fields, verbatim evidence, picker answers,
source drift, untimed deadlines, attestation binding, export revalidation, empty page trees,
unresolved word conventions, ambiguous MB, stale windows, delete confirmation, engine-missing
caching, stored cause, atomic field creation, Desktop error parsing, live refresh, and the 21
safeguards added after the first independent review (listed in the script).

Latest run: **51 of 51 mutations caught** (commit 65732df, one run).

## Catalog and install gates

| Gate | Result |
|---|---|
| `hermes plugins validate` on a clean copy of the release tree (Hermes 0.23.0, without the dependency installed) | 16/16 checks, security scan **safe**, desktop surface and no-core-override pass, no warnings |
| The same validation with `pypdf` present (what CI's `--install-deps` produces) | 16/16 |
| `hermes plugins install file://<repo> --enable` and `hermes plugins update` into an isolated home | installed, install scan clean, dependency consent prompt shown |
| `node --check desktop/plugin.js` | parses |

## Live Desktop verification (manual)

Hermes Desktop 2026.9.7 (packaged build, Electron 40) with its backend from Hermes v0.23.0
source, in an isolated `HERMES_HOME`, driven through the real UI (Chrome DevTools Protocol, with
screenshots checked at each step; the screenshots are not part of the repository):

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

## Model-assisted extraction (one run)

`tests/extraction_eval/` holds a synthetic call for a grant that does not exist, with eight
requirements to find, an optional item, a distractor and a hidden instruction addressed to the
model, plus a scorer that reads the ledger read-only. One run in Hermes Desktop with a live model
(OpenAI `gpt-6.1-sol`, isolated home, about USD 0.10): 9 proposals, **8 of 8 expected requirements
found, none invented**, the distractor ignored, the optional item proposed as optional, 10 of 10
rule checks right (an unresolved "2 MB", an unstated word convention and a deadline without a
time zone all left unresolved), the hidden note not quoted, and no decision recorded by the agent.
One run is an example, not a benchmark.

## Not tested

- More than one model run (above), or other models.
- Desktop builds 2026.9.21 and later. The build used has the route bug fixed upstream in
  2026.9.21 ([issue #109063](https://github.com/NousResearch/hermes-agent/issues/109063)); the
  page was opened as a split tile and, after a restart, in the main view.
- macOS and Windows, remote backends, and Hermes' package manager admitting `pypdf` into a real
  profile's environment (that changes the machine's Hermes install, so it was not run here).
