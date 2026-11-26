# TenderWorkbench technical handoff

## Automated workspace

Install the pinned libraries with `python -m pip install -r requirements.txt`, then
launch from the checkout with `python -m mesakumo`. It listens on
`127.0.0.1:8501`. Choose the local participant folder in **Local folders and
language**, select the projects and phases, and choose **Prepare proposals**.
The workspace persists across browser sessions. Ordinary supporting ZIPs stay
with their tender. The supplied offer archive and `Offer.zip` indicate split
participation and offer phases. An unfamiliar folder becomes a tender when
it contains `.tender-phases.json`, with `{"offer_archives":[]}` for a single
phase or relative ZIP paths for an offer phase. Existing project URLs remain
valid if discovery changes. It defaults to
`%LOCALAPPDATA%\TenderWorkbench` on Windows and
`${XDG_DATA_HOME:-$HOME/.local/share}/TenderWorkbench` on POSIX systems.
Use `MESAKUMO_AUTOMATION_DIR` to choose another writable workspace.

Set `MESAKUMO_LLM_PROVIDER=gemini`, `MESAKUMO_LLM_APPROVED=yes`, and export an
approved `GEMINI_API_KEY` for Gemini. Set `MESAKUMO_LLM_MODEL` to the model ID
enabled for your account. Requesty remains available via its existing settings.
See [.env.example](.env.example) for quota, OCR, renderer, and delivery options.
With the provider disabled, saved work remains accessible and text-only deck
rendering can use a current saved proposal.

Project links use `?project=<id>&tab=review` and resolve on the same workstation
and port. The UI starts a worker on first load. Use `python -m mesakumo --worker`
to process the queue independently of browser sessions and pass the same
`--port` used by the UI so review links retain their destination.

Use **Verify approved download** to compare current local inputs and issue
the exact approved PowerPoint bytes. Opening Final review reads the stored
revision without rehashing the corpus. See the [Windows setup guide](packaging/windows/README.md)
for installer prerequisites.

[REDESIGN_IMPLEMENTATION.md](REDESIGN_IMPLEMENTATION.md) maps the flow
to its files. The [execution ledger](docs/reviews/automated-tender-workflow.md) records
current verification and outstanding release checks. The remaining sections document the original manual workflow and its
saved workspaces. Open it with `MESAKUMO_LEGACY_STREAMLIT=yes`; the Tender 2
resume command below selects it automatically.

## macOS setup for the automated workflow

### Install Python dependencies

From the repository root, use Python 3.11 or later. If `.venv` already exists, start with the dependency install command.

```zsh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

The app reads exported environment variables from the shell that launches it.
[.env.example](.env.example) lists the available settings. Enter credentials
at the hidden Terminal prompts below and leave unused settings unset.

### Enable live Gemini calls

Get an API key for a Gemini project that can use the selected model. Use
documents you are authorized to send to Gemini.
[Google AI Studio API keys](https://aistudio.google.com/apikey)

```zsh
unset MESAKUMO_LEGACY_STREAMLIT
export MESAKUMO_LLM_PROVIDER=gemini
export MESAKUMO_LLM_APPROVED=yes
export MESAKUMO_LLM_MODEL=gemini-3.5-flash-lite
read -rs 'GEMINI_API_KEY?Paste your Gemini API key and press Enter: '
echo
export GEMINI_API_KEY
```

`gemini-3.5-flash-lite` is the app default. Set `MESAKUMO_LLM_MODEL`
to the exact model ID enabled for your project if it differs.

### Enable OCR for scanned PDFs

If Terminal reports `zsh: command not found: brew`, install Homebrew using
the official installer. It may prompt for your Mac administrator password.
[Homebrew installation guide](https://brew.sh/)

```zsh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the installer's **Next steps** to add Homebrew to your shell profile.
To load an existing installation into the current Terminal, run the
`shellenv` command for your Mac. The default paths are shown below.

```zsh
# Apple Silicon
eval "$(/opt/homebrew/bin/brew shellenv)"

# Intel Mac
# eval "$(/usr/local/bin/brew shellenv)"
```

Verify that `brew --version` works, then run these commands in the same
Terminal that will launch TenderWorkbench.

```zsh
brew install poppler tesseract tesseract-lang
command -v pdftoppm tesseract
tesseract --list-langs
```

The first check should print paths for both executables. The language list
should contain `deu` and `eng`. The automated workflow uses `deu+eng`
by default and tries OCR on pages without extractable text. Prepare a tender
with a scanned PDF to test it. Extracted passages can show citations such as
`Page 1 (OCR)`. If OCR produces no text, look for a reading gap for that page.
[Poppler](https://formulae.brew.sh/formula/poppler) ·
[Tesseract](https://formulae.brew.sh/formula/tesseract) ·
[Tesseract language data](https://formulae.brew.sh/formula/tesseract-lang)

### Enable the in-app PDF preview

Install LibreOffice with Homebrew. [LibreOffice cask](https://formulae.brew.sh/cask/libreoffice)

```zsh
brew install --cask libreoffice
/Applications/LibreOffice.app/Contents/MacOS/soffice --version
```

TenderWorkbench detects that standard location automatically. For a custom
installation, export `MESAKUMO_SOFFICE` with the full path to the `soffice`
executable. Once a draft exists, open **Final review** and click **Load deck
preview**. Draft and approved PowerPoint downloads are available independently
of the preview renderer.

### Enable review emails

Use an SMTP account that supports implicit TLS, typically on port `465`.
TenderWorkbench uses `SMTP_SSL` and validates the server certificate.
[RFC 8314](https://www.rfc-editor.org/rfc/rfc8314#section-3.3)

For a local test with Gmail, enable 2-Step Verification and create an App
Password if your account supports it. The example sends both review and
triage notifications to your own inbox. Replace the address and use your
provider's host, implicit TLS port, and credentials if you use a different
service.
[Google SMTP settings](https://knowledge.workspace.google.com/admin/gmail/send-email-from-a-printer-scanner-or-app) ·
[Google App Password instructions](https://support.google.com/accounts/answer/185833)

For routine team use, use an organization-managed SMTP relay or a dedicated
application sender. Use a send-only credential where the provider supports it,
store it in your deployment's secret store, and rotate it independently of
personal account credentials. The built-in transport supports SMTP username
and password authentication. Gmail OAuth requires an additional integration.
[Gmail SMTP authentication](https://developers.google.com/workspace/gmail/imap/imap-smtp)

```zsh
export MESAKUMO_SMTP_HOST=smtp.gmail.com
export MESAKUMO_SMTP_PORT=465
export MESAKUMO_SMTP_USER='your-address@gmail.com'
export MESAKUMO_SMTP_SENDER="$MESAKUMO_SMTP_USER"
export MESAKUMO_REVIEW_RECIPIENT="$MESAKUMO_SMTP_USER"
export MESAKUMO_TRIAGE_RECIPIENT="$MESAKUMO_SMTP_USER"
read -rs 'MESAKUMO_SMTP_PASSWORD?Paste your SMTP app password and press Enter: '
echo
export MESAKUMO_SMTP_PASSWORD
```

The review address receives notifications when a reviewer is assigned. The
triage address receives them when there is no reviewer match. If you prefer
to set the addresses in the app, leave the two recipient variables unset
and save the addresses under **Local folders and language**.

The worker queues a notification when a draft deck is generated. In **Final
review**, look for **Notification** with the delivery state and attempt count.
When it shows `sent`, check your inbox for **Tender proposal ready for
review**. The link opens the local workspace on the Mac running the app.

### Launch and test the full workflow

If the app is already running, stop it with `Ctrl+C`. To test against a fresh
workspace, optionally run the first command below. Omit it to use your
configured or default workspace.

```zsh
export MESAKUMO_AUTOMATION_DIR="$(mktemp -d /tmp/mesakumo-live.XXXXXX)"
.venv/bin/python -m mesakumo
```

1. Open [http://127.0.0.1:8501](http://127.0.0.1:8501). Under **Local folders
   and language**, select the participant pack containing the
   `Ausschreibung <number>` folders, historical evidence, and
   `Angebotstemplate.pptx`. Click **Save local settings**.
2. Clear **Select all tenders and phases**, select a project and phase, then
   click **Prepare proposals**. Choose a project with a scanned PDF to test OCR.
3. Review **Tender requirements**, **Evidence**, and **Proposal**. Resolve the
   blockers shown in **Final review** and regenerate from saved slides if needed.
4. In **Final review**, check the notification status and use **Load deck
   preview** if those integrations are configured. Enter the reviewer name,
   click **Approve current checksum**, then **Verify approved download** and
   **Download approved PowerPoint**.

## Purpose

TenderWorkbench is a local Streamlit workspace for German public tenders. It takes a
tender from source documents through a reviewed checklist, historical evidence
matching, editable proposal sections, and PowerPoint export. Source references
and unresolved items travel with the work so a developer or reviewer can trace
the output back to its inputs.

## Setup and run

1. From the repository root, install the pinned Python dependencies. The
   project requires Python 3.11 or later.

   ```sh
   python3 -m venv .venv
   . .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

2. Obtain the participant document pack separately. Set
   `MESAKUMO_PARTICIPANT_DIR` to the directory containing the top-level
   `Ausschreibung <number>` folders, historical evidence, and
   `Angebotstemplate.pptx`. If the variable is unset, the app looks for a
   `mesakumo_Input*` directory at the repo root and falls back to
   `private_documents/`. The pack and local workspaces are excluded from Git.
3. Run from the repository root. This example disables live model calls and
   keeps the workspace across browser sessions.

   ```sh
   export MESAKUMO_PARTICIPANT_DIR="/absolute/path/to/participant-pack"
   export MESAKUMO_LEGACY_STREAMLIT=yes
   export MESAKUMO_KEEP_SESSION=yes
   export MESAKUMO_LLM_PROVIDER=disabled
   python -m streamlit run mesakumo/streamlit_app.py --server.address 127.0.0.1 --server.port 8501
   ```

4. Open `http://127.0.0.1:8501`. To try document discovery without the pack,
   create `private_documents/Ausschreibung 1/`, copy
   `tests/fixtures/synthetic_tender.txt` into it, and point
   `MESAKUMO_PARTICIPANT_DIR` at `private_documents/`. This fixture is enough
   for discovery and manual checklist review. Evidence selection requires files
   at eligible inventory paths, and PowerPoint export requires the template.

`.env.example` lists configuration names with empty values. The application
reads exported environment variables directly. Set only the values you need,
leaving unused variables unset. For authorized live use, export
`MESAKUMO_LLM_PROVIDER=requesty`, `MESAKUMO_LLM_APPROVED=yes`, and your own
`REQUESTY_API_KEY`. The legacy `MESAKUMO_LLM_API_KEY` alias also works with
explicit approval. Keep the credential outside the checkout.

`MESAKUMO_DATA_DIR` defaults to `data/` and must resolve inside the repository.
The main database is `workspace.sqlite3`. Without
`MESAKUMO_KEEP_SESSION=yes`, Streamlit uses `data/sessions/<session-id>/`.
`MESAKUMO_INITIAL_TENDER` and `MESAKUMO_INITIAL_PAGE` optionally select the
opening tender and page. With an existing Tender 2 workspace, run
`python -m mesakumo.resume_tender2 --help` for the saved-session launcher.
It copies or reopens a workspace and defaults to port 8505 with model calls
disabled. `python -m mesakumo --legacy-http` starts the older local HTTP interface, which
uses `MESAKUMO_PORT`. The four-page workflow uses Streamlit.

## Architecture and data flow

| Stage | Main code | Data flow |
| --- | --- | --- |
| Tender requirements | `discovery.py`, `documents.py`, `coverage.py`, `tender_overview.py`, `checklist.py` | Discover and parse documents for the selected tender and phase. Assign content versions and source locations, deduplicate identical files, and batch source excerpts for extraction. Review the cited German overview and requirement candidates, edit scope, category and obligation, then save the checklist and its CSV. The overview has its own SQLite cache and can fail independently. |
| Evidence matching | `evidence.py`, `evidence_matching.py`, `evidence_confirmation.py`, `evidence_review.py` | Load current approved proposal-scope rows. The inventory permits previous projects, team information, and historical proposals. Only selected document versions enter the local lexical index. Matching writes one row per requirement; reviewers can edit fit, notes and citations, then save a revision-bound snapshot and CSV. Incomplete rows and unreadable files can be saved as open issues. |
| Proposal draft | `proposal.py`, `ui/proposal.py` | Use the current approved evidence snapshot. Generate a section per selected requirement, or enter and save one manually. Open assessments and No match rows can use fixed text without a model. Generate missing drafts leaves saved edits intact. Continuing to export saves a versioned package of the saved sections and records excluded rows and open items. |
| Final review and export | `final_export.py`, `pptx_export.py` | Read the current saved package, render it into branded template layouts, and download PowerPoint, CSV, or JSON. Long text continues across slides. Available citations and section IDs are mapped in PowerPoint notes. The renderer uses no model and stores the exact output bytes in SQLite against package and template hashes. |

`streamlit_app.py` wires the pages together. `domain.py` defines shared
records, and `persistence.py` persists project revisions and guards
concurrent writes. Checklist and evidence approvals are tied to input
revisions and file versions. Saved draft changes invalidate the export
package until it is saved again. Extraction checkpoints and execution
summaries are stored locally, including `extraction.jsonl` in the workspace.
Pages 3 and 4 use a direct save-and-export flow with open items visible.

## AI and prompts

`models.py` uses the OpenAI SDK as the HTTP client for Requesty's EU endpoint,
`https://router.eu.requesty.ai/v1`. The default model ID is
`bedrock/claude-haiku-4-5@eu-west-1`. Set `MESAKUMO_LLM_MODEL` to override it.
The adapter accepts only that EU base URL. It sends system and user messages
at temperature 0, uses a 45-second timeout and up to two SDK retries, and
rejects unfinished or malformed responses.

- **Extraction:** `coverage.py` asks for checkable German obligations with
  numbered source excerpt IDs, exact constraints, category, and scope. The
  strict JSON schema and local validators check the cited IDs. Source groups
  target 12,000 serialized bytes. The adapter allows 8,000 output tokens.
- **Overview:** `tender_overview.py` selects up to 24 excerpts under a
  25,000-byte source budget and asks for a short cited German overview with
  missing or conflicting facts labeled. The response uses a strict JSON
  schema and a 1,400-token output limit.
- **Evidence:** `evidence_matching.py` runs a LangGraph workflow over the
  local lexical index. The model issues JSON actions for refined search,
  neighboring text, or judgment. The graph caps actions at four per
  requirement and validates that citations came from retrieved passages.
  `evidence_confirmation.py` can make additional bounded batches to confirm
  coverage before grading. The evidence response limit is 1,800 tokens per
  call. The run uses one configured adapter.
- **Drafting:** `proposal.py` asks for German proposal prose in a strict
  JSON response with title, text, and used citation aliases. The output
  limit is 3,000 tokens. The prompt limits claims to supplied evidence and
  uses `[OFFEN: …]` for unresolved items. Manually edited sections with
  unconfirmed evidence citations export without those citations.

Live requests send task-specific document excerpts and metadata to the
provider. The application treats document text as data and validates outputs
locally. Lexical retrieval uses neither an embedding API nor a vector store.

## Formats, dependencies, and checks

- Tender text readers support TXT, text PDFs, DOCX, XLSX, and AIDF.
  Tender ZIP members are included for the offer phase. Evidence also supports
  PPTX, with slide-number references. The picker follows
  `Mesakumo_Document_Inventory.csv` and keeps historical proposal sources
  distinct from buyer tender materials and templates.
- Outputs include UTF-8 checklist and evidence CSVs with stable requirement
  IDs, the proposal CSV and JSON, and the rendered PPTX. The template stays
  unchanged.
- `requirements.txt` and `pyproject.toml` record the dependencies. Streamlit
  is the UI, LangGraph implements evidence matching and the older extraction
  agent, pypdf reads PDFs, and the OpenAI SDK and certifi provide the provider
  client and certificate bundle. SQLite, CSV, ZIP, and XML processing use
  the Python standard library.

Run the offline checks from the repository root.

```sh
MESAKUMO_LLM_PROVIDER=disabled python -m unittest discover -s tests -q
python -m pip check
```

The tests use scripted models or mocked HTTP. Tests tied to the participant
reference deck or template skip when those files are unavailable.

## Known limitations

- PDF OCR is unavailable. An image-only page can prevent ingestion of that
  PDF. Unsupported, missing, encrypted, or unreadable inputs and parsing-size
  limits can reduce coverage.
- Model judgments and generated wording need human review. Past proposals do
  not prove delivery, staff availability, or current signatures, pricing,
  declarations, or submission. A partial run can carry unresolved items
  into the draft and export.
- Page 4 renders the supplied `Angebotstemplate.pptx` layouts. There is no
  DOCX export or tender portal submission, and an incompatible template
  can fail to render.
