# Current implementation state

Updated: 21 November 2026. Task 3 complete: Tender Requirements review, approval
and revision-bound UTF-8 CSV. Stop after Task 3; evidence retrieval is not implemented.

Launch: `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false`
Existing CLI remains `.venv/bin/python -m mesakumo` (run separately).

Page 1: stable IDs, editable statement/category/obligation level/constraints/citations,
manual additions with human provenance, removals, original filename/location/excerpt
view, adjacent extraction gaps. Only current phase buyer sources can be cited.
Approve and Save uses existing checklist review/invalidation and SQLite stale-write
checks; approved snapshot and CSV bytes commit together. CSV failures save no approval
and can be retried. Approvals are idempotent and tied to revision, fingerprint and
source snapshot. Restart restores approval/download. Edits invalidate the handoff.
Page 2 receives the exact approved revision and stays locked without current approval.

Checks: 40 automated tests pass. Synthetic Streamlit acceptance edits/adds/removes,
compares registered download bytes to CSV/saved rows, simulates CSV failure/recovery,
reopens a fresh app session and confirms edit invalidation. Backend tests cover stale
writes, citation scope, idempotence and database failure rollback. Legacy HTTP smoke
passes. Headless Streamlit starts on loopback. Local actual-document linkage checks
pass for all three packs/four phase selections using synthetic test rows and temporary
SQLite storage; not an extraction accuracy evaluation.

No live Requesty calls needed/performed for Task 3. Prior key-truncation fix established
successful Haiku completion and initial JSON action only; full real-document extraction
remains unverified. Existing Requesty adapter/agent behavior preserved. No visual browser
QA performed. No OCR/XLSX/AIDF parser, evidence retrieval, drafting or corporate export.

Obligation level defaults to Unspecified for legacy records; fingerprints remain
compatible when that optional field has no explicit value. Partial/unsupported source
gaps can be acknowledged during checklist approval, but never bypass citation validation.
Checklist approval is distinct from final proposal approval. Save individual form edits
before approving; only the displayed saved table is exported. Earlier approvals/CSVs
are retained in SQLite; UI downloads the current approved snapshot.

Existing uncommitted work, participant-material privacy/ignore rules and credentials
are preserved. No Task 4 work performed.


21 November 2026 UI/diagnostics follow-up: page 1 puts a full-width checklist first,
with explicit empty-table columns and readable source labels. Supporting forms,
inventory, source text, coverage and traces are collapsed. Agent errors now distinguish
provider failures from invalid JSON/actions; historical generic errors retain an
explanation of missing diagnostic detail. Existing saved failures are not deleted
or automatically retried. Minimal live first-action check PASS; full extraction still
unverified. 40 tests and HTTP smoke PASS. Browser host unavailable; visual QA blocked.
