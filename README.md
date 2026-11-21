## Reopen Tender 2 for local testing

Run `.venv/bin/python -m mesakumo.resume_tender2` and open
`http://127.0.0.1:8505`. The regular Streamlit app opens Tender 2 with its
saved checklist and evidence review. Choose **Evidence Matching** to see
the saved Page 2 rows. The first run creates an independent workspace in
`data/recovered-tender-2`. Later runs reopen that copy and retain your edits.

The launcher uses saved data and disables model calls by default. Add
`--enable-model` if you want to run matching manually with the usual model
configuration. Saved incomplete evidence still needs review and approval
before it can reach Proposal Draft. Use `--source path/to/workspace.sqlite3
--workspace data/another-test-copy` to load a different saved session into a
new copy.

---

## Pages 3 and 4 — Proposal Draft and PowerPoint export

On **Proposal Draft**, generate missing sections or write and save a section
directly. **Continue to Final Review & Export** saves the current sections as a
versioned package. Open items and omitted requirements stay visible without
requiring separate confirmations. Sections previously declined stay out until
edited or regenerated.

Select **Generate PowerPoint**, then **Download PowerPoint**. The renderer
uses the participant folder's `Angebotstemplate.pptx` and calls no model.
The deck retains open-item markers, complete text across continuation slides,
and available source references in speaker notes. CSV and JSON remain
available under **Other downloads**. SQLite saves the rendered bytes so the
same version can be downloaded after reopening. Edit a section on Page 3 and
continue again to export the updated content. The original template is
preserved.

---

## Page 2 milestone C — evidence review and approval

From Evidence Matching, choose **Read evidence and its sources** to inspect
the exact tender and historical evidence excerpts separately. Use **Save
evidence review** to correct the fit, edit evidence notes or gap/action notes,
and add or remove citations from the selected corpus. Strong and Partial
matches require a citation. No match requires a gap note and approval
acknowledgment. Failed and unprocessed rows stay visible and block approval
until resolved or rerun.

**Approve and Save Evidence** stores the reviewed snapshot and its UTF-8 CSV
in one SQLite transaction. The evidence CSV has one row per requirement,
the six review columns, stable requirement IDs, checklist and review revisions,
gap/action notes and distinct structured tender and evidence citations with
document IDs, locations, excerpts and versions. The Page 1 checklist CSV
remains a separate download.

Page 3 can confirm receipt of a current approved evidence snapshot. Edits,
selection changes, selected file version changes and stale checklist
approvals block the handoff until the corresponding current review is approved.
Use `MESAKUMO_KEEP_SESSION=yes` to reopen the same workspace after restart.
With the default fresh-session setting, a new browser session opens a new
workspace.

Verification used synthetic tender and evidence inputs with a scripted model.
Streamlit AppTest covered citation edits, approval, stored download bytes,
CSV-to-table comparison, reopen, acknowledgment, a simulated SQLite write
failure, and invalidation. The local reference deck slide-citation check
also passed. No real tender matching call or visual browser QA was performed
for this milestone. Proposal generation remains future work.

---

# Tender isolation, readable results and complete source scheduling

Start extraction is beside Rescan. The selected tender/phase appears above the
results and inside the loading dialog. The overview shows Requirement, Category
and Source document/location. Search filters the overview; **Read requirement and
its sources** shows the full statement, obligation classification, constraints,
original supporting excerpts and complete original passages with wrapping text.
Numbers in source filenames identify documents; the selected tender heading
identifies the tender pack.

New browser sessions start with a fresh workspace, including when
MESAKUMO_DATA_DIR is configured. Old workspace.sqlite3 results are not restored.
Within a browser session, each tender/phase has a separate project. The optional
MESAKUMO_KEEP_SESSION=yes setting restores the durable review workflow.
Existing saved work has not been deleted.

The UI now uses CoverageRuns, which schedules every current readable source batch,
rather than allowing the model to finish early or stop at 40 actions. Numbered
excerpts are supplied to the model; the application derives passage IDs, source
versions, locations and original quotations from the selected evidence IDs.
Requesty receives a strict JSON schema containing the permitted categories and
current evidence IDs. Validation failures retry with diagnostics; dense groups
split automatically if provider output is truncated. Failed batches remain gaps.
Interrupted runs can resume their validated batches within the same source snapshot.
Model candidates are replaced on a fresh run; human edits are retained.

Document processing now includes XLSX sheet/row/cell references, AIDF data.lv
references, Windows-encoded text, PDFs readable with an empty password, and DOCX
headers/footers/notes. Parser versions invalidate old source snapshots. The local
format audit found readable passages for every tender document across all four
pack/phase selections. Scanned/password-protected/corrupt files still report gaps.

Live verification on Ausschreibung 2 - mit Partner processed all **379 batches**
from **341 passages in 8 documents**, saved **428 candidates**, and left **zero gaps**.
Every one of **478 citations** resolved to the selected tender and every retained
quotation matched the original passage. See data/coverage_audit_v2/audit.json.
Source coverage and citation integrity do not certify semantic completeness:
human review is still required to catch omitted, misunderstood or duplicated
obligations. Five distributed candidate/source pairs were also spot-checked.

52 automated tests and the legacy HTTP smoke pass, including tender switching,
fresh browser sessions, source offsets, invalid evidence rejection, adaptive group
splitting, interruption/resume, source formats, review and approval/export behavior.
Browser visual inspection remains unverified because Safari inspection timed out.

The sections below describe earlier implementations and historical checks.

---

# Sidebar extraction controls and diagnostics (21 November 2026)

**Rescan** and **Start extraction** now sit side by side beneath the tender selector.
Start explicitly runs the selected tender, including retrying a failed attempt,
and opens Tender Requirements if invoked from another page. A non-dismissible
loading dialog shows progress during the run.

A live diagnostic reproduced Claude returning explanatory prose followed by one
fenced JSON action. The parser now accepts that unambiguous format; multiple JSON
blocks and invalid tool arguments/citations are still rejected. The prompt also
lists citation IDs already read and distinguishes passage IDs from batch IDs.
Validation errors identify the row and failing field.

Each new run appends timestamped JSON events to `data/extraction.jsonl` (or beside
the configured workspace database), with project, snapshot, attempt, stage, tool,
step, coverage counts and error. Prompts, raw model replies, source text and API
keys are excluded. Saved failures keep their original diagnostics until retried.

Validation: 42 automated tests pass. A live Requesty check using synthetic tender
text completed in three actions and saved two requirements. Authorized live verification on Ausschreibung 1 saved 58 requirements in 17 actions
without parsing or citation failures. Coverage is partial: 170 processed batches,
406 unprocessed batches and five documents without readable passages. The UI
explicitly warns about incomplete coverage. Results are saved in the main workspace.
Safari inspection timed out, so the visual layout has not been browser-verified.

---

# Start extraction explicitly (21 November 2026)

Select a tender pack (and phase where available), then click **Start extraction**.
Opening the app, switching tenders and editing the checklist do not call the model.
Saved failures show **Retry failed extraction**; click it to run a fresh attempt
with the current key loader and detailed diagnostics. Completed/partial results
remain available without running again. A disabled provider shows a configuration
message only when extraction is requested.

The screenshot's generic error is a saved historical failure; its original cause
was not recorded. It remains visible until explicitly retried. Existing work is
preserved, and a full live tender extraction has not been verified by this change.

---

# Checklist layout and failure diagnostics (21 November 2026)

Page 1 now opens on a full-width requirements table with explicit column headers,
even when extraction has produced no rows. Statements come first; source references
show filenames and locations. Edit/add forms, source excerpts, document inventory,
coverage details and execution traces are collapsed until needed. Approval and
CSV download remain beneath the checklist.

The old message “Model request failed or returned an invalid JSON action” came from
a catch-all in agent.py. Existing saved failures cannot reveal their original cause;
refreshing restores them without another request. The updated handler distinguishes
Requesty errors, malformed JSON and unsupported actions. Open **Why extraction
stopped** for details and use **Retry failed extraction** for a fresh attempt.

A fresh minimal live first-action check passes using the corrected key. It does not
verify a full tender extraction. All 40 tests and the legacy HTTP smoke pass. Browser
access was unavailable, so visual QA could not be completed; Streamlit AppTest covers
the table headers, review flow, approval/download and retry behavior.

Launch and manual check:

```sh
.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Restart the app, open Tender Requirements, confirm the checklist headers are visible,
expand only the information you need, then retry a saved failure to see a fresh
specific diagnostic. Existing projects, failed runs and approvals are preserved.

---

# Task 3: Tender Requirements review (21 November 2026)

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Open http://127.0.0.1:8501. Page 1 now supports the complete checklist review:

1. Select a tender/phase. Review extraction gaps beside the saved checklist.
2. Choose a stable requirement ID in **Requirement to edit**. Edit its statement,
   category, obligation level, exact constraints and source references. Enter a
   reviewer and human provenance note, then **Save requirement**. Select **Add new
   requirement** for a manual row, or use **Remove requirement** for an existing row.
3. Use **Original source view** to inspect the filename, page/paragraph/line range,
   source version and unchanged excerpt. Only current buyer passages in the selected
   phase can be cited; internal partner guidance is excluded.
4. Save each individual edit first. Enter **Approving reviewer**, acknowledge gaps
   and manual completeness review, then choose **Approve and Save**. A download
   appears only after the approved snapshot and CSV have both committed to SQLite.
5. Download the UTF-8 CSV and compare its rows to the saved checklist. It contains
   tender, phase, approved revision, stable IDs, edited fields, human provenance
   and JSON arrays for exact constraints and complete citations (filename, location,
   passage/version IDs, original excerpt).
6. **Continue to Evidence Matching** becomes available. Page 2 displays the exact
   approved revision/snapshot it received; evidence retrieval is a later task.

Approval and CSV bytes survive restarts. Saving any edit/add/remove or changing
sources invalidates the current handoff. Page 2 blocks access until fresh approval;
old snapshots stay retained in SQLite. Approval of the checklist is separate from
final proposal approval. Unknown obligation levels default to **Unspecified**;
reviewers should classify them from the source rather than assuming mandatory.

CSV generation happens before the transaction. If generation or saving fails,
no approval is committed: the page shows an error and **Approve and Save** can be
retried. Repeated approval submissions return the existing snapshot. Stale writes
from another tab are rejected. Gaps, including unsupported files, require explicit
acknowledgment; approval does not certify that extraction found every obligation.

Manual acceptance: edit one candidate, add a cited manual row, remove another,
approve and download. Compare IDs, statements, constraints and citations with the
table. Restart and check the download and received page-2 revision remain. Return
to page 1, save another edit and confirm Evidence Matching is locked again.

```sh
.venv/bin/python -m unittest discover -s tests -p test_checklist.py -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/http_smoke.py
```

Task 3 verification: 39 automated tests pass, including Streamlit acceptance and
exact download-payload comparison, CSV failure/recovery, database rollback, restart,
revision invalidation and stale writes. Legacy HTTP smoke passes. Local actual-pack
checks confirm source-linked CSV/approval/reopen across all four pack/phase selections
using synthetic checklist rows; no extraction accuracy is claimed. No new live
Requesty calls were made for Task 3. No visual browser QA performed.

The sections below describe previous milestones and historical verification.
Task 3 supersedes earlier statements that Streamlit checklist editing is unavailable.

---

# Requesty key-loader fix (21 November 2026)

The previous HTTP 403 was caused by the local document loader truncating the key
at `+`/`=` characters. The loader now reads the complete quoted
`export REQUESTY_API_KEY=...` assignment from the Team 1 guide. A live Haiku request
now succeeds. Earlier 403/access-blocker statements below are historical and
superseded by this finding. The live initial agent action also passes after unwrapping a single JSON Markdown fence; strict action and citation validation remain. Full real-tender extraction has not been verified.

The populated `.env.example` is ignored and is **not loaded** by the app; it also
still contains disabled-provider/approval settings. The corrected document fallback
works without loading that file. Alternatively export REQUESTY_API_KEY in the same
terminal used to launch Streamlit. Restart any running Streamlit process after
this fix, then click **Retry failed extraction** for a saved failure.

```sh
.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

33 offline tests and the legacy HTTP smoke pass, including a full-key regression
check. Live probes use only synthetic prompts, never tender contents.

---

# Simplified Requesty setup (21 November 2026)

The adapter now follows the Team 1 document's OpenAI SDK example:

```python
client = OpenAI(base_url="https://router.eu.requesty.ai/v1", api_key=api_key)
response = client.chat.completions.create(model=model, messages=messages)
```

To use the document's environment-variable setup, set `REQUESTY_API_KEY` privately
in your terminal before launching. This takes precedence over the local document
loader; no document parsing is needed when that variable is set. The existing
`MESAKUMO_LLM_API_KEY` configuration and local Team 1 document fallback still work.
Explicit disabled-provider or approval settings remain respected.

```sh
export REQUESTY_API_KEY="<your Team 1 key>"
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

The SDK supplies authentication, JSON transport and two automatic retries, with a
45-second timeout. The adapter still restricts the endpoint to Requesty EU, disables
redirect following, and validates finish status/content. The 2 MB response limit is
checked after SDK decoding, rather than bounding bytes during transport. SDK HTTPS
uses verified certificates. No native tools were added; LangGraph still routes JSON
actions. See the [official SDK usage](https://github.com/openai/openai-python).

Manual check: open page 1 with the key configured, select a pack and inspect status.
If a saved failure exists, click Retry failed extraction. Refreshing must restore
the result without another call. The live SDK check still returned HTTP 403; this
change does not establish working gateway access. All 31 synthetic/unit tests and
the legacy HTTP integration smoke pass.

---

# Task 2: LangGraph requirements agent (21 November 2026)

Launch from the repository root:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Tender Requirements discovers the local pack and phase, then starts one extraction
when a model is configured. Reopening or rerunning restores the persisted run.
Failed runs require **Retry failed extraction**; interrupted processes are marked
failed after the run lock is released. Runs are isolated by project and source
snapshot, so changed sources start a new run. Completed/partial runs stay saved.

As authorized for Task 2, the app loads the key from the local participant file
`99 LLM keys/hackathon-team-1-llm-zugang.md` into process memory when no provider
is explicitly set. It uses `bedrock/claude-haiku-4-5@eu-west-1` and the Requesty EU
endpoint. Explicit environment configuration takes precedence; set
`MESAKUMO_LLM_PROVIDER=disabled` for local discovery without extraction.
Keys are never written into code, traces or SQLite. Participant files stay ignored.

The existing Requesty adapter supplies text responses. This implementation uses
three application tools via a validated JSON action protocol, **not native provider
tool calling**. LangGraph StateGraph conditionally routes the agent-selected action:
`list_tender_documents`, `read_tender_passages`, or `submit_requirement_candidates`.
Reads accept known document or passage IDs with an offset. Each returned batch
includes IDs and a next offset; the model chooses follow-up reads, including
referenced attachments. Internal partner guidance is excluded from the agent.

Requests stay below 48 KB including a 1 KB system overhead reserve. Long passages
are fragmented without changing citation IDs; a read returns at most about 14 KB
of serialized source data. All fragments remain visible in coverage accounting.
A run allows at most 40 model actions (the adapter may retry transport failures).
Submissions may contain zero candidates. Citations and processed batches must
have been read in the selected phase. Conservative equivalence merges matching
wording/classification/constraints and unions citations; paraphrases are kept for
human review. Similar differing wording is flagged as a possible conflict, not
an established contradiction. Neither coverage nor validation proves accuracy.

SQLite stores actions, processed/read batches, candidates, failures and coverage.
An OS file lock prevents simultaneous runs across tabs/processes. Progress by
document, a sourced candidate checklist, coverage gaps and an execution trace
appear on page 1. Manual checklist editing/review stays available in the legacy
CLI interface. Other Streamlit pages remain status pages.

Manual test: open page 1 and select a pack/phase. Observe the extraction status
and document coverage. If gateway access fails, refresh: no new run should start.
Click Retry failed extraction to make one new attempt. After a successful run,
expand a candidate to inspect its cited passages; refresh and confirm the same
trace and checklist. Change pack/phase and confirm a separate project/run.

Offline acceptance (no key needed):

```sh
.venv/bin/python -m unittest discover -s tests -p test_agent.py -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/http_smoke.py
```

Live verification: both approved models returned HTTP 403 through Requesty.
No live extraction or native tool support is verified. Initial TLS trust failure
was fixed by supplementing system CA roots with certifi, retaining verification.
Graph construction follows the official [LangGraph StateGraph reference](https://reference.langchain.com/python/langgraph/graph/state/StateGraph).

The sections below retain Task 1 and legacy workflow details.

---

# Task 1: Streamlit local tender discovery (21 November 2026)

```sh
cd /Users/arijeet/Documents/Mesakumo_hackathon
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Open http://127.0.0.1:8501. Tender Requirements is the landing page. Use the
sidebar to choose one of the three top-level Ausschreibung packs, inspect roles,
reading statuses and extracted passages, and rescan. For pack 3 choose participation
or offer; offer adds ZIP members. Identical bytes within a phase share one source
and aliases remain visible. Archives are containers, not counted sources.
No upload or model configuration is required and discovery makes no model calls.
The other three pages state their implementation status.

Set `MESAKUMO_PARTICIPANT_DIR` to an external participant directory if needed;
default is the local `mesakumo_Input*` directory. Discovery excludes examples,
company references and keys. CSV roles seed actual filesystem discovery; missing
CSV classifications are visible. The AIDF is a buyer submission form, its PDF is
the readable schedule, and Bearbeitungshinweis.txt is internal guidance (stored
as knowledge, excluded from tender requirement extraction).

Manual test: switch through all packs, inspect the AIDF and guidance roles, select
both pack 3 phases and find the ZIP aliases. Click Rescan and reopen selections;
project IDs and distinct source counts must stay unchanged. Navigate all four pages.

The existing `.venv/bin/python -m mesakumo` launch and projects are preserved;
run it separately on the same default port. Legacy workflow instructions below
apply to that interface. Discovery uses separate stable project IDs and the same
SQLite store. Originals stay in the participant directory; parsed passages persist
in ignored local data. Participant files were removed from the Git index while
kept on disk; earlier Git history still contains them and has not been rewritten.

Task 1 checks: 19 unittest tests pass, legacy HTTP smoke passes; real packs were
checked locally with networking blocked. No live Requesty call was made.
Unsupported XLSX/AIDF and conservative PDF failures remain visible; no OCR added.
Source changes retain document versions; renamed files have new source identities.

---

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

The legacy app uses server-rendered HTML on Python's loopback HTTP server, typed dataclasses
and SQLite. Retaining the existing UI preserves tested workflow behaviour and
avoids a Streamlit rewrite. The legacy parser uses pypdf 6.21.4; the new shell also requires Streamlit 1.51.0. DOCX processing
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
have 45-second socket timeouts, at most three attempts using SDK backoff
on retryable status codes or connection failures. Model errors
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
virtual environments and secrets are Git-ignored. A Git repository exists.
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
are retained but not parsed or rendered; the supplied corporate template is outside this milestone.
No evidence matching, proposal generation, template export or submission is done.

Next: obtain permitted tender/model/template inputs, perform and inspect one live
extraction, then implement evidence review against company sources. See
PROJECT_STATUS.md and IMPLEMENTATION_LOG.md for the handoff.
# mesakumo_hackathon
