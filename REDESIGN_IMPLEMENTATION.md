# TenderWorkbench redesign implementation

**Branch** `feat/automated-tender-workflow`, based on `main` at `f2d1651`.

The new default entry point is `python -m mesakumo`. It opens the persistent Projects workspace on loopback. A single **Prepare proposals** action queues the selected tender phases. The worker validates and saves the requirements, evidence, and proposal content, renders a draft, and leaves the consultant one approval of the current PowerPoint checksum. The original review flow and demo databases remain accessible through the legacy launcher.

## Run the new workflow

1. Install the pinned requirements with `python -m pip install -r requirements.txt`.
2. Set the approved provider in your shell. For Gemini, export `MESAKUMO_LLM_PROVIDER=gemini`, `MESAKUMO_LLM_APPROVED=yes`, `GEMINI_API_KEY`, and the model ID enabled for your project in `MESAKUMO_LLM_MODEL`. The fallback Gemini model ID is `gemini-3.5-flash-lite`. Requesty retains its existing EU configuration. Provider calls send selected excerpts and metadata.
3. Run `python -m mesakumo`. Open **Local folders and language**, choose the directory containing the `Ausschreibung` folders and `Angebotstemplate.pptx`, and save. Use **Prepare proposals** on one project or a selection. To work with a saved demo in the old UI, run `python -m mesakumo.resume_tender2`.
4. Open the project when a draft is ready. Use the linked Requirements, Evidence and Proposal screens to resolve blockers. Use **Regenerate from saved slides** after wording-only edits, or **Prepare proposals** after changes to criteria or proof. Review the draft under **Final review**, approve its checksum, and download the approved PowerPoint.

The workspace defaults to `%LOCALAPPDATA%\TenderWorkbench` on Windows and `${XDG_DATA_HOME:-$HOME/.local/share}/TenderWorkbench` on POSIX systems. `MESAKUMO_AUTOMATION_DIR` overrides the default. The review URL uses `http://localhost:8501/?project=<id>&tab=review` and points to the same workspace when the local app runs again on the same port. Keep the app running to continue a job after closing the browser. `python -m mesakumo --worker` can run the worker independently. Set the same `--port` on both processes if you change it.

[Technical handoff](TECHNICAL.md) · [Environment settings](.env.example) · [Redesign brief](Mesakumo_Redesign_and_Codex_Prompts.md)

## Phase 0 · Baseline and fixtures

**Output.** `tests/baseline_redesign.py` makes a temporary SQLite backup of the saved demo and prints aggregate counts. Its synthetic drafting cases exercise a valid response, invalid JSON, provider failure, and an oversized payload. `tests/fixtures/redesign_evaluation.json` contains seven hand-labeled cases, spanning the three historical bids, Tender 2, both Tender 3 phases, and an English submission equivalent. The evaluation entries use short paraphrases and source locators.

| Baseline check | Observed result |
| --- | --- |
| Original branch test suite | 97 tests passed at `f2d1651`. |
| Saved Tender 2 criteria | 374 rows with no stored scope and `Unspecified` obligation. The legacy consumer treated defaulted scope as proposal. |
| Saved Tender 2 evidence | 19 selected files. 4 strong, 112 partial, 44 no match, and 214 with no fit value. |
| Saved Tender 2 package | 10 sections, 364 excluded criteria, and 3 evidence file issues. |
| Legacy draft generation | Valid JSON yielded a candidate. Malformed JSON and provider failure produced actionable errors. An oversized citation payload stopped before a provider call. |
| Legacy Streamlit export | The synthetic saved-evidence fixture passed through the proposal and export UI to a valid PowerPoint download. The original template bytes survived unchanged. |

**Historical patterns.** The Transformationschallenge description and the historical bid's slides 8–11 pair several obligations with one workshop-to-handoff narrative. The Change Management evaluation matrix and slides 9–22 use differentiated workstreams, including training, governance and stakeholder analysis. Digitalisierungspaten presents price, bidder status, team and specific qualifications on dedicated slides and includes separate annexes for declarations and certificates. These patterns informed the chapter plan, proof-only matching and current-action checklist.

**Tender inputs.** Tender 2's `VDI_TZ_3` service description on pages 4–7 separates research work packages and their outputs. Tender 3's participation instructions and selection criteria are outside the offer ZIP. The offer ZIP has nine members. Historical documents, credentials, workspace databases and generated decks are excluded from the implementation changes. The reported live drafting and PowerPoint failures were outside the baseline reproduction.

[Baseline probe](tests/baseline_redesign.py) · [Legacy UI export checks](tests/test_final_export.py) · [Hand-labeled evaluation cases](tests/fixtures/redesign_evaluation.json)

## Phase 1 · Project launcher and persistent jobs

**Output.** Projects can be opened in a second browser session and after restarting the app. A project ID is derived from the resolved root, tender folder and phase. A ZIP indicates distinct participation and offer jobs. The same tender folder name in two roots produces independent projects. The launcher has an explicit route for the old Streamlit UI and for the old HTTP shell.

**Logic.** `automation_store.py` extends the existing SQLite store with settings, current projects, immutable revision snapshots, jobs, work units, model metrics and outbox tables. An immediate transaction serializes claims and duplicate enqueues. The worker checks the lease token before publishing a unit or revision and extends the lease while processing. Expired jobs can be reclaimed and completed units are reused on restart. The UI uses query parameters and status polling. Old saved workflows use the original database format via `legacy_streamlit.py` and `resume_tender2.py`. Opening a legacy workspace is the explicit compatibility path. Automated runs re-extract the current criteria before evidence matching.

**Verification.** Tested competing enqueues and claims across threads, reclaim with an expired token, reopening in independent Streamlit test sessions, unknown project handling, root isolation, and duplicate phase selection. A real process launched at port 18501 returned a healthy response and listened on localhost only.

[Store and claims](mesakumo/automation_store.py) · [Worker](mesakumo/automation.py) · [Streamlit entry point](mesakumo/streamlit_app.py) · [Legacy resume](mesakumo/resume_tender2.py)

## Phase 2 · Model adapter, source reading and requirement classification

**Output.** Each candidate now has scope, obligation, response need, chapter, original conditions and citation IDs. The Tender requirements screen shows proposal, administrative and uncertain rows, the cited brief, possible conflicts, duplicate counts and reading gaps. Reviewers can edit or resolve individual items.

**Logic.** `gemini.py` uses the native `google-genai` structured response adapter. `automation_model.py` validates responses with Pydantic, reserves RPM and input TPM in SQLite, supports a configurable RPD ceiling, and records usage when the provider supplies it. Quotas are shared by jobs using the same workspace and provider-project/model bucket. Automatic RPM starts at 12, or 4 for a model ID containing `hydrogen`. The default input TPM is 200,000. The daily limit is enabled when configured. Transient 429 and selected network/5xx failures retry with backoff and jitter. Access and configuration errors stop the affected job. Truncated extraction and drafting outputs split into smaller checkpointed units.

`automation_requirements.py` runs per-file batches over a versioned manifest. The default source budget is 16,000 serialized bytes per batch before prompt and schema overhead. Candidate and fact source IDs are checked locally. Procurement portal submissions are routed to administrative current actions. Rated criteria retain scored status. Uncertain candidates get a focused classification attempt using their cited passages. Consolidation merges normalized equivalents when classification and conditions agree. It sends closely worded pairs for a separate equivalence judgment only when their conditions, numbers, date terms and polarity match. Explicitly identifying replacement language in an amendment can retire a matching clause while retaining the older source and conditions in the reviewer view. Ambiguous variants and conflicting facts remain visible. The tender's stated language can drive the proposal language, with a German or English workstation preference available.

The automated reader in `documents.py` retains readable PDF pages, reports page-level gaps, and tries local OCR when `pdftoppm` and Tesseract are available. Legacy parsers retain their previous entry-point behavior. Citations preserve exact file, version, locator and original excerpt. The renderer and reviewer see the same saved references.

**Verification.** Mocked native Gemini 429, invalid JSON, 403 and output truncation. Tested usage accounting, retry from a saved checkpoint, bilingual portal routing, scored proof routing, explicit amendment precedence, distinct conditions, and mixed readable/blank PDF pages. Live model access, model quality and OCR accuracy on scans remain pilot checks. [Gemini structured output documentation](https://ai.google.dev/gemini-api/docs/structured-output)

[Extraction and consolidation](mesakumo/automation_requirements.py) · [Readers and OCR](mesakumo/documents.py) · [Gemini](mesakumo/gemini.py) · [Quota and retries](mesakumo/automation_model.py)

## Phase 3 · Evidence and reviewer assignment

**Output.** Evidence is prepared automatically from eligible inventory entries. It shows confirmed, partial, verified missing and search incomplete, with exact locators and rejected candidates. Consultants can browse another allowed source, replace citations and save an assessment. Current actions and proposed methods stay in criterion coverage. Reviewer suggestions show supporting profile passages, criterion overlap, and ties. Reviewers can change the assignment independently of the mail destination.

**Logic.** `automation_evidence.py` caches parsed sources by root, path and hash and reuses the existing local BM25 index. It shortlists up to five excerpts and assesses up to four unresolved proof criteria per model call. A missing decision becomes search incomplete if the shortlist, document access or clipped excerpts limited the assessment. Assessment reuse is keyed to the criterion, model and actual candidate excerpts. Changed knowledge inputs lead to retrieval again and invalidate provider judgments whose shortlists changed. A saved evidence edit is reused when all its cited passage IDs still resolve.

The reviewer rank uses locally indexed team material. It recognizes named CVs and instructor forms in the inventory, or reads a local `reviewer_profiles.json` override in the workspace directory. Each entry has `name`, `role`, `profile_path` (the exact eligible inventory path), and `location` (for example `Page 1`). A candidate must have a named source and overlapping core work terms. A tie or zero credible matches goes to local triage. Historical citations never establish current staffing availability.

**Verification.** The end-to-end test confirms that the synthetic portal requirement enters no evidence request. The assessment citations resolve to the known file. Named persona tests cover a supported suggestion and a no-credible-match result. The automated pilot used the lexical approach. The hand-labeled set remains available for a comparative retrieval evaluation.

[Evidence and reviewer logic](mesakumo/automation_evidence.py) · [Inventory and readers](mesakumo/evidence.py) · [BM25 index](mesakumo/evidence_matching.py)

## Phase 4 · Editable proposal plan

**Output.** The Proposal screen has a slide-group outline, editable titles and claim text, claim-level citations, and structured fields for milestone dates, confirmed team roles and verified prices. Coverage associates each criterion with its response group or an open issue. Related method steps can share one slide response.

**Logic.** `automation_proposal.py` groups criteria into template chapters and bounded subgroups. Group keys depend on their criterion IDs. A factual model claim must quote citation text verbatim and can use only reference IDs tied to the covered criteria. Proof claims require evidence references. Current promises and missing information use the open-item path. Manual changes are versioned separately, and a context or grouping change surfaces saved wording for review. Refreshing a single group changes its unit key. Text-only edits preserve the approved-input lineage and invalidate the deck revision. The editor can record reviewer-verified text and update claim references.

**Verification.** Tests cover a combined response for two work-package criteria, required coverage, rejecting unknown references and unsupported factual claims, and concurrent revision protection. The same-machine Streamlit test saves a title edit, regenerates from saved work with zero model calls, and approves that exact new deck after reopening the workspace.

[Plan, grounding and blockers](mesakumo/automation_proposal.py) · [Proposal editor](mesakumo/ui/automation.py) · [Revision checks](mesakumo/automation_store.py)

## Phase 5 · Template render and final approval

**Output.** One run can reach a reviewable draft without checklist or evidence approval steps. Final review exposes the actual draft download, optional PDF preview, an Open in PowerPoint action on macOS and Windows, issues and coverage, reviewer and notification state, revision history, regeneration, and approval of the current checksum.

**Template map.** The supplied template has 21 ordered slides, uses six masters and has no native DrawingML tables on those slides. The timeline variants use editable PowerPoint shape groups. The map is tied to SHA-256 `c6db0bee73294c0b3b6a5ce514158fd97526a98e7761f1c4ede90c3de20d26d3`.

| Original slides and layouts | Original design | Use in the redesign |
| --- | --- | --- |
| 1 / 6 | Branded title page with inheriting client and project placeholders | Cover with proposal and client fields. |
| 2 / 7, 3 / 14, 4 / 8 | Welcome, company profile, and agenda | Omitted sample slides. Role layouts and branded assets are inherited where referenced. |
| 5 / 9, 6 / 7, 7 / 10, 8 / 9 | Context cards, approach placeholder, outcome panel, and participation boxes | Context, approach, outcomes and participation. Approach layout is reused for annexes. |
| 9–10 / 10 | Two sample Gantt alternatives | Slide 9 supplies the timeline role. Verified milestone labels and dates fill mapped text shapes. Sample dates and bars are dropped. |
| 11 / 11, 12 / 5, 13 / 8 | Organization diagram, team cards and second agenda | Slide 12 supplies editable team cards with reserved fields for verified names. |
| 14 / 12, 15 / 9 | Offer summary and commercial terms | Slide 15 supplies pricing and commercial terms. |
| 16 / 2, 17 / 3, 18 / 4, 19 / 15, 20 / 1, 21 / 13 | Sample terms, signatures, references, contacts and closing | Omitted sample slides. Current required documents and signatures stay in checklist coverage. |

**Logic.** `automation_deck.py` fills selected editable shapes, keeps layout and theme relationships, creates continuations with the same role layout, and removes sample slide parts, notes, placeholder text, highlights, and think-cell OLE data from the output. It validates internal package relationships and ZIP integrity. Invalid template revisions leave the saved proposal available for retry. A local LibreOffice installation can provide a PDF preview via `MESAKUMO_SOFFICE`, standard macOS app path or PATH. Final approval checks blockers, the current input fingerprint, stored bytes, template hash and revision. Any saved change clears the current deck approval and keeps the previous revision in history.

**Verification.** The supplied template rendered to 21 pages and the mapped fixture to nine pages in LibreOffice. All nine generated role pages were visually inspected. A second render verified corrections to inherited highlighting and sample cover placeholders. Sample client, project, date and staff text was checked in the resulting package, and controlled tests exercised a stale approval and a render failure with proposal recovery. Actual PowerPoint opening, editability and visual fidelity remain release checks. The structured fields fill native text boxes. Native tables, scheduled Gantt bars and additional template variants remain future renderer work.

[Template renderer](mesakumo/automation_deck.py) · [Approval persistence](mesakumo/automation_store.py) · [LibreOffice headless rendering documentation](https://help.libreoffice.org/latest/en-GB/text/shared/guide/start_parameters.html)

## Phase 6 · Notifications, offline pilot and Windows packaging

**Output.** The worker queues a link-only notification when a draft becomes reviewable, including drafts with blockers. It persists a reviewer name or local triage, concise reason and localhost link. The requested demo address is configured in this workstation's local settings for review and triage. SMTP requires its own host, sender and authorized credentials. The Windows bootstrap and Inno Setup recipe are included.

**Logic.** `automation_mail.py` uses the same SQLite database for its outbox and lease. A review episode receives one stable Message-ID. Definite temporary SMTP rejections retry with bounded backoff. Ambiguous interruption after attempted delivery is recorded as uncertain for operator verification. Transport failures leave the project accessible. The installer recipe copies application source, pinned requirements and the inventory, with a per-user Python environment and data directory. The caller can point it to the local participant folder at first launch.

**Offline pilot.** These elapsed times use the actual local tender filenames, format parsers, SQLite worker, supplied template and a deterministic model double. The model double supplies synthetic criteria, so the timings cover local orchestration and rendering. They provide no live provider latency, content recall, or consultant timing measurement.

| Project | Cold elapsed / fixture calls | Warm elapsed / fixture calls | Result |
| --- | --- | --- | --- |
| Tender 2, partner tender | 2.081 s / 21 | 0.047 s / 0 | Draft saved |
| Tender 3, offer | 0.658 s / 24 | 0.048 s / 0 | Draft saved |
| Tender 3, participation | 0.718 s / 23 | 0.047 s / 0 | Draft saved |

**Verification.** The capture transport received the saved link, reviewer label and reason. Tests covered duplicate delivery, temporary rejection and retry, retention of the same Message-ID, stable link reopening, edits, regeneration and checksum approval after reopening. Checks used local fixture delivery. A live provider and SMTP configuration were unavailable for end-to-end live validation, and Microsoft PowerPoint and Windows were unavailable on this workstation. The Inno Setup recipe is ready for a Windows build with `ISCC.exe packaging\windows\TenderWorkbench.iss`, followed by a first launch and restart check on Windows. The active consultant review and edit time has not been measured, so the 30-minute target remains unverified. [Inno Setup file packaging documentation](https://jrsoftware.org/inno-setup/documentation/topic_filessection.htm)

[Outbox](mesakumo/automation_mail.py) · [Offline pilot](tests/pilot_automation.py) · [Windows launcher](packaging/windows/Launch-TenderWorkbench.cmd) · [Installer recipe](packaging/windows/TenderWorkbench.iss)

## Reproduce the checks

The final offline suite passed 112 tests, including 15 automated-workflow tests.

```sh
MESAKUMO_LLM_PROVIDER=disabled .venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m pip check
PYTHONPATH=. MESAKUMO_LLM_PROVIDER=disabled .venv/bin/python tests/baseline_redesign.py
PYTHONPATH=. MESAKUMO_LLM_PROVIDER=disabled .venv/bin/python tests/pilot_automation.py
```

For a same-machine manual release check, open a draft from Projects, copy its review URL, close the browser, and restart TenderWorkbench on the same port. Open the copied URL in a fresh browser session. Resolve a blocker, save a slide edit, and regenerate the draft. Compare the generated pages with the mapped template roles in PowerPoint, approve the new checksum, and verify the SHA-256 of the approved download. Measure active consultant time separately from elapsed job time. Record the model ID, quotas, prompt usage and outstanding gaps for the live pilot.
