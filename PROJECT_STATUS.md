# Current implementation state

Updated: 21 November 2026. Task 2 implemented: LangGraph requirements agent wired
into Tender Requirements. Stop after Task 2; later Streamlit pages remain status pages.

Launch: `.venv/bin/python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false`
Legacy launch remains `.venv/bin/python -m mesakumo` (run separately).

Agent-selected JSON actions route through LangGraph to three scoped application
tools using the existing Requesty text adapter. Bounded passage reads, citation
validation against actual reads, processed batch accounting, zero-candidate
submissions, conservative duplicate merging, possible contradiction flags, per-document
progress, checklist, trace and coverage gaps are implemented. SQLite snapshots and
process locks prevent rerun/tab duplicates; failed/interrupted runs require explicit
retry. Internal partner guidance is excluded. Team 1 local credentials load in
memory only unless provider configuration is already set; disabled mode is supported.

Verification: 29 automated tests pass including scripted reference-following,
attachment citation, invalid citations, partial coverage, zero candidates, locks,
interruption/retry, long passage bounds and Streamlit reopen without model calls.
Legacy HTTP smoke passes; Streamlit starts on loopback. Actual tender source scoping
and bounded fragmentation checked locally for all four pack/phase selections.

Live blocker: Requesty EU returns HTTP 403 for both Haiku and Sonnet IDs in Team 1
access document. No live model-generated action or extraction result verified.
Initial Python CA trust issue fixed by supplementing system roots with certifi.
No native provider tool support claimed: implementation uses JSON text actions.

Limits: matching wording/classification/constraints merges conservatively;
paraphrases remain separate. Conflict detection is heuristic, human review required.
No OCR/XLSX/AIDF parsing added; unsupported sources count as coverage gaps. Partial
runs restore as-is. Reading/processing coverage does not establish completeness.
No visual browser QA. Participant materials remain local/ignored; existing staged
index removals and prior Git history are preserved. No Task 3 work performed.
