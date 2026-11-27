# TenderWorkbench

TenderWorkbench prepares local tenders for review in a persistent Streamlit workspace. It extracts cited requirements, matches historical proof, drafts editable slide groups, and binds final approval to the exact PowerPoint bytes.

Run `python -m mesakumo` after installing `requirements.txt`. The [macOS setup guide](TECHNICAL.md#macos-setup-for-the-automated-workflow) covers live Gemini calls, scanned PDF OCR, LibreOffice previews, and review email. See the [Windows setup guide](packaging/windows/README.md) for installer prerequisites and [REDESIGN_IMPLEMENTATION.md](REDESIGN_IMPLEMENTATION.md) for implementation and verification.

The [review execution ledger](docs/reviews/automated-tender-workflow.md) records the current verification and release checks.

The [architecture and workflow guide](docs/tenderworkbench-architecture-and-workflow.md) explains every screen and action, follows the code from intake through approval, and describes what the app saves and reuses.
