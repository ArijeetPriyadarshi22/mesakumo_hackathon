"""
Standalone test runner for VS Code.
Simulates a human-approved Project object (from previous pipeline steps),
sends it to Claude Sonnet 5 via Requesty API, and generates Result_Proposal.pptx.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from domain import (
    Document,
    EvidenceMatch,
    Project,
    ProposalSection,
    Requirement,
    ReviewDecision,
    SourcePassage,
)
from export import ClaudeProposalStructurer, PptxProposalExporter


def load_local_env() -> None:
    """Loads variables from a local .env file if present (convenient for VS Code runs)."""
    for env_path in (Path(".env"), Path(__file__).resolve().parent / ".env"):
        if env_path.exists() and env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


def build_sample_approved_project(template_path: Path) -> Project:
    """Creates a realistic German tender project that passed human review."""
    project = Project(
        id="AN00455",
        name="KI-Strategie und Use-Case-Roadmap",
        synthetic=False,
        scope=(
            "Erarbeitung einer KI-Strategie inkl. KI-Reifegrad-Check und priorisierter "
            "Use-Case-Roadmap für die Stadtwerke Ulm/Neu-Ulm."
        ),
        documents=[
            Document(
                id="doc-template",
                name=template_path.name,
                role="template",
                status="Ready",
                original_path=str(template_path),
            ),
            Document(
                id="doc-tender",
                name="Leistungsbeschreibung_KI_Strategie.pdf",
                role="tender",
                status="Processed",
            ),
        ],
        passages=[
            SourcePassage(
                id="p-1",
                document_id="doc-tender",
                location="Abschnitt 1.2 Ausgangslage",
                excerpt=(
                    "Der Auftraggeber (Stadtwerke Ulm, Standort Ulm) plant den systematischen "
                    "Aufbau von KI-Kompetenzen. Bestehende Prozesse sind teilweise heterogen."
                ),
            ),
            SourcePassage(
                id="p-2",
                document_id="doc-tender",
                location="Abschnitt 4.1 Konditionen & Fristen",
                excerpt=(
                    "Angebotsbindefrist: 18. Dezember 2026. Gewünschter Projektstart: "
                    "11. Januar 2027, Laufzeit ca. 8 Wochen. Ansprechpartner: Herr Dr. Markus Weber."
                ),
            ),
        ],
        requirements=[
            Requirement(
                id="REQ-1",
                text="Durchführung eines KI-Reifegrad-Checks in fünf Dimensionen mit 8 Interviews",
                source_ids=["p-1"],
                reviewed=True,
            ),
            Requirement(
                id="REQ-2",
                text="Identifikation und Bewertung von KI Use Cases mit ROI-Horizont 3 bis 18 Monate",
                source_ids=["p-1"],
                reviewed=True,
            ),
        ],
        evidence=[
            EvidenceMatch(
                id="EV-1",
                requirement_id="REQ-1",
                source_ids=["p-1"],
                quality="sufficient",
                limitations="",
                decision="Approved",
            )
        ],
        sections=[
            ProposalSection(
                id="SEC-1",
                title="Anschreiben & Verständnis",
                text=(
                    "Sehr geehrter Herr Dr. Weber, vielen Dank für die Möglichkeit, Ihnen unser Angebot "
                    "für das Projekt 'KI-Strategie und Use-Case-Roadmap' bei den Stadtwerken Ulm vorzustellen. "
                    "Wir begleiten Sie pragmatisch von der Standortbestimmung bis zur umsetzbaren Roadmap."
                ),
                requirement_ids=["REQ-1"],
                reviewed=True,
            ),
            ProposalSection(
                id="SEC-2",
                title="Vorgehensweise (3 Phasen)",
                text=(
                    "Phase 1: Analyse & KI-Reifegrad-Check (Woche 1-3, 14 PT) – 8 strukturierte Interviews, "
                    "Bewertung von Daten, Prozessen und Organisation.\n"
                    "Phase 2: Use-Case-Identifikation & Priorisierung (Woche 3-6, 18 PT) – 2 Kreativ-Workshops, "
                    "Bewertung nach Machbarkeit und ROI (3-18 Monate).\n"
                    "Phase 3: Umsetzungs-Roadmap & Governance (Woche 6-8, 10 PT) – Konkreter Maßnahmenplan, "
                    "Kompetenzaufbau und Abschlusspräsentation."
                ),
                requirement_ids=["REQ-1", "REQ-2"],
                reviewed=True,
            ),
            ProposalSection(
                id="SEC-3",
                title="Projektteam & Konditionen",
                text=(
                    "Projektleitung: Stefan Lassen (Manager bei mesakumo GmbH, 16 Jahre Praxiserfahrung in IT- & KI-Strategie). "
                    "Unterstützung durch Dominik Brosa (Manager, Fokus Prozess- & Datenarchitektur). "
                    "Gesamtaufwand: 42 PT zum Tagessatz von 1.350 EUR zzgl. MwSt. Reisekostenpauschale vor Ort in Ulm: 150 EUR/Tag."
                ),
                requirement_ids=["REQ-1", "REQ-2"],
                reviewed=True,
            ),
        ],
    )

    project.decisions.append(
        ReviewDecision(
            target_id=project.id,
            revision=project.revision,
            fingerprint=project.fingerprint(),
            reviewer="human.reviewer@mesakumo.de",
            timestamp="2026-11-21T04:30:00Z",
            decision="Approved",
            note="Freigegeben für PPTX-Export",
        )
    )
    return project


def write_pptx_safely(pptx_bytes: bytes, preferred_path: Path) -> Path:
    """
    Writes PPTX bytes to `preferred_path`. If the file is currently open/locked
    by PowerPoint or another viewer (PermissionError), writes to a timestamped
    fallback filename instead of crashing.
    """
    try:
        preferred_path.write_bytes(pptx_bytes)
        return preferred_path
    except PermissionError:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        alt_path = preferred_path.with_name(f"{preferred_path.stem}_{stamp}{preferred_path.suffix}")
        alt_path.write_bytes(pptx_bytes)
        print(
            f"NOTE: '{preferred_path.name}' was locked (likely open in PowerPoint). "
            f"Saved to '{alt_path.name}' instead."
        )
        return alt_path


def main() -> None:
    load_local_env()

    if not os.environ.get("REQUESTY_API_KEY"):
        print("WARNING: REQUESTY_API_KEY not found in environment!")
        print('Run in PowerShell: $env:REQUESTY_API_KEY="your-key"')
        return

    template_path = Path("Angebotstemplate.pptx")
    if not template_path.exists():
        alt_template = Path(__file__).resolve().parent / "Angebotstemplate.pptx"
        if alt_template.exists():
            template_path = alt_template
        else:
            print(f"ERROR: '{template_path}' not found in current folder or script folder.")
            return

    project = build_sample_approved_project(template_path)
    print(f"Project '{project.name}' approved status: {project.approved}", flush=True)

    structurer = ClaudeProposalStructurer(model_id="bedrock/claude-sonnet-5@eu-west-1")
    exporter = PptxProposalExporter(
        fallback_template_path=template_path,
        structurer=structurer,
    )

    print("Calling Claude Sonnet 5 & rendering Angebotstemplate.pptx...", flush=True)
    pptx_bytes = exporter.render(project)

    out_file = Path("Result_Proposal.pptx")
    saved_path = write_pptx_safely(pptx_bytes, out_file)
    print(f"SUCCESS! Saved generated presentation to: {saved_path.resolve()}", flush=True)


if __name__ == "__main__":
    main()