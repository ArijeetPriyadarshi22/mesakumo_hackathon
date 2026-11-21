# Task 2 — Evidence Matching

**Approved tender requirements → local company evidence matching → reviewed findings for proposal drafting**

Task 2 takes Arijeet’s approved checklist and gives Luba reviewed findings for the proposal. The Evidence Matching page lets a reviewer confirm or change a verdict, select or remove evidence, verify an additional quotation against the original, and leave a comment. It saves these edits to the Task 2 report. The Task 1 checklist remains the upstream record.

## Arijeet → Task 2

Provide an approved requirements CSV or a fully approved checklist in a document project. In the app, leaving **Approved Task 1 CSV** empty uses the project checklist when it is nonempty and every requirement is marked reviewed. From Python, call `run_workflow(checklist, inventory, participant_root, ...)` for a CSV or `match_local_requirements(requirements, inventory, participant_root, ...)` for a nonempty list of `Requirement` records with `reviewed=True`. Both functions are in `mesakumo.evidence_workflow` and return a Task 2 report dictionary for review.

| Default CSV header | Contract |
| --- | --- |
| `requirement_id` | Required. A unique, nonempty ID per row. |
| `requirement_text` | Required. The buyer requirement as nonempty text. |
| `approval` | Required by default. Approved values are `approved`, `freigegeben`, `yes`, `true`, and `1`. Blank, `pending`, `unreviewed`, `draft`, `rejected`, `not approved`, `no`, `false`, and `0` are skipped. Matching is case-insensitive. Unknown values fail import. |
| `constraints` | Optional. Explicit conditions as a JSON string array or one item per line in a quoted CSV field. |
| `tender_source` | Optional. A human-readable buyer reference, such as a section or page. |
| `tender_source_ids` | Optional. Buyer passage IDs in the same list format as `constraints`. |

The reader accepts UTF-8 with or without a BOM and comma, semicolon, or tab delimiters. At least one row must be approved.

```csv
requirement_id,requirement_text,approval,constraints,tender_source,tender_source_ids
R01,Show experience delivering two workshops.,approved,Include delivery dates.,Synthetic tender section 2,T-001
```

To use different headers, supply a JSON mapping from the keys `id`, `text`, `approval`, `constraints`, `tender_source`, and `source_ids` to the actual column names. Omitted keys keep their defaults. For example, `{"id":"ID","text":"Requirement","approval":"Status"}` maps three headers. Use `MESAKUMO_CHECKLIST_COLUMNS` with the app, `--checklist-columns` with the CLI, or pass a `ChecklistColumns` instance as `columns=` in Python. For an export with no approval column, confirm that Task 1 exported only approved rows, then select the approved-only checkbox, pass `--approved-export`, or set `approved_export=True`. If an approval column is present, its values still apply.

## Company sources and matching

The **document inventory CSV** is a separate input. It points to originals under the participant directory and requires the headers `Primary role`, `Location type`, and `Path relative to participant input folder`. Task 2 reads eligible local file rows under **Previous project sources** by default. With the supplied inventory, this selects the two company project decks. **Team information** and **Historical proposal** are optional groups. The CLI also accepts an exact eligible inventory path through `--include-source`. Historical buyer documents and credential rows are outside evidence selection.

Task 2 reads PPTX, PDF with extractable text, DOCX, and UTF-8 TXT. A citation identifies the original filename, relative path, document version (SHA-256), and location. PPTX locations use the slide number in presentation order. PDF locations use physical page numbers. DOCX uses body paragraph locations and TXT uses line ranges. A prior proposal can support what was offered or claimed. Treat any claim of prior delivery according to the underlying evidence.

Local BM25 ranks passages using the requirement text and explicit constraints. The matcher starts with up to 12 candidates when a shortlist is available. A supported Strong can finish at the shortlist when source coverage permits. Otherwise it scans the remaining readable passages in bounded batches. A full scan is also available. Cited passages must come from the evaluated company sources, and quoted text is checked against those passages.

| Verdict | Internal value | Meaning |
| --- | --- | --- |
| Strong | `sufficient` | Evidence supports every mandatory part. |
| Partial | `partial` | Evidence supports some parts and there are remaining gaps. |
| Gap | `none` | A completed scan found no support. The evidence list is empty. |

An **Incomplete** search can result from an unreadable selected file, partial extraction, or a provider failure. The app can show and export provisional findings from readable material. For a partially readable PDF, readable pages keep their original numbers and the missing physical pages appear in the source reading report. Resolve the source or provider issue and rerun before treating the result as a completed Gap or handing it off as approved.

## Task 2 → Luba

### Reviewed CSV

In **Evidence Matching**, choose **Save review** for each requirement, then **Download saved report (CSV)**. The download includes one row per included requirement, including rows still awaiting review. Use `review_state=Reviewed` together with `search_state=complete` when consuming reviewed findings. The other review states are `Unreviewed`, `Stale`, and `Provisional`. The app rechecks the source versions on download. Changed originals or a changed in-app checklist require a rerun.

| Fields | Meaning |
| --- | --- |
| `requirement_id`, `requirement`, `constraints` | Requirement identity, text, and explicit conditions. |
| `tender_source`, `tender_source_ids` | Buyer reference and JSON array of buyer passage IDs. |
| `category`, `suggested_category`, `review_state` | Saved verdict when a current review exists, otherwise the suggested verdict. Incomplete rows use `Incomplete` in `category`. |
| `match`, `reference`, `evidence` | Supporting text, readable references with relative path, location and version, and JSON evidence records. Evidence reflects the saved selection when a current review exists. |
| `gap`, `reviewer_comment` | Suggested missing support and the reviewer’s comment. |
| `search_state`, `search_issue`, `search_scope`, `candidate_passages`, `evaluated_passages` | Coverage, any problem encountered, scope, and passage counts. |

### Programmatic handoff

Call `mesakumo.evidence_workflow.reviewed_handoff(report: dict, *, participant_root: Path | None = None) -> dict` with the saved version 1 Task 2 report dictionary. The downloaded CSV is a separate format. Pass `participant_root` to recheck the selected originals against their recorded versions. With that argument, a changed or missing original fails verification and requires a rerun. Without it, the function uses the saved matching snapshot.

The returned fields are `schema_version`, `source_verification`, `reviewed`, and `pending`. Each reviewed item carries `requirement`, the human-reviewed `quality`, selected `evidence`, `suggested_gaps`, `reviewer_comment`, and `search_scope`. Incomplete, stale, and unreviewed rows go to `pending` with a `requirement_id` and `reason`. Excluded rows are omitted. A reviewed Partial or Gap still describes missing support and should reach drafting as an open item. A shortlist-only search needs a full scan before it can be reviewed as Partial or Gap.

Example excerpt from a synthetic `reviewed` item. The requirement object is abbreviated and the version is illustrative.

```json
{
  "requirement": {
    "id": "R01",
    "text": "Show experience delivering two workshops.",
    "constraints": ["Include delivery dates."],
    "provenance": "Synthetic tender section 2",
    "source_ids": ["T-001"]
  },
  "quality": "partial",
  "evidence": [{
    "passage_id": "P-001",
    "document_id": "D-001",
    "document_name": "Example Projects.pptx",
    "document_version": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "location": "Slide 3",
    "path": "references/Example Projects.pptx",
    "role": "Previous project sources",
    "quotes": ["We delivered two workshops."]
  }],
  "suggested_gaps": ["Delivery dates are missing."],
  "reviewer_comment": "Request the delivery dates before drafting.",
  "search_scope": "full scan after retrieval"
}
```

## Run and verify

Use the project’s Python 3.11+ environment. From the repo root, install the dependencies with `python -m pip install -r requirements.txt`. For the app, set the local paths and launch:

```sh
export MESAKUMO_DOCUMENT_INVENTORY="/path/to/document-inventory.csv"
export MESAKUMO_PARTICIPANT_ROOT="/path/to/participant-inputs"
python -m mesakumo
```

Open `http://127.0.0.1:8501/` by default. In a document project, choose **Requirements and evidence → Open Evidence Matching**. A CSV can be selected directly there. For a CLI run, use `python -m mesakumo.evidence_workflow match --help`. It supports offline replay via `--offline-responses`, or `--live` after approval. From Python, pass `model=` with an offline implementation of `LanguageModel.complete(prompt)` for integration tests. Keep reports containing company evidence in approved local storage.

For an authorized live Team 1 run, configure the process environment privately:

| Setting | Value |
| --- | --- |
| `MESAKUMO_LLM_PROVIDER` | `requesty` |
| `MESAKUMO_LLM_APPROVED` | `yes` after approval for the provider and selected materials |
| `MESAKUMO_LLM_BASE_URL` | `https://router.eu.requesty.ai/v1` |
| `MESAKUMO_LLM_MODEL` | Mesakumo-approved EU Claude model or routing-policy ID |
| `MESAKUMO_LLM_API_KEY` | Team 1 Requesty key provided privately |

If the page reports missing setup, check that the inventory path is a file and the participant path is a directory. If the page reports that model access is disabled, check the approved provider settings above. After updating either set of environment variables, restart the app with `python -m mesakumo` from the same shell.

Run the offline suite from the repo root:

```sh
python -m unittest discover -s tests -v
```

Previous offline acceptance used an approved fixture and a stub or replay to verify matching, review and CSV download with original slide and PDF page citations. The remaining integration checks are import of Arijeet’s genuine approved CSV and live Claude matching on the approved route. For the CSV, check headers, approval values and the imported row count. For live testing, confirm permission to call the route and to send the selected material before making the request.
