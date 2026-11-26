# Historical implementation state

Updated 21 November 2026. Pages 1 through 4 now support the Streamlit
flow from tender requirements to a reviewed corporate PPTX.

This page records the retired manual interface. Launch the current automated
workspace with `.venv/bin/python -m mesakumo`.
[Current setup guide](TECHNICAL.md)

Page 1 schedules readable tender batches, shows coverage gaps, and makes
requirements searchable with their original source excerpts. Reviewers can
edit, add, or remove rows and use Approve and Save to commit the checklist
snapshot and CSV together. The CSV header is `requirement`. Approval is tied to
the project, revision, fingerprint and source snapshot. New browser sessions
started fresh by default. The manual interface had a setting for restoring a
workspace across sessions.

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
notes and structured citations. Page 3 drafts and reviews sections for a current checklist and evidence approval.
It keeps gaps open until a reviewer records their resolution and requires
citation reconfirmation when prose is edited.

Earlier Page 2 automated and scripted checks and the legacy HTTP smoke test passed. The Streamlit flow verified editing, downloads, reopen, rollback and
approval invalidation. The main reference deck yielded 64 text passages in the
local citation check. The earlier inventory audit read 16 of 19 eligible files,
with three PDFs flagged for textless pages.

The milestone B live synthetic Requesty check passed. Milestone C used scripted
matching and local files. Real tender grading and visual browser QA remain
unverified.

Page 4 reviews the current approved package and retains its JSON and CSV.
It renders the provided `Angebotstemplate.pptx` locally with branding,
continuation slides and source notes. The preview remains available with
open items. The final download requires a named approval of the exact
preview bytes and is blocked by unresolved included items or omitted
required or unclassified proposal requirements. Changes to the template,
sections or upstream approvals require a current preview and approval.
The branch exporter from `vika-changes` was inspected and was not reused
because its normal rendering path calls a model and expects a different schema.

Latest verification: 98 tests and the HTTP smoke test passed. The synthetic
Streamlit flow used the supplied template. Tests verified exact preview
and approved download bytes, reopening, stale inputs, template failures,
content preservation, citation notes, branding assets and sample cleanup.
All seven slides of a synthetic QA export were inspected in Quick Look.
PowerPoint and a real tender export were not exercised for this milestone.

Remaining work includes OCR, grading validation on real tenders and Word
export once an official DOCX template is available and selected.
