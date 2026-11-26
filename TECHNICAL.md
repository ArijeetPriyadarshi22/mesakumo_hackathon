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
verification and outstanding release checks.

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

## Architecture and data flow

| Stage | Main code | Data flow |
| --- | --- | --- |
| Project intake | `discovery.py`, `automation_store.py`, `ui/automation.py` | Discover tender phases under the participant folder, register projects, and queue jobs in SQLite. |
| Tender requirements | `automation_requirements.py`, `documents.py` | Read versioned file contents, extract and classify cited requirements, and report reading gaps. |
| Evidence | `automation_evidence.py`, `evidence_matching.py` | Index eligible historical and team material, assess evidence, and suggest a reviewer. |
| Proposal | `automation_proposal.py`, `ui/automation.py` | Draft slide groups from the cited requirements and assessed evidence. Save consultant edits and verify coverage. |
| Final review | `automation.py`, `automation_deck.py`, `automation_mail.py` | Render a PowerPoint draft, optionally notify the reviewer, and verify the current revision and exact deck bytes for approval and download. |

The participant folder can be selected under **Local folders and language**.
`MESAKUMO_PARTICIPANT_DIR` overrides the initial folder suggestion. If it is
unset, the app looks for a `mesakumo_Input*` folder in the checkout and falls
back to `private_documents/`. The participant pack is kept outside Git.

The current app stores jobs, checkpoints, model usage, the outbox, and
project revisions in the automated workspace. Earlier manual workspace
databases use a separate format. Re-select the source pack and prepare the
projects in the automated workspace.

## Model providers

Gemini uses the native SDK for structured output. Requesty uses the OpenAI
SDK with the EU endpoint `https://router.eu.requesty.ai/v1`. To enable
Requesty, export `MESAKUMO_LLM_PROVIDER=requesty`,
`MESAKUMO_LLM_APPROVED=yes`, and `REQUESTY_API_KEY`. The default Requesty
model is `bedrock/claude-haiku-4-5@eu-west-1`. Set `MESAKUMO_LLM_MODEL`
to override it.

Live requests send task-specific document excerpts and metadata to the
provider. The app validates responses locally and uses a lexical index for
retrieval.

## Formats, dependencies, and checks

- Tender text readers support TXT, text PDFs, DOCX, XLSX, and AIDF.
  Tender ZIP members are included for the offer phase. Evidence also supports
  PPTX, with slide-number references. The picker follows
  `Mesakumo_Document_Inventory.csv` and keeps historical proposal sources
  distinct from buyer tender materials and templates.
- The app stores draft and approved PowerPoint bytes with project revisions.
- `requirements.txt` and `pyproject.toml` record the Python dependencies.
  Streamlit provides the UI, pypdf reads PDFs, and the Gemini and OpenAI
  SDKs connect to the configured model provider.

Run the offline checks from the repository root.

```sh
MESAKUMO_LLM_PROVIDER=disabled python -m unittest discover -s tests -q
python -m pip check
```

The tests use scripted models or mocked HTTP. Tests tied to the participant
reference deck or template skip when those files are unavailable.

## Known limitations

- Image-only PDF pages require the local OCR tools. Pages with no readable
  result are reported as reading gaps. Unsupported, missing, encrypted, or
  unreadable inputs and parsing-size limits can reduce coverage.
- Model judgments and generated wording need human review. Past proposals do
  not prove delivery, staff availability, or current signatures, pricing,
  declarations, or submission. A partial run can carry unresolved items
  into the draft and export.
- Final review uses the supplied `Angebotstemplate.pptx` layouts. There is no
  DOCX export or tender portal submission, and an incompatible template
  can fail to render.
