# Current implementation state

Updated: 20 November 2026. Documents → Requirements milestone: **partial**;
implemented offline workflow and provider boundary, awaiting real inputs/live check.

Works: four stages; document-project creation; original retention and SHA-256
versions; TXT line ranges, PDF pages, DOCX body paragraphs/headings/table text;
visible failures; inspectable sources; structured candidate validation; manual
requirement add/edit/delete/classify with provenance; checklist review; SQLite
reopen; invalidation and final approval gates. Synthetic sample stays separate.

Stack: existing Python 3.11.9 `.venv`, standard-library HTTP/HTML, dataclasses,
SQLite, pypdf 6.21.4; standard-library ZIP/XML DOCX and urllib Requesty adapter.
Retain UI to preserve tested behaviour; no agent framework introduced.

```sh
cd /Users/arijeet/Documents/Mesakumo_hackathon
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m mesakumo
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/http_smoke.py
```

Open http://127.0.0.1:8501; stop with Ctrl+C. The implementation-session verification server was stopped; this port is free for your run. Data stays within the project root.

Configuration names: MESAKUMO_DATA_DIR, MESAKUMO_PORT, MESAKUMO_LLM_PROVIDER,
MESAKUMO_LLM_APPROVED, MESAKUMO_LLM_BASE_URL, MESAKUMO_LLM_MODEL,
MESAKUMO_LLM_API_KEY. `.env` is not auto-loaded. Default provider is disabled.
See README for the verified Requesty EU endpoint and private setup instructions.

Verification: 17 unittest checks pass; optional actual HTTP smoke check passes
including multipart intake, manual review, failed disabled call and process restart.
Browser host unavailable, no visual check. Requesty boundary tested with mocks;
no credentials/live calls or real-tender accuracy check. No Git repository/hash.

Missing: supplied tender/knowledge/text modules/template and approved Claude model
ID/key/data-use instructions. No OCR or full DOCX layout support. No automatic
chunking for >48 KB extraction input. No evidence search/review, proposal
generation, corporate export or full historical snapshots. Template constraints
remain unknown; a template-role upload is stored only. Renamed files create new
IDs; repeated extraction appends possible duplicates.

Next milestone and acceptance:
1. Close the live extraction gap: configure approved access, inspect supplied tender,
   run one authorised extraction, compare dates/quantities/mandatory conditions
   against its source and reopen the corrected reviewed checklist. Verify browser UI.
2. Then add evidence candidate review: permitted company passages shown alongside
   requirements; accept/reject/partial/no-match decisions persist and invalidate
   downstream review. Keep evidence quality separate from checklist review and
   coverage. Inspect corporate template before selecting export tooling.

Handoff prompt: Continue in this root. Read PRODUCT_PLAN.md, README.md,
PROJECT_STATUS.md and IMPLEMENTATION_LOG.md first. Preserve existing UI and state.
Rerun both test commands. Obtain approved Requesty EU Claude model ID/key and
permitted sample documents; finish live extraction and browser verification, then
implement evidence candidate review with visible supporting passages and honest
no-match outcomes. Do not build proposal generation/export before scoping the
supplied corporate template. Record remaining gaps accurately.
