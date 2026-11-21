# Implementation history

## Page 2 milestone C — 21 November 2026 — complete

- Page 2 now displays one reviewed row per approved requirement, the six
  requested columns, and Gap / next action. Reviewers can inspect exact,
  separate tender and historical evidence excerpts, change the fit and notes,
  and edit citations within the selected corpus.
- The review and selection persist in the workspace. SQLite commits the
  approved snapshot and UTF-8 evidence CSV together. Page 3 checks the
  current Page 1 approval, review revision, selection and captured file hashes.
  Incomplete rows block approval. Genuine No match and file coverage gaps
  can be acknowledged once review is complete.
- Scripted verification covered citation validation, exact excerpts, edit and
  removal, CSV download bytes and row parity, separate Page 1 CSV, restart with
  persistence enabled, rollback and retry, handoff and invalidation after
  review, selection, evidence file version and tender source changes.
  All 62 tests and the HTTP smoke test passed.
- The actual reference deck was read locally for slide citations. Real tender
  and historical evidence were not sent to the provider for this milestone.
  Visual browser QA and real tender grading remain unverified.
  Page 3 only acknowledges receipt of the approved evidence snapshot.

## Page 2 milestone B — 21 November 2026 — complete

- Start Evidence Matching and explicit Retry each capture the approved project,
  phase, revision, original requirement rows, and selected document versions.
  Results stay within the current UI session and are cleared when the approval
  or selected paths change. A retry captures a new snapshot of those files.
- LangGraph runs a bounded search and judgment loop per requirement with one
  configured Requesty model instance per run. The local lexical index searches
  requirement text and exact constraints. The model may refine the query or
  read adjacent passages of an already retrieved source. Each requirement
  gets at most four model actions, including the final judgment.
- Action validation restricts citations to retrieved selected passages. Python
  attaches the original text, filename, location, role and document version.
  The UI preserves the tender requirement and its original citations and
  displays evidence fit, missing criteria, current actions and the tool trace.
  Provider failures, unprocessed rows and unreadable files are visible gaps.
- Scripted checks: all three grades, refined search, nearby text, invalid and
  unselected citations, historical proposals, current signatures, unreadable
  evidence, provider failure, skipped work, the action limit, and explicit UI
  retry. All 62 automated tests and the HTTP smoke test passed.
- Live synthetic check: the configured Requesty EU model judged two synthetic
  requirements in a complete run. It returned Strong match with a valid source
  for a delivered audit and No match with a current-action flag and no citation
  for a new bid signature. Only synthetic content was used in the live check.
- Remaining work: assess semantic grading on real tender evidence, add OCR
  for textless sources, and carry reviewed evidence into proposal drafting.
  UI behavior was verified with Streamlit AppTest.

## Page 2 milestone A — 21 November 2026 — complete

- Evidence Matching uses `ChecklistReview.current()` and displays the saved approval's
  project, phase, revision and requirement IDs. The existing Continue to Evidence
  Matching button takes the reviewer there after Approve and Save. Missing or stale
  approvals lock Page 2 and discard any preview.
- The inventory picker includes 19 eligible files in three roles: Previous project
  sources, Team information and Historical proposal. Buyer inputs and blank forms,
  credentials and templates are excluded. Historical bids retain their role. Paths
  are resolved beneath the participant directory using Unicode normalization.
- Start Evidence Matching reads only the chosen files and shows per-file status
  and a local source preview. PPTX citations use the order in the presentation,
  including when a slide has no text or its XML filename is out of sequence.
- Verification: all 57 automated tests passed, including four focused tests for the
  approval and CSV handoff, stale source gate, selection controls, path resolution,
  file statuses and PPTX citations. The legacy HTTP smoke test passed. The actual
  reference deck yielded 64 text passages, with citations verified for slides 3,
  15 and 65. Selecting all actual inventory entries locally yielded 16 readable
  files and three PDFs flagged for pages without extractable text.
- Remaining work: requirement-to-evidence matching and grading, and OCR for
  scanned or otherwise textless pages. This milestone makes no model calls.
  Streamlit AppTest covered the UI flow. Visual browser QA was not run.

## Foundation baseline verification — 20 November 2026 — partial

This entry is a retrospective record, not a claim of a prior independent browser
check. At this milestone's start, inspected the existing README, plan, configuration,
source and tests. `.venv/bin/python -m unittest discover -s tests -v` independently
passed all **7 existing tests**. Existing typed records, SQLite persistence,
four HTTP stages, synthetic fixture and manual draft/review rules were present.
Live provider, parsing, matching, generation and export were previously absent.
No Git repository/hash available. Previous browser operation was unverified.

## Documents → Requirements — 20 November 2026 — partial

### Implemented behaviour

Document projects accept 15 MB TXT/PDF/DOCX uploads with tender, knowledge, text
module or template roles. Originals are retained with stable IDs per filename/role
and content-hash versions. Latest tender versions drive extraction; failed versions
are visible and block analysis. Source passages retain document version and TXT
line ranges, PDF pages, or DOCX body paragraph/heading locations. No invented DOCX
pagination. Templates are stored only. Synthetic sample records remain separate.

Requirements can be added, edited, classified, deleted and marked reviewed with
visible sources and manual provenance. Edits/new versions invalidate checklist and
downstream review. Historical sources prevent re-review until corrected. Provider
results append atomically only after JSON/schema/source-ID checks; unknown sources,
failed calls and oversized contexts preserve saved corrections. Input limit is
48 KB including prompt; output limit 8,000 tokens. No silent truncation.

Requesty EU adapter uses environment settings, an explicit approval switch, no
model default, no redirects, 45-second socket timeouts, bounded 3-attempt retries,
and sanitized errors. No SDK or orchestration framework. Structural validity is
explicitly distinguished from semantic correctness.

### Files and decisions

Changed: `mesakumo/domain.py`, `documents.py`, `models.py`, `requirements.py`,
`workflow.py`, `ui/server.py`, `ui/intake.py`, `.env.example`, `pyproject.toml`,
`requirements.txt`, tests and `tests/fixtures/synthetic_tender.txt`, README and plan.
Created this log and PROJECT_STATUS.md.

Reused existing Python 3.11.9 environment. Installed pypdf 6.21.4 for PDF extraction;
DOCX ZIP/XML and TXT use standard library. Kept server-rendered HTTP UI: adding
forms preserved existing behaviour with less migration than Streamlit. No agents.
Backward-compatible dataclass defaults allow existing saved fixtures to load.

Official provider references checked on this date:
- https://docs.requesty.ai/quickstart — Bearer auth, POST chat completions, response shape.
- https://docs.requesty.ai/features/eu-routing — EU base URL and separate inference-region requirement.
No model ID was guessed or selected. Approved routing remains a supplied input.

### Checks and observed results

- Before changes: `.venv/bin/python -m unittest discover -s tests -v` — 7 passed.
- Dependency setup: `.venv/bin/python -m pip install 'pypdf>=5,<7'` — installed 6.21.4;
  pinned resulting version in both dependency files.
- After changes: `.venv/bin/python -m unittest discover -s tests -v` — **17 passed**.
  Covers original retention/versioning, TXT/PDF/DOCX source mapping, empty/corrupt/
  scanned failure, invalid source IDs, failure preservation, context rejection,
  manual review/reopen/invalidation, historical-source rejection, Requesty HTTP
  contract/retry and sanitized failures, plus all previous foundation checks.
- `.venv/bin/python tests/http_smoke.py` — **passed** actual loopback HTTP creation,
  multipart upload, source inspection, manual addition, checklist review, disabled
  call failure and server process restart/reopen. Uses labelled synthetic input,
  temporary data removed afterward; no model network call.
- `.venv/bin/python -m mesakumo` — started on loopback port 8501 outside sandbox
  socket restrictions; `curl --noproxy '*' --max-time 5 ...` — HTTP 200.
- Browser automation attempt — browser access host unavailable. No visual browser
  check performed. README supplies precise manual steps.
- Live provider — **not run**. HTTP contract tests use mocked responses only.
- No Git repository exists; commit hash unavailable.

### Remaining work / blockers

Milestone stays partial until a permitted tender and approved Requesty access are
available for an inspected live extraction. No real company inputs or corporate
format were supplied. Template inspection/export constraints remain open. No
matching, proposal generation, corporate rendering, OCR, automatic context batching
or duplicate consolidation. DOCX only includes body paragraphs/table cell text;
headers/footers/text boxes/images and layout must be inspected separately. PDF
empty pages conservatively fail the entire document. Human review never guarantees
extraction completeness. Full version snapshots and an edit-diff audit are absent.

Next handoff: see PROJECT_STATUS.md. Finish live/browser verification with approved
inputs, then build evidence review against permitted company sources; inspect the
actual corporate template before choosing export libraries.

Final reconciliation: reran all 17 tests and the real-loopback smoke after final source-display changes; both passed. `.venv/bin/python -m pip check` reported `No broken requirements found`. Preserved the synthetic partial-evidence display. DOCX tabs/line breaks are retained; automatic list numbering and layout are not reconstructed.

Stopped the implementation-session verification server after checking HTTP startup; port 8501 is available for the user run.

## 2026-11-21 — Task 1: Streamlit shell and local discovery

Implemented four sidebar pages with Tender Requirements as default. Other pages
report actual status. The legacy `python -m mesakumo` path and existing projects
remain intact. Startup discovers only top-level Ausschreibung directories under
MESAKUMO_PARTICIPANT_DIR (default local participant directory). LocalProcessor
reads TXT/PDF/DOCX; unsupported and failed inputs remain explicit. CSV seeds roles;
actual files are authoritative. Corrected AIDF to buyer submission form, its PDF
to readable schedule, and pack 2 guidance to internal hackathon guidance. Guidance
uses the existing knowledge role so it cannot become tender extraction input.
Pack 3 participation keeps loose sources; offer adds bounded ZIP members, with
identical bytes mapped to one source and visible aliases. IDs hash tender/phase
and relative source path; reopening/rescanning retains SQLite projects and versions.
No model adapter is imported by discovery. Reuse domain, persistence, parser and
invalidation rather than replacing validation, Requesty or approval rules.

Inspected clean git status and found no applicable AGENTS.md. Read exact local
files `mesakumo_Input für Teilnehmer/mesakumo Hackathon Uni-Ulm_Challenge.docx`
and `mesakumo_Input für Teilnehmer/mesakumo-kontext-fuer-agenten.md`.
Participant files, including credentials, were already tracked: removed 117 files
from the index with `git rm -r --cached -- 'mesakumo_Input für Teilnehmer'`, kept
on disk and ignored. Existing Git history was not rewritten.

Changed: mesakumo/discovery.py, mesakumo/streamlit_app.py,
tests/test_discovery.py, requirements.txt, pyproject.toml, .gitignore, README.md,
PROJECT_STATUS.md, IMPLEMENTATION_LOG.md; staged participant index removals.

Checks and results:
- `.venv/bin/python -m unittest discover -s tests -v`: 19 passed, including
  synthetic phase/alias/idempotence checks and Streamlit AppTest default/navigation.
  Initial missing dependency and default 3-second AppTest timeout were resolved by
  installing pinned Streamlit and allowing 30 seconds. Final suite passes.
- `.venv/bin/python tests/http_smoke.py`: PASS, actual loopback HTTP using synthetic
  inputs, including legacy entry point, upload, review, disabled model, restart.
- `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.headless true --server.address 127.0.0.1 --server.port 8511 --browser.gatherUsageStats false`:
  launched successfully. Sandbox blocked initial socket binds; rerun with escalation
  succeeded. No browser visual QA performed.
- Actual-document Python checks using temporary ProjectStore, packs(), and
  open_selection() for all selections with urllib.request.urlopen patched to fail:
  3 packs; 4 distinct projects; repeat project dictionaries unchanged. Pack 1:
  20 sources, 15 readable/3 unsupported/2 unreadable. Pack 2: 9 readable. Pack 3:
  participation 15 readable plus archive; offer 22 distinct sources (21 readable,
  1 unsupported), 2 identical aliases plus archive. All actual paths and ZIP members
  matched CSV entries. Pack 1 PDFs failing conservative parser remain explicit.
- Actual-document AppTest (60-second timeout, data/acceptance_streamlit, networking
  blocked): PASS default title, 3 pack options, all packs, offer phase, rescan and
  all four pages. Path-valued selectbox caused an AppTest serialization failure;
  changed widget values to names and reran successfully.
- `git diff --check`: PASS.

No live Requesty calls were made. Existing provider unit tests use mocks. No
extraction accuracy, OCR, XLSX/AIDF parsing or visual layout verification claimed.
No later tasks implemented. Retained source versions are historical records;
renames have new identities. Streamlit requirements editing is outside Task 1;
existing editing remains available via the preserved CLI interface.

## 2026-11-21 — Task 2: LangGraph requirements extraction agent

Implemented one Claude agent with LangGraph StateGraph, conditional action routing
and three application tools: list_tender_documents, read_tender_passages and
submit_requirement_candidates. Reused RequestyModel.complete and validate_candidates;
no native provider function/tool support is claimed. The model returns strictly
validated JSON actions and selects its follow-up reads, including attachments.
Known IDs scope reads to the active tender/phase snapshot and current source versions.
Internal Bearbeitungshinweis guidance is excluded from both listings and reads.

Reads return bounded source fragments (1,500 characters each, approximately 14 KB
serialized per tool result), preserving original passage IDs and next offsets.
The full prompt is checked below the existing 48,000-byte limit with a 1 KB system
message reserve. Run bound: 40 model actions, plus existing transport retries.
Processed/read batches persist separately; zero-requirement submissions are valid.
Citations must refer to passages actually read. Conservative wording/kind/constraint
matches merge and union citations; different quantities/wording remain separate.
Similar differing candidates are flagged for human review as possible conflicts.

SQLite extraction_runs stores source-snapshot runs and node checkpoints. An OS
file lock prevents concurrent rerun/tab execution. Automatic execution occurs once
on page 1 when configured; terminal runs restore; failures and interrupted runs
need explicit retry. Source changes produce a new snapshot. Final checklist changes
and run results commit in one SQLite transaction with revision protection.
Streamlit shows per-document progress during extraction, sourced candidate checklist,
coverage gaps (including unsupported documents), and tool trace with passage IDs.
Existing CLI entry point, projects, validation and approval behavior remain intact.

As explicitly authorized, configure_team_one reads the private Team 1 credential
file into process memory when no provider is set. Default approved model is
bedrock/claude-haiku-4-5@eu-west-1. Explicit provider environment settings take
precedence, including disabled mode. Credentials were not copied into code/logs/DB.
Initial live probe hit Python CA trust failure. Added certifi roots alongside system
roots in Requesty HTTPSHandler, preserving certificate verification and redirect
rejection. Both documented models subsequently returned HTTP 403, so no live agent
action/extraction or native tool support was verified. No response bodies or keys
were logged. This is an external access blocker, not a passing live model test.

Changed files for Task 2: mesakumo/agent.py, mesakumo/models.py,
mesakumo/streamlit_app.py, tests/test_agent.py, requirements.txt, pyproject.toml,
README.md, PROJECT_STATUS.md, IMPLEMENTATION_LOG.md. Preserved Task 1 changes and
staged participant-material index removals. No applicable AGENTS.md found in repo.
LangGraph API checked against official StateGraph reference:
https://reference.langchain.com/python/langgraph/graph/state/StateGraph

Exact checks/results:
- `.venv/bin/python -m unittest discover -s tests -v`: 29 tests PASS (19 prior,
  10 new). New tests are synthetic/scripted, require no Requesty key, and prove
  specification read → model-chosen pricing attachment read → valid attachment
  citation; reject unread/foreign citations and unread processed batches; accept
  zero requirements; report partial coverage/action bounds; validate long-passage
  bounded reads/revisits; preserve distinct obligations/flag possible conflicts;
  test locking, interrupted retries, and Streamlit reopen/retry call counts.
- `.venv/bin/python -m unittest discover -s tests -p test_agent.py -v`:
  agent-only offline acceptance passes (rerun after final test addition).
- `.venv/bin/python tests/http_smoke.py`: PASS, synthetic inputs through actual
  legacy loopback server, including restart/reopen and disabled-provider failure.
- `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.headless true --server.address 127.0.0.1 --server.port 8511 --browser.gatherUsageStats false`:
  server launched successfully. AppTest verifies page 1 behavior; no visual browser QA.
- Actual-document local check with temporary ProjectStore, open_selection(),
  sources() and DisabledModel: PASS all four pack/phase selections. Buyer scopes:
  pack 1 20 docs/531 passages/576 fragments; pack 2 8 docs/326 passages/364 fragments
  (internal guidance excluded); pack 3 participation 15/269/297, offer 22/321/355.
  No model called and no extraction accuracy claimed on these actual documents.
- Live Requesty JSON action probe using configured Team 1 key, minimal prompt and
  no tender contents: Haiku HTTP 403; Sonnet HTTP 403. Initial CA failure resolved.
- `git diff --check`: PASS (existing CSV CRLF normalization warning only).

Remaining gaps: live model access; semantic completeness/accuracy evaluation;
heuristic rather than comprehensive contradiction detection; conservative merges
leave paraphrases for human review. Partial runs restore as-is; failed retries
start a bounded fresh attempt while preserving already accepted checklist records.
No new OCR, XLSX/AIDF reader, evidence matching, draft generation or export.
Stopped after Task 2. Test Streamlit server stopped after launch verification.

## 2026-11-21 — Follow-up: simplify Requesty setup to match Team 1 guide

Replaced manual urllib request construction/retry parsing with OpenAI SDK client
and client.chat.completions.create(), matching the provided access document.
Added direct REQUESTY_API_KEY support (document's variable), while preserving
legacy MESAKUMO settings and the previously authorized local document fallback.
An explicit key skips document parsing; explicit disabled/approval flags are
respected. Secrets stay outside tracked files. Endpoint restriction, no redirects,
45-second timeout, two retries, finish/content validation and sanitized errors
remain. SDK owns retry timing and auth/JSON transport. Response size is checked
after SDK decoding; it is no longer a hard streaming byte cap. This tradeoff is
recorded in README. Agent JSON actions and persistent runs are unchanged.

Changed: mesakumo/models.py, tests/test_documents_requirements.py, requirements.txt,
pyproject.toml, .env.example, README.md, PROJECT_STATUS.md, IMPLEMENTATION_LOG.md.
Added openai==2.8.1; tests use HTTPX MockTransport to verify actual SDK requests,
retry counts, sanitized failures, redirects, unfinished responses and documented
environment name. Official SDK example checked at https://github.com/openai/openai-python.

Checks:
- `.venv/bin/python -m unittest discover -s tests -v`: 31 PASS; synthetic fixtures,
  scripted agent, SDK transport mocks and Streamlit AppTest. No Requesty key needed.
- `.venv/bin/python tests/http_smoke.py`: PASS synthetic actual-loopback legacy
  workflow including process restart/reopen and disabled model failure.
- Live minimal SDK probe with authorized local Team 1 key: Requesty HTTP 403.
  No credentials/response bodies logged; no live extraction success claimed.
- `git diff --check`: PASS. Actual-document parsing/scoping was not repeated for
  this transport-only follow-up; prior local checks remain documented above.

Manual check/launch: `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false`.
Set REQUESTY_API_KEY privately as in the supplied guide, open page 1, choose a pack,
retry a saved failure if present, and refresh to confirm restoration/no extra calls.
Remaining blocker: Requesty gateway/model access returns HTTP 403. No Task 3 work.

## 2026-11-21 — Diagnose and fix Requesty HTTP 403

Found a local configuration bug, superseding the earlier gateway-access diagnosis:
configure_team_one's regex accepted only alphanumeric/underscore/hyphen characters,
truncating the actual key at a plus sign and dropping the remaining encoded value.
Replaced regex extraction with shlex parsing of the guide's complete quoted export
assignment. The full value (including +, / or =) reaches the SDK unchanged. Added a
synthetic regression test for full-key preservation and explicit environment precedence.
No credential values were printed, changed or copied into tracked files.

The inherited process still has no REQUESTY_API_KEY. User's ignored .env.example
contains an assignment, but the app does not load that file, and it has provider
set to disabled and approval no. Left these private settings untouched. Corrected
document fallback now provides working access without depending on that file.

Live minimal Haiku probe now succeeds (normal stop). A second actual agent-prompt
probe revealed a JSON Markdown fence around an otherwise valid action. Added exact
single-fence unwrapping before existing strict JSON/tool/citation validation; no
arbitrary explanatory text is accepted. Repeated live initial action probe PASS:
list_tender_documents, offset 0. No tender contents sent in these checks. Native
provider tool calls and full real-document extraction are still not verified.

Changed: mesakumo/models.py, mesakumo/agent.py,
tests/test_documents_requirements.py, tests/test_agent.py, README.md,
PROJECT_STATUS.md, IMPLEMENTATION_LOG.md.

Exact checks/results:
- `.venv/bin/python -m unittest discover -s tests -v`: 33 PASS. Synthetic/scripted
  tests include complete special-character keys, precedence, fenced actions,
  malformed text rejection and Streamlit rerun/retry behavior.
- `.venv/bin/python tests/http_smoke.py`: PASS legacy actual-loopback integration
  with synthetic fixture, disabled provider, persistence and restart.
- Live SDK minimal synthetic prompt using full document-loaded key: SUCCESS,
  model bedrock/claude-haiku-4-5@eu-west-1, finish_reason stop.
- Live RequestyModel.complete with real agent protocol and no source documents:
  PASS initial list_tender_documents JSON action after exact fence unwrapping.
- `git diff --check -- mesakumo/models.py mesakumo/agent.py tests/test_documents_requirements.py tests/test_agent.py README.md PROJECT_STATUS.md IMPLEMENTATION_LOG.md`: PASS.

Restart Streamlit with `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false`.
Manual check: open page 1, click Retry failed extraction for an existing failure,
inspect trace/coverage and refresh to confirm no new run. No later task implemented.
Actual-document parser/scoping checks were not repeated for this configuration fix.

## 2026-11-21 — Task 3: complete Tender Requirements review

Inspected current code, git status and repository instructions before editing;
no applicable AGENTS.md found. Preserved existing uncommitted Task 1/2/follow-up
work and private participant/credential files. Reused domain requirements,
save/delete/review helpers, checklist invalidation, SQLite project persistence
and revision-based stale-write checks.

Implemented page-1 saved checklist plus requirement editor: stable ID, statement,
category, obligation level, exact constraint lines and selected source references.
Reviewers can edit/add/remove with reviewer identity and human provenance. The
source view includes filename, exact parser page/paragraph/line range, source
version, passage ID and original excerpt. Only active buyer passages in the current
phase are selectable/citable; internal partner guidance remains excluded. Gaps and
unsupported/unreadable documents appear beside the checklist. Old rows default to
Unspecified obligation level; default omission in fingerprints preserves legacy
fingerprints. Explicit classification changes still invalidate approval.

New ChecklistReview service persists immutable approval snapshots and exact UTF-8
CSV bytes in checklist_approvals. CSV includes tender, phase, revision, reviewer,
requirement IDs, edited fields, provenance and structured citations (source filename,
location, passage/document/version IDs, original excerpts). CSV generation precedes
mutation of stored state; approved project snapshot and CSV commit in one transaction.
Generation/SQLite failures leave no new approval; UI explains retry recovery.
Repeated submissions resolve to the same current approval; stale writes are rejected.
Snapshot/fingerprint/source-snapshot checks plus CSV integrity gate page 2.

Added Approve and Save with explicit gap/completeness acknowledgment and current
CSV download. Individual requirement forms must be saved before approval; the saved
table is the exported truth. Gaps may be explicitly acknowledged without discarding
citation validation. Existing legacy review defaults still block failed sources.
Checklist approval is distinct from final proposal approval. Evidence Matching
receives/displays the exact approved checklist revision; otherwise it shows a locked
state. Editing invalidates current progression; historical approval/CSV records remain
stored. Evidence retrieval, draft generation and corporate export remain unimplemented.

Changed Task 3 files: mesakumo/domain.py, mesakumo/requirements.py,
mesakumo/checklist.py, mesakumo/ui/checklist.py, mesakumo/streamlit_app.py,
tests/test_checklist.py, README.md, PROJECT_STATUS.md, IMPLEMENTATION_LOG.md.
No new dependency or credential changes.

Exact checks and results:
- `.venv/bin/python -m unittest discover -s tests -v`: 39 PASS (33 prior + 6 review
  tests). Initial Streamlit compilation typo was fixed before final passing runs.
- `.venv/bin/python -m unittest discover -s tests -p test_checklist.py -v`: PASS
  review acceptance; final test file includes six checks. Synthetic Streamlit flow
  edits one candidate, adds a cited manual requirement, removes another, injects
  CSV generation failure, confirms no approval/download, retries successfully,
  retrieves bytes registered at the actual Streamlit download URL and compares to
  the SQLite CSV and approved rows. Fresh AppTest session restores approval/download;
  page 2 receives the approved revision; a saved edit disables progression and locks
  direct page-2 access. Backend tests cover idempotence, old revisions, source scope,
  preserved legacy obligation/fingerprint defaults, CSV generation failure and SQLite
  trigger-induced transaction rollback/recovery. No model/key required.
- `.venv/bin/python tests/http_smoke.py`: PASS preserved CLI/actual-loopback HTTP,
  synthetic upload, manual review, disabled call, process restart/reopen.
- `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.headless true --server.address 127.0.0.1 --server.port 8511 --browser.gatherUsageStats false`:
  successful headless launch; test server stopped afterward. UI checks use AppTest,
  not visual browser QA.
- Local actual-document checks used packs(), open_selection(), sources() and
  ChecklistReview with temporary SQLite across Ausschreibung 1, Ausschreibung 2 -
  mit Partner, and Ausschreibung 3 participation/offer: PASS current source citation
  mapping, UTF-8 CSV, atomic approval and fresh-store reopen. Checklist rows were
  synthetic linkage checks citing actual passages; not extraction accuracy claims.
  No participant originals modified; temporary verification DB removed.
- `git diff --check`: PASS.

No live Requesty calls were made for Task 3. Prior corrected-key Haiku completion
and initial JSON action remain the only live provider verifications; full actual
tender extraction/review accuracy remains unverified. Remaining limits: manual
completeness review is required; legacy unsupported formats/OCR gaps remain; CSV
uses JSON arrays in citation/constraint cells; historical snapshots have no separate
history browser. No evidence retrieval or later-task work implemented.

Launch: `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false`.
Manual test: edit one row, add a cited manual row, remove another, Approve and Save,
download and compare CSV, restart/reopen, inspect page-2 approved revision, then
save another edit and confirm fresh approval is required. Stopped after Task 3.

## 2026-11-21 — Follow-up: diagnose saved extraction error and clean page 1

Inspected user screenshot, current code and git status. Screenshot message originates
in agent.py's broad decide() exception handler, which conflated provider errors with
malformed/unsupported JSON. Local SQLite has two saved failed attempts at zero actions;
these old records contain no specific underlying exception. Their root cause cannot
be reconstructed. Refresh restores saved failures by design. Kept records intact.

Split diagnostics into sanitized Requesty failure, adapter failure, invalid JSON and
unsupported action cases. No raw model output, keys or private document contents are
included in diagnostics. A fresh live initial JSON-action probe passes with the current
corrected key, using only the protocol/minimal synthetic state and no tender contents.
This does not establish successful full-document extraction or identify the exact old
failure. UI explains old generic failures and directs explicit retry.

Cleaned Tender Requirements: removed the initial giant inventory, hundreds of source
expanders and duplicate progress/coverage tables; put the checklist first, full width.
Explicit schema keeps columns visible on an empty table (previous [] data had none).
Statement is first; readable source filenames/locations replace raw IDs in the summary.
Stable IDs remain in the table/editor. Collapsed edit/add, original-source, coverage,
document inventory and trace sections preserve all functionality without clutter.
Removed the narrow side column and redundant internal IDs/path text from the main view.
Kept review revision, approval/export failure recovery and page-2 gates unchanged.

Changed: mesakumo/agent.py, mesakumo/streamlit_app.py, mesakumo/ui/checklist.py,
tests/test_agent.py, tests/test_discovery.py, README.md, PROJECT_STATUS.md,
IMPLEMENTATION_LOG.md. Preserved prior uncommitted work; no participant/credential edits.

Checks/results:
- `.venv/bin/python -m unittest discover -s tests -v`: 40 PASS. New scripted diagnostic
  test distinguishes provider HTTP 403 from invalid JSON. Existing review CSV/restart/
  invalidation acceptance passes. Empty-page AppTest asserts six explicit checklist
  columns and a bounded number of expanders. These are synthetic/local tests.
- `.venv/bin/python tests/http_smoke.py`: PASS preserved synthetic actual-loopback
  legacy workflow, upload/review, disabled call, restart/reopen.
- `MESAKUMO_LLM_PROVIDER=disabled .venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.headless true --server.address 127.0.0.1 --server.port 8511 --browser.gatherUsageStats false`:
  launch succeeds. Attempted visual browser inspection with CUA; concrete blocker:
  “The browser access host is unavailable.” No visual screenshot QA claimed. Test
  server stopped after verification.
- Live RequestyModel.complete(PROTOCOL + minimal synthetic state), parse_action():
  PASS list_tender_documents offset 0. No tender text sent; full extraction not run.
- `git diff --check`: PASS.

Launch: `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false`.
Manual check: restart, open page 1, confirm checklist headers appear immediately,
expand edit/source/details as needed, and use Retry failed extraction to replace a
saved failure with a fresh attempt/specific diagnostic. No later-task implementation.
