# TenderWorkbench on Windows

Build the per-user installer from the repository root with Inno Setup.

```bat
ISCC.exe packaging\windows\TenderWorkbench.iss
```

The recipe writes `TenderWorkbench-Setup.exe` under `packaging\windows\dist`. The
installer includes application code and the document inventory. Install Python 3.11
with the `py` launcher before first use. The first launch also needs access to
the configured Python package index or an approved local wheel cache for the
dependencies in the installed requirements file.

Use the Start menu entry or [Launch-TenderWorkbench.cmd](Launch-TenderWorkbench.cmd).
The app keeps its Python environment and workspace under
`%LOCALAPPDATA%\TenderWorkbench`. Open the page on localhost and choose the
separate participant folder that contains the tender folders and offer template.

Before release, test install, first launch, a completed draft, reopening a
review link after restart, an approved download in desktop PowerPoint, and
retention of the workspace after an upgrade. Perform the Windows check using
the same account permissions and package source as the intended installation.
