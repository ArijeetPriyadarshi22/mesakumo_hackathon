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
