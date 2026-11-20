# Mesakumo tender-to-proposal workspace

Local single-user InnovateU prototype. Documents → Requirements is implemented;
evidence matching, proposal generation and corporate export remain unavailable.
The separate synthetic sample contains hand-authored examples, never company facts.

## Setup, run, stop and test

Reuse the existing Python 3.11 environment:

```sh
cd /Users/arijeet/Documents/Mesakumo_hackathon
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m mesakumo
```

Open http://127.0.0.1:8501. Stop the server with **Ctrl+C**. Restart and open a saved
project to resume. No live model is required for upload or manual review.

```sh
.venv/bin/python -m unittest discover -s tests -v
# Optional actual socket/process integration check, synthetic data only:
.venv/bin/python tests/http_smoke.py
```

The app uses server-rendered HTML on Python's loopback HTTP server, typed dataclasses
and SQLite. Retaining the existing UI preserves tested workflow behaviour and
avoids a Streamlit rewrite. The only dependency is pypdf 6.21.4. DOCX processing
uses bounded ZIP/XML reading from Python's standard library.

## Use the Documents → Requirements workflow

1. Create a **document project** with a name and declared tender scope. Use a
   separate **synthetic sample** for the earlier demonstration fixture.
2. Upload a UTF-8 `.txt`, text-based `.pdf` or `.docx`, choosing tender, company
   knowledge, reusable text module or template. Maximum upload: 15 MB.
3. Inspect processing status and extracted passages in Documents. PDF locations
   are pages; DOCX locations are body paragraphs and headings (including table
   cell paragraphs), never invented pages; TXT locations are 20-line ranges.
4. In Requirements and evidence, add requirements manually using current tender
   source checkboxes, or request candidates from the explicitly configured model.
5. Inspect statements beside original excerpts. Correct classification, constraints
   and sources, add omitted obligations or delete candidates. Manual changes
   require a reviewer label and provenance note.
6. Mark the checklist reviewed after inspecting the full tender. This records human
   review, not proof that every obligation was found. Edits and new document
   versions reset checklist review and final approval. Historical sources must be
   corrected to current sources before re-review.
7. Refresh/restart and reopen the saved project. Failed model calls do not replace
   existing requirements. Repeat successful extractions **append** candidates;
   inspect and remove duplicates manually.

### Offline verification with a known fixture

Upload `tests/fixtures/synthetic_tender.txt` to a new document project. Expect one
passage at `Lines 1–5`. The known obligations are two workshops, the stated deadline,
two references completed since 2023, and PDF submission. Add and review these
manually without a model. These expected results are fixture ground truth, not
measured extraction accuracy. The tests use synthetic PDF and DOCX inputs too.

## Requesty EU / Claude configuration

`.env.example` lists settings. The app reads **process environment**, and does not
load `.env` automatically. Configure privately before starting:

- `MESAKUMO_LLM_PROVIDER=requesty`
- `MESAKUMO_LLM_APPROVED=yes`: set only after Mesakumo approves source use and the
  model/inference routing. This explicit setting enables requests from the UI.
- `MESAKUMO_LLM_BASE_URL=https://router.eu.requesty.ai/v1`
- `MESAKUMO_LLM_MODEL`: exact Mesakumo-approved EU Claude model/policy ID; no default.
- `MESAKUMO_LLM_API_KEY`: supplied Requesty key; never put it in tracked files.

The provider for this application is separate from the coding assistant's model.
Contract verified on 20 November 2026 against Requesty's official
[quickstart](https://docs.requesty.ai/quickstart) and
[EU routing documentation](https://docs.requesty.ai/features/eu-routing):
OpenAI-compatible POST `/chat/completions`, `Authorization: Bearer …`, messages,
model, and `choices[0].message.content`. EU gateway handling does **not** by itself
ensure EU inference: use Mesakumo's approved EU model or restricted routing policy.
The implementation rejects other base URLs and HTTP redirects. Provider calls
have 45-second socket timeouts, at most three attempts, and 1/2-second backoff
on rate limits, transient server errors or connection failures. Model errors
omit credentials, response bodies and source content. Requests are synchronous.

Extraction sends only current tender passages, with instructions to treat content
as untrusted data. Input is limited to **48 KB including the prompt**, with no
silent truncation; oversized tenders must be narrowed with explicitly declared
scope in a separate project. Output is capped at 8,000 tokens and 2 MB. Non-normal
completion, malformed JSON and unknown source IDs reject the whole result before
saving. Structural validity does not establish semantic correctness.

**No live provider request has been verified.** Credentials, approved model ID
and confirmation of permitted document use are still required.

## Persistence, modules and privacy

SQLite: `data/workspace.sqlite3`. Originals: `data/originals/<project>/<document>/`.
Original bytes are preserved with SHA-256 versions. Reuploading the same filename
and role retains the document ID; changed bytes create a retained version. Renaming
creates a new document. Only the latest tender version is used for extraction.
Failed latest tender versions block extraction/review until replaced by a readable
version. Failed uploads remain visible. There is no file-removal UI yet.

`MESAKUMO_DATA_DIR` optionally selects a data directory within the project root;
`MESAKUMO_PORT` defaults to 8501. Relative data paths resolve against this root.
Back up the entire data directory while stopped. Data, private documents, exports,
virtual environments and secrets are Git-ignored. No Git repository exists yet.
Review staged files before any future commit.

| Module | Responsibility |
| --- | --- |
| `mesakumo/ui/server.py`, `ui/intake.py` | HTTP forms, multipart upload, source inspection and checklist UI |
| `domain.py`, `workflow.py` | Typed records, review rules and version-bound approval |
| `persistence.py` | SQLite snapshots with stale-write protection |
| `documents.py` | Original retention, TXT/DOCX/PDF parsing and source locations |
| `requirements.py` | Candidate validation, manual editing and checklist review |
| `models.py` | Disabled adapter and Requesty EU boundary |
| `export.py` | Unimplemented replaceable export interface |

The local server has form tokens and stale-write protection, but no authentication,
TLS, multi-user support or encryption at rest. Keep it on loopback. Free section
edits clear source associations and invalidate section review. Earlier final
approval history remains recorded with fingerprints; full historical project
snapshots and export artifacts are not stored.

## Known limits and verification

Seventeen automated tests pass. A separate real-loopback integration check passed
upload → source inspection → manual checklist → review → disabled-provider error
→ process restart → reopen. Browser automation could not run because its host was
unavailable; visual verification is outstanding. Provider tests are mocks, not a
live Claude run. Use the numbered usage steps above for manual browser verification.

No OCR, legacy `.doc`, layout-preserving tables, DOCX headers/footers/text boxes,
image extraction, automatic DOCX list numbering or automatic completeness checks. A PDF with an empty page is
conservatively rejected, including possibly intentional blank pages. Textual
pages containing image-only obligations still need manual inspection. Templates
are retained but not parsed or rendered; no corporate template has been supplied.
No evidence matching, proposal generation, template export or submission is done.

Next: obtain permitted tender/model/template inputs, perform and inspect one live
extraction, then implement evidence review against company sources. See
PROJECT_STATUS.md and IMPLEMENTATION_LOG.md for the handoff.
# mesakumo_hackathon
