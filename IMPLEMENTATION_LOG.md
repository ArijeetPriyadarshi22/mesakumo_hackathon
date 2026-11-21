# Implementation history

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
