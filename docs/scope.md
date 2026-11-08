# Submission Desk: approved scope (2026-11-08)

Approved by Alfred on 2026-11-08 ("build the plugin | Do all the recommended").
This file records the scope checkpoint so later decisions can be checked against it.

## Product contract

One person preparing one university, grant, scholarship or similar application gets an
inspectable answer to: what goes where, which file version satisfies which rule, and what
remains their own responsibility. The plugin checks local readiness. It never logs in, signs,
uploads, submits or predicts acceptance.

## Readiness loop

1. SELECT: material folder (on the Hermes backend host) and instruction sources (files or
   pasted portal text; capture date kept).
2. PROPOSE: the session agent quotes the source (the quote must be found in the stored
   snapshot), or the user adds requirements by hand.
3. CONFIRM: the user confirms or corrects (Desktop page or `/submission-desk`). The agent
   cannot.
4. MAP: requirement -> exact portal field label -> document (stable id + SHA-256 revision).
5. CHECK: rehash -> presence, format sniffing, exact bytes, PDF pages, word counts.
6. INSPECT: missing / failed / unknown / conflicting / stale / human-action, each with a reason
   and a next step -> correct -> recheck.
7. EXPORT: checklist.md + manifest.json, revalidated or labelled snapshot-only.

## Keep

- One application per workspace, explicit material root; files outside it refused (realpath,
  O_NOFOLLOW, inode check).
- Source snapshots (.txt/.md/.html/.pdf text and pasted text); excerpts located exactly or after
  whitespace/Unicode normalisation; capture date; "saved snapshot, not the live portal".
- Versioned requirements: proposed (agent) / confirmed or rejected (human); manual entry;
  explicit conflicts; deadlines need an explicit timezone.
- Portal fields as exact labels; versioned mappings; the same bytes in two fields keep
  separate reviews.
- Documents: stable id; revision = SHA-256 of the bytes; rename/move found by hash; duplicates
  flagged.
- Machine checks with measurement, limit, units, method and revision for every result.
- Human reviews per mapping + revision; human attestations labelled as attestations.
- Readiness derived on every read; no stored ready flag; append-only history.
- Surfaces: agent tools, `/submission-desk`, Desktop page, read-only CLI.

## Cut

- Plugin-owned model calls (ctx.llm); the session agent proposes instead.
- DOCX/ODT/image content checks, character limits, institution-specific word rules.
- Hooks and context injection.
- Packet copying, portal fetching, autofill, reminders, eligibility rulings, essay scoring,
  signatures, submission.
- Desktop-side file picking for remote backends.
- Bundled skill.

## Approved test seams

a. Agent tools through real Hermes plugin registration and tool dispatch.
b. Real files on disk, including generated encrypted/corrupt/scanned PDFs and byte edits with
   restored mtime.
c. Human surfaces: plugin_api.py router; slash command with the host's choice picker
   (answers simulated through the host asker binding are labelled simulated).
d. Readiness and export through tools, REST and CLI.
e. Host lifecycle: install, enable/disable, doctor, validate, restart, update, reinstall, purge.
f. Forged approval and hostile text through agent tools; real-model runs.
g. Rendered Desktop in an isolated instance.

## Decisions taken on the batched questions

1. Scope and seams approved as drafted.
2. Real-model runs: `with-secrets ANTHROPIC_API_KEY` in the isolated test home, about 20 runs.
3. Desktop: isolated instance (own HERMES_HOME, own Electron userData, own port), silent on a
   special Hyprland workspace, driven over CDP.
4. Independent review: fresh `hermes chat -q` reviewer processes in a separate reviewer home.
5. pypdf declared as a bounded Python dependency; MIT licence, copyright Alfred Birkelund.

## Changes after the first independent review

- The applicant chooses the material folder. A folder the agent picks is only proposed, and
  nothing in it is read until the applicant confirms that exact folder (the reviewer showed one
  agent tool call could open a workspace over `/etc`).
- Real-model runs: the Anthropic account had no credit, so the one model-assisted extraction run
  used OpenAI `gpt-6.1-sol` in the isolated home (see `docs/TEST-MATRIX.md`).
