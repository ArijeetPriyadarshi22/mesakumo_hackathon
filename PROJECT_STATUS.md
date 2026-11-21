# Current implementation state

Updated 21 November 2026. Page 2 milestones A through C are complete. Reviewers can
match, edit and approve evidence against the approved checklist, then
download a separate reviewed evidence CSV.

Launch with `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false`.
The legacy CLI remains available separately via `.venv/bin/python -m mesakumo`.

Page 1 schedules readable tender batches, shows coverage gaps, and makes
requirements searchable with their original source excerpts. Reviewers can
edit, add, or remove rows and use Approve and Save to commit the checklist
snapshot and CSV together. The CSV header is `requirement`. Approval is tied to
the project, revision, fingerprint and source snapshot. New browser sessions
start fresh by default. Set `MESAKUMO_KEEP_SESSION=yes` to restore a workspace.

Page 2 uses the saved approval to display the same project, phase, revision
and requirement IDs. Use Continue to Evidence Matching after saving approval.
An edit or source change that invalidates the approval locks Page 2.

The evidence picker offers Previous project sources, Team information and
Historical proposal from the inventory. Select all eligible and Clear
selection are available. Start Evidence Matching captures the approved
revision and selected document versions. The run indexes readable passages
locally and uses one configured Requesty model with LangGraph to judge each
approved requirement. Citations are restricted to retrieved selected passages
and resolved to original text, filename, location, role and version. Historical
proposals are past bid material. Current actions and confirmations are flagged
separately. Buyer packs, blank buyer forms, templates and credentials are excluded.

Failed or skipped lookups remain incomplete and block evidence approval.
Reviewers can inspect exact tender and historical evidence citations separately,
change grades and notes, and edit citations drawn from the selected corpus.
No match rows and source coverage gaps can be acknowledged during approval.

Evidence approval and its UTF-8 CSV are stored atomically in SQLite. The CSV
has one row per requirement, matches the review table, and carries gap/action
notes and structured citations. Page 3 confirms receipt only while the Page 1
approval, evidence review, selection and selected document versions are current.
Reopen the same workspace by enabling `MESAKUMO_KEEP_SESSION=yes`.

Automated and scripted checks: all 62 tests and the legacy HTTP smoke test
passed. The Streamlit flow verified editing, downloads, reopen, rollback and
approval invalidation. The main reference deck yielded 64 text passages in the
local citation check. The earlier inventory audit read 16 of 19 eligible files,
with three PDFs flagged for textless pages.

The milestone B live synthetic Requesty check passed. Milestone C used scripted
matching and local files. Real tender grading and visual browser QA remain
unverified.

Remaining work includes OCR, real tender grading validation, proposal drafting
and corporate export. Page 3 is limited to the approved evidence handoff.
