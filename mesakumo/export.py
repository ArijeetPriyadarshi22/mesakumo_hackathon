"""
Corporate PowerPoint rendering for mesakumo proposals.
Combines:
1. ClaudeStructurer (AWS Bedrock: claude-sonnet-5 / claude-haiku-4-5) to map human-approved
   Project text into the exact slide schema without inventing facts.
2. PptxProposalExporter to deterministically populate Angebotstemplate.pptx (21 slides),
   clean up template designer annotations, clone dynamic slides, and render Gantt charts.
"""

from __future__ import annotations

import copy
import io
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openai import OpenAI
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.opc.packuri import PackURI
from pptx.oxml.ns import qn
from pptx.slide import Slide
from pptx.util import Inches

try:  # Package import (app, tests); plain import when run as a script from mesakumo/.
    from .domain import Project
except ImportError:
    from domain import Project

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str], None]


def _default_progress(msg: str) -> None:
    """Prints concise live status lines immediately to stdout."""
    print(f"[Claude/PPTX] {msg}", flush=True)


# =============================================================================
# 1. Protocol & Fallback
# =============================================================================

class ProposalExporter(Protocol):
    def render(self, project: Project) -> bytes: ...


class UnconfiguredExporter:
    def render(self, project: Project) -> bytes:
        raise NotImplementedError("Export is not implemented. No proposal file was created.")


# =============================================================================
# 2. LLM Structuring Agent (AWS Bedrock Claude)
# =============================================================================

STRUCTURER_SYSTEM_PROMPT = """
Du bist ein präziser Daten-Strukturierungs-Agent für die mesakumo GmbH (Ulm).
Deine Aufgabe ist es, aus einem bereits vom Menschen geprüften Angebotsentwurf (Project-Objekt)
ein striktes JSON-Objekt für die Befüllung der PowerPoint-Vorlage (Angebotstemplate.pptx) zu erstellen.

STRIKTE REGELN (mesakumo Standard):
1. KEINE ERFINDUNGEN: Nutze ausschließlich Fakten, Zahlen, Personen, Referenzen und Preise aus dem bereitgestellten Input.
2. LÜCKEN MARKIEREN: Wenn eine benötigte Information im Input fehlt, schreibe exakt "[OFFEN: <Beschreibung>]" (z.B. "[OFFEN: Ansprechpartner im Tender nicht genannt]"). Erfinde niemals Namen oder Tagessätze!
3. SPRACHE & STIL: Schreibe auf Deutsch in der Sie-Form. Pragmatisch, konkret, unternehmerisch, ohne Marketing-Superlative ("revolutionär", "disruptiv" etc. vermeiden).
4. PRÄGNANZ FÜR FOLIEN: Halte Bulletpoints kurz und prägnant (max. 12–16 Wörter pro Punkt, max. 3–6 Punkte pro Box), damit kein Text über die Folienränder läuft.
5. OUTPUT FORMAT: Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown-Codeblöcke oder Erklärtext.
""".strip()

JSON_SCHEMA_INSTRUCTION = """
Erzeuge genau diese JSON-Struktur:
{
  "slide_decisions": {
    "include_org_chart": "boolean (true bei komplexen Projekten >= 6 Wochen oder mehreren Stakeholdern, false bei kurzen Checks)",
    "timeline_variant": "integer (1 für kompakte Projekte in Wochen, 2 für mehrphasige Transformationsprojekte)",
    "include_team_slide": "boolean (true wenn konkrete Beraternamen im Input vorkommen, sonst false)"
  },
  "meta": {
    "offer_id": "string (z.B. AN00452 oder [OFFEN: Angebotsnummer])",
    "date": "string (aktuelles deutsches Datum, z.B. 21. November 2026)",
    "valid_until": "string (Bindefrist aus Ausschreibung oder [OFFEN: Bindefrist])",
    "client_name": "string (Name des Auftraggebers)",
    "client_location": "string (Hauptstandort des Kunden)",
    "project_title": "string (Offizieller Projekttitel)"
  },
  "cover_letter": {
    "salutation": "string (z.B. Sehr geehrte Damen und Herren,)",
    "body_paragraphs": ["string (Absatz 1)", "string (Absatz 2)"],
    "lead_quote": "string (Kurzes persönliches Statement der Projektleitung zum Projekt)",
    "lead_name_role": "string (Vorname Nachname | Rolle bei der mesakumo GmbH)"
  },
  "situation": {
    "background_bullets": ["string (2-3 Stichpunkte zum Hintergrund)"],
    "challenges_bullets": ["string (2-3 Stichpunkte zu Herausforderungen)"],
    "goal_1_bullets": ["string (2-3 Stichpunkte zu Hauptziel 1)"],
    "goal_2_bullets": ["string (2-3 Stichpunkte zu Hauptziel 2)"]
  },
  "approach_slides": [
    {
      "title": "string (Titel des Arbeitspakets / Handlungsfelds)",
      "bullets": ["string (4-6 konkrete Arbeitsschritte und Ergebnisse)"]
    }
  ],
  "scope": {
    "included_deliverables": ["string (4-6 konkrete Projektergebnisse im Angebot)"],
    "optional_services": ["string (Optionale Leistungen oder 'Keine optionalen Zusatzleistungen definiert.')"],
    "out_of_scope": ["string (Abgrenzung: Was ist nicht Teil des Projekts)"]
  },
  "cooperation": {
    "contact_and_governance": "string (Ansprechpartner & Regeltermine beim Kunden)",
    "documents_and_stakeholders": "string (Zugang zu Unterlagen & Wissensträgern)",
    "team_availability": "string (Verfügbarkeit der Fachbereiche / IT des Kunden)"
  },
  "timeline": {
    "duration_badge": "string (z.B. '12 Wochen' oder '[OFFEN: Laufzeit]')",
    "summary_text": "string (Kurze Zusammenfassung der Projektphasen und Arbeitsweise vor Ort/remote)",
    "gantt_tasks": [
      {"task": "string (Phasenname)", "start_week": 1, "end_week": 4, "pt": "string (z.B. 15 PT)"}
    ]
  },
  "org_chart": {
    "include": true,
    "client_sponsor": "string (Rolle/Name beim Kunden)",
    "pm_tasks": ["string (Aufgaben der mesakumo Projektleitung)"]
  },
  "team_members": [
    {
      "name": "string (Vorname Nachname aus Wissensbasis)",
      "role": "string (Position bei mesakumo)",
      "bio": "string (Kurzer Werdegang aus Wissensbasis)",
      "skills": ["string (Kernkompetenz 1)", "string (Kernkompetenz 2)"],
      "experience": "string (z.B. '15 Jahre Praxiserfahrung')"
    }
  ],
  "offer": {
    "scope_and_effort_bullets": ["string (Leistungspaket + geschätzter Aufwand in PT/Std)"],
    "duration_and_deadline": "string (z.B. '12 Wochen Projektlaufzeit bis TT.MM.JJJJ')",
    "lead_pitch": "string (1-2 Sätze zur Qualifikation der Projektleitung)"
  },
  "conditions": {
    "timeframe": "string (Projektzeitraum / Starttermin)",
    "remuneration": "string (Vergütungsmodell & Tagessatz/Stundensatz aus Wissensbasis oder [OFFEN])",
    "travel_expenses": "string (Reisekostenregelung)",
    "scope_reference": "string (Verweis auf Leistungsbeschreibung)",
    "project_location": "string (Einsatzort vor Ort / remote)"
  }
}
""".strip()


class ClaudeProposalStructurer:
    """
    Uses Requesty OpenAI-compatible router (bedrock/claude-sonnet-5@eu-west-1)
    to plan slide visibility and transform an approved Project object into structured JSON.
    """

    def __init__(
        self,
        model_id: str = "bedrock/claude-sonnet-5@eu-west-1",
        api_key: str | None = None,
        base_url: str = "https://router.eu.requesty.ai/v1",
        on_progress: ProgressCallback | None = _default_progress,
        reasoning_effort: str | None = "none",
        max_tokens: int = 16000,
    ):
        self.model_id = model_id
        self.api_key = api_key or os.environ.get("REQUESTY_API_KEY", "")
        self.base_url = base_url
        self.on_progress = on_progress
        # On large inputs Claude Sonnet otherwise spends the whole output budget on hidden
        # thinking and returns almost no JSON (finish_reason "length"). Structuring needs none.
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens

    def _emit(self, message: str) -> None:
        if self.on_progress:
            try:
                self.on_progress(message)
            except Exception:
                pass

    def build_payload(self, project: Project) -> dict[str, Any]:
        """Streams Claude's response in real time, emits short decision summaries, and returns JSON."""
        self._emit(
            f"Vorbereitung: {len(project.sections)} freigegebene Abschnitte, "
            f"{len(project.requirements)} Anforderungen, {len(project.passages)} Textstellen."
        )
        project_context = self._serialize_project_for_llm(project)
        user_prompt = (
            f"Hier sind die freigegebenen Projektdaten (Project ID: {project.id}):\n\n"
            f"{project_context}\n\n"
            f"{JSON_SCHEMA_INSTRUCTION}"
        )

        self._emit(f"Sende Anfrage an {self.model_id} (Streaming aktiv)...")
        raw_response = self._invoke_llm(STRUCTURER_SYSTEM_PROMPT, user_prompt)
        self._emit("Antwort vollständig empfangen, parse JSON-Struktur...")
        payload = self._extract_json(raw_response)
        self._log_decisions_summary(payload)
        return payload

    def _log_decisions_summary(self, payload: dict[str, Any]) -> None:
        """Prints concise highlights of what Claude decided for the presentation."""
        decisions = payload.get("slide_decisions", {})
        meta = payload.get("meta", {})
        approach = payload.get("approach_slides", [])
        team = payload.get("team_members", [])
        timeline = payload.get("timeline", {})
        conditions = payload.get("conditions", {})

        self._emit(
            f"Entscheidung Folien: Timeline-Variante {decisions.get('timeline_variant', 1)} | "
            f"Org-Chart={'Ja' if decisions.get('include_org_chart') else 'Nein'} | "
            f"Team-Folien={'Ja' if decisions.get('include_team_slide') else 'Nein'}"
        )
        self._emit(
            f"Meta & Laufzeit: Kunde='{meta.get('client_name', '?')}' | "
            f"Laufzeit='{timeline.get('duration_badge', '?')}' | "
            f"Arbeitspakete={len(approach)} | Team={len(team)}"
        )
        if conditions.get("remuneration"):
            self._emit(f"Konditionen erkannt: {conditions['remuneration']}")

        open_markers = self._collect_open_markers(payload)
        if open_markers:
            self._emit(f"Offene Punkte markiert ({len(open_markers)}): {', '.join(open_markers[:5])}")
        else:
            self._emit("Alle Pflichtfelder aus den freigegebenen Daten belegt (keine [OFFEN]-Lücken).")

    @classmethod
    def _collect_open_markers(cls, obj: Any) -> list[str]:
        found: list[str] = []
        if isinstance(obj, str) and "[OFFEN" in obj:
            found.append(obj.strip())
        elif isinstance(obj, dict):
            for v in obj.values():
                found.extend(cls._collect_open_markers(v))
        elif isinstance(obj, list):
            for item in obj:
                found.extend(cls._collect_open_markers(item))
        return found

    def _invoke_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Streams tokens from Requesty/Bedrock and prints short progress updates without hanging."""
        if not self.api_key:
            raise RuntimeError(
                "REQUESTY_API_KEY is not set. Set it via environment variable or pass api_key=..."
            )
        client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        chunks: list[str] = []
        last_logged_len = 0
        last_seen_section = ""

        options = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        stream = client.chat.completions.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            temperature=0.1,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **options,
        )

        finish_reason = None
        for event in stream:
            if not event.choices:
                continue
            finish_reason = event.choices[0].finish_reason or finish_reason
            delta = event.choices[0].delta.content or ""
            if not delta:
                continue
            chunks.append(delta)
            joined = "".join(chunks)

            sec_matches = re.findall(r'"([a-z_]+)"\s*:', joined)
            if sec_matches:
                latest_sec = sec_matches[-1]
                if latest_sec != last_seen_section and latest_sec in {
                    "slide_decisions",
                    "meta",
                    "cover_letter",
                    "situation",
                    "approach_slides",
                    "scope",
                    "cooperation",
                    "timeline",
                    "org_chart",
                    "team_members",
                    "offer",
                    "conditions",
                }:
                    last_seen_section = latest_sec
                    self._emit(f"Claude strukturiert Bereich '{latest_sec}'...")

            if len(joined) - last_logged_len >= 1200:
                last_logged_len = len(joined)
                self._emit(f"Fortschritt: ~{len(joined)} Zeichen generiert...")

        # Never turn a cut-off or empty answer into an empty presentation.
        if finish_reason == "length":
            raise RuntimeError(
                f"The structuring model hit its output limit ({self.max_tokens} tokens) before finishing the "
                "slide data. No presentation was created. Raise max_tokens or reduce the approved sections."
            )
        if not chunks:
            raise RuntimeError("The structuring model returned no slide data. No presentation was created.")
        return "".join(chunks)

    @staticmethod
    def _serialize_project_for_llm(project: Project) -> str:
        data = {
            "project_id": project.id,
            "project_name": project.name,
            "scope": project.scope,
            "today_date": datetime.now().strftime("%d.%m.%Y"),
            "requirements": [
                {
                    "id": r.id,
                    "kind": getattr(r, "kind", "Must"),
                    "text": r.text,
                    "constraints": getattr(r, "constraints", ""),
                }
                for r in project.requirements
            ],
            "approved_sections": [
                {"id": s.id, "title": s.title, "text": s.text}
                for s in project.sections
            ],
            "evidence_matches": [
                {
                    "req_id": e.requirement_id,
                    "quality": e.quality,
                    "limitations": e.limitations,
                }
                for e in project.evidence
            ],
            "source_passages": [
                {"doc_id": p.document_id, "loc": p.location, "excerpt": p.excerpt}
                for p in project.passages[:40]
            ],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        # Fallback: extract outermost JSON object if extra text wraps it
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
        return json.loads(cleaned)


# =============================================================================
# 3. Deterministic PPTX Renderer (Calibrated to Angebotstemplate.pptx)
# =============================================================================

class PptxProposalExporter:
    """
    Populates the 21-slide Angebotstemplate.pptx using structured JSON data.
    Slide indices match the inspected template structure:
      [0] Title Slide
      [1] Cover Letter (Wir freuen uns auf die Zusammenarbeit)
      [2] Company Credentials (mesakumo: Erfahrener Partner...)
      [3] Agenda 1
      [4] Ausgangssituation (4 rows: Hintergrund, Herausforderungen, Ziel 1, Ziel 2)
      [5] Bewährte Vorgehensweise (Cloned 1..N times)
      [6] Wie wir Sie unterstützen (3 scope boxes)
      [7] Ihre Mitarbeit im Projekt (3 client cooperation boxes)
      [8] Vorschlag für Projektverlauf (Variante 1 -> Gantt Chart)
      [9] Vorschlag für Projektverlauf (Variante 2 -> Removed)
      [10] Beispielhafte Projektorganisation (Optional)
      [11] Geplantes mesakumo Projektteam (2 cards per slide)
      [12] Agenda 2
      [13] Unser Angebot (Scope/Effort + Duration + Lead)
      [14] Spezifische Angebotsbedingungen (5 condition rows)
      [15] AGB (1)
      [16] AGB (2)
      [17] Unser Angebot für KUNDE (Signature slide)
      [18-20] Static Client Logos & Closing Slides
    """

    ANNOTATION_KEYWORDS = (
        "Bild Kunde",
        "Logo Kunde",
        "Variante 1",
        "Variante 2",
        "Passendes Icon",
        "Nur wenn sinnvoll",
        "Projektleitung",
        "Unterschrift hinzufügen",
        "Projektteam anpassen",
        "Projektorganisation anpassen",
        "Jahr dazuschalten",
        "Inhalte ergänzen",
    )

    def __init__(
        self,
        fallback_template_path: str | Path | None = "Angebotstemplate.pptx",
        structurer: ClaudeProposalStructurer | None = None,
        strict_approval: bool = True,
        assets_dir: str | Path | None = None,
        on_progress: ProgressCallback | None = _default_progress,
    ):
        self.fallback_template_path = Path(fallback_template_path) if fallback_template_path else None
        self.structurer = structurer or ClaudeProposalStructurer(on_progress=on_progress)
        self.strict_approval = strict_approval
        self.assets_dir = Path(assets_dir) if assets_dir else None
        self.on_progress = on_progress

    def _emit(self, message: str) -> None:
        if self.on_progress:
            try:
                self.on_progress(message)
            except Exception:
                pass

    def _resolve_template_path(self, project: Project) -> Path:
        """Resolves the PPTX template path from project documents or fallback path."""
        candidates: list[Path] = []

        for doc in getattr(project, "documents", []):
            if getattr(doc, "role", "") == "template" and getattr(doc, "original_path", None):
                candidates.append(Path(doc.original_path))

        if self.fallback_template_path:
            candidates.append(self.fallback_template_path)
            candidates.append(Path(__file__).resolve().parent / self.fallback_template_path)

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate

        searched = ", ".join(str(c) for c in candidates) or "none"
        raise FileNotFoundError(
            f"Angebotstemplate.pptx could not be found. Searched paths: {searched}"
        )

    def _find_asset(self, relative_or_abs_path: str | None) -> Path | None:
        """Safely resolves an optional image asset path if it exists on disk."""
        if not relative_or_abs_path or not isinstance(relative_or_abs_path, str):
            return None

        raw = Path(relative_or_abs_path.strip())
        if not str(raw) or str(raw) == ".":
            return None

        search_paths = [raw]
        if self.assets_dir:
            search_paths.append(self.assets_dir / raw)
        search_paths.append(Path(__file__).resolve().parent / raw)

        for p in search_paths:
            if p.exists() and p.is_file():
                return p
        return None

    def render(self, project: Project, prebuilt_payload: dict[str, Any] | None = None) -> bytes:
        """
        Main entrypoint implementing ProposalExporter.
        1. Verifies human approval (`project.approved`).
        2. Uses Claude Structurer (unless `prebuilt_payload` is passed) to map text to JSON.
        3. Renders the PPTX presentation safely without duplicate slide XML parts and returns raw bytes.
        """
        if self.strict_approval and not project.approved:
            raise PermissionError(
                f"Project '{project.id}' must be approved by a human reviewer before export."
            )

        template_path = self._resolve_template_path(project)
        payload = prebuilt_payload or self.structurer.build_payload(project)

        self._emit(f"Lade Template '{template_path.name}'...")
        prs = Presentation(str(template_path))

        if len(prs.slides) < 18:
            raise ValueError(
                f"Unexpected template structure in '{template_path.name}': "
                f"expected at least 18 slides, found {len(prs.slides)}."
            )

        # Step 1: Populate static/fixed-index slides BEFORE cloning/deleting slides!
        decisions = payload.get("slide_decisions", {})
        try:
            timeline_variant = int(decisions.get("timeline_variant", 1))
        except (TypeError, ValueError):
            timeline_variant = 1
        timeline_variant = 2 if timeline_variant == 2 else 1

        active_timeline_idx = 8 if timeline_variant == 1 else 9
        unused_timeline_idx = 9 if timeline_variant == 1 else 8

        self._emit("Befülle Titel, Anschreiben, Ausgangssituation, Scope & Mitarbeit...")
        self._fill_slide_0_title(prs.slides[0], payload)
        self._fill_slide_1_cover_letter(prs.slides[1], payload)
        self._fill_slide_4_situation(prs.slides[4], payload)
        self._fill_slide_6_scope(prs.slides[6], payload)
        self._fill_slide_7_cooperation(prs.slides[7], payload)

        self._emit(f"Befülle Projektverlauf (Variante {timeline_variant}), Orga, Angebot & Bedingungen...")
        self._fill_slide_timeline(prs.slides[active_timeline_idx], payload)
        self._fill_slide_10_org_chart(prs.slides[10], payload)
        self._fill_slide_13_offer(prs.slides[13], payload)
        self._fill_slide_14_conditions(prs.slides[14], payload)
        self._fill_slide_17_signature(prs.slides[17], payload)

        # Step 2: Handle dynamic multi-slide sections in REVERSE slide index order
        # so earlier slide indices remain valid during insertion/deletion.

        # 2a. Slide [11]: Team members (2 consultants per slide) or delete if none
        if decisions.get("include_team_slide", True) and payload.get("team_members"):
            self._emit("Erzeuge Team-Folien...")
            self._expand_team_slides(prs, template_idx=11, team_members=payload.get("team_members", []))
        else:
            self._delete_slide(prs, 11)

        # 2b. Slide [10]: Org Chart (keep only if LLM decided it is meaningful for this tender)
        if not decisions.get("include_org_chart", True):
            self._delete_slide(prs, 10)

        # 2c. Delete unused Timeline Variant (either [8] or [9])
        self._delete_slide(prs, unused_timeline_idx)

        # 2d. Slide [5]: Approach / Handlungsfelder (1..N slides)
        self._emit("Erzeuge Vorgehensweisen-Folien...")
        self._expand_approach_slides(prs, template_idx=5, approach_list=payload.get("approach_slides", []))

        # Step 3: Remove all template designer helper annotations (Ellipse callouts)
        self._remove_template_annotations(prs)

        # Step 4: Global text & footer placeholder replacement across all slides
        self._emit("Ersetze Platzhalter (Angebotsnummer, Kunde, Projekttitel, Datum)...")
        self._apply_global_replacements(prs, payload)

        # Step 5: Export to bytes
        self._emit("Speichere PPTX-Paket...")
        buf = io.BytesIO()
        prs.save(buf)
        buf.seek(0)
        return buf.getvalue()

    # -------------------------------------------------------------------------
    # Slide-Specific Populators (Calibrated to inspected shapes)
    # -------------------------------------------------------------------------

    def _fill_slide_0_title(self, slide: Slide, payload: dict[str, Any]) -> None:
        meta = payload.get("meta", {})
        by_name = {s.name: s for s in slide.shapes}

        if "Textplatzhalter 2" in by_name and by_name["Textplatzhalter 2"].has_text_frame:
            by_name["Textplatzhalter 2"].text_frame.text = meta.get("project_title", "Projektangebot")
        if "Textplatzhalter 3" in by_name and by_name["Textplatzhalter 3"].has_text_frame:
            by_name["Textplatzhalter 3"].text_frame.text = f"Angebot für {meta.get('client_name', 'Kunde')}"
        if "Textplatzhalter 4" in by_name and by_name["Textplatzhalter 4"].has_text_frame:
            by_name["Textplatzhalter 4"].text_frame.text = meta.get("offer_id", "AN00XXX")
        if "Textplatzhalter 1" in by_name and by_name["Textplatzhalter 1"].has_text_frame:
            by_name["Textplatzhalter 1"].text_frame.text = meta.get("date", "")

        # Client Logo placeholder ('Inhaltsplatzhalter 5')
        logo_shape = by_name.get("Inhaltsplatzhalter 5")
        logo_path = self._find_asset(meta.get("client_logo_path"))
        if logo_shape:
            if logo_path:
                self._replace_shape_with_picture(slide, logo_shape, logo_path)
            else:
                self._remove_shape(logo_shape)

    def _fill_slide_1_cover_letter(self, slide: Slide, payload: dict[str, Any]) -> None:
        letter = payload.get("cover_letter", {})
        by_name = {s.name: s for s in slide.shapes}

        # Main cover letter box: 'Inhaltsplatzhalter 12'
        body_shape = by_name.get("Inhaltsplatzhalter 12")
        if body_shape and body_shape.has_text_frame:
            salutation = letter.get("salutation", "Sehr geehrte Damen und Herren,")
            paragraphs = letter.get("body_paragraphs", [])
            full_lines = [salutation, ""] + paragraphs
            self._set_paragraphs_preserve_style(body_shape, full_lines)

        # Lead quote box: 'Rechteck 15'
        quote_shape = by_name.get("Rechteck 15")
        if quote_shape and quote_shape.has_text_frame and letter.get("lead_quote"):
            quote_shape.text_frame.text = letter["lead_quote"]

        # Lead name/role box: 'Textfeld 25'
        role_shape = by_name.get("Textfeld 25")
        if role_shape and role_shape.has_text_frame and letter.get("lead_name_role"):
            role_shape.text_frame.text = letter["lead_name_role"]

        # Lead photo placeholder: 'Franziska'
        photo_shape = by_name.get("Franziska")
        photo_path = self._find_asset(letter.get("lead_photo_path"))
        if photo_shape and photo_path:
            self._replace_shape_with_picture(slide, photo_shape, photo_path)

    def _fill_slide_4_situation(self, slide: Slide, payload: dict[str, Any]) -> None:
        sit = payload.get("situation", {})
        content_boxes = [
            s for s in slide.shapes
            if s.has_text_frame and s.name.startswith("Content Placeholder")
        ]
        content_boxes.sort(key=lambda s: s.top)

        rows_data = [
            sit.get("background_bullets", []),
            sit.get("challenges_bullets", []),
            sit.get("goal_1_bullets", []),
            sit.get("goal_2_bullets", []),
        ]

        for box, bullets in zip(content_boxes, rows_data):
            if bullets:
                self._set_paragraphs_preserve_style(box, [f"• {b}" for b in bullets[:4]])

    def _fill_slide_6_scope(self, slide: Slide, payload: dict[str, Any]) -> None:
        scope = payload.get("scope", {})
        rect_boxes = [
            s for s in slide.shapes
            if s.has_text_frame and s.name.startswith("Rectangle 40")
        ]
        for box in rect_boxes:
            if box.left < 3_000_000:
                items = scope.get("included_deliverables", [])
                if items:
                    header = "Zu erwartende Projektergebnisse"
                    self._set_paragraphs_preserve_style(box, [header, ""] + [f"• {x}" for x in items[:6]])
            elif box.top < 3_000_000:
                items = scope.get("optional_services", [])
                if items:
                    self._set_paragraphs_preserve_style(box, [f"• {x}" for x in items[:5]])
            else:
                items = scope.get("out_of_scope", [])
                if items:
                    header = "Nicht Teil des Projektes"
                    self._set_paragraphs_preserve_style(box, [header] + [f"• {x}" for x in items[:4]])

    def _fill_slide_7_cooperation(self, slide: Slide, payload: dict[str, Any]) -> None:
        coop = payload.get("cooperation", {})
        boxes = [
            s for s in slide.shapes
            if s.has_text_frame and s.name.startswith("Rectangle 12")
        ]
        boxes.sort(key=lambda s: s.top)

        values = [
            coop.get("contact_and_governance", ""),
            coop.get("documents_and_stakeholders", ""),
            coop.get("team_availability", ""),
        ]
        for box, val in zip(boxes, values):
            if val:
                box.text_frame.text = val

    def _fill_slide_timeline(self, slide: Slide, payload: dict[str, Any]) -> None:
        timeline = payload.get("timeline", {})
        by_name = {s.name: s for s in slide.shapes}

        badge = by_name.get("Rechteck 198")
        if badge and badge.has_text_frame and timeline.get("duration_badge"):
            badge.text_frame.text = f"{timeline['duration_badge']} | Projektlaufzeit"

        summary = by_name.get("MIO_TEXTBOX_BODY2")
        if summary and summary.has_text_frame and timeline.get("summary_text"):
            summary.text_frame.text = timeline["summary_text"]

        dummy_chart = by_name.get("Gruppieren 127") or by_name.get("Gruppieren 192")
        tasks = timeline.get("gantt_tasks", [])
        if tasks:
            chart_png = self._render_gantt_png(tasks)
            if dummy_chart:
                left, top, width = dummy_chart.left, dummy_chart.top, dummy_chart.width
                self._remove_shape(dummy_chart)
                slide.shapes.add_picture(io.BytesIO(chart_png), left, top, width=width)
            else:
                slide.shapes.add_picture(io.BytesIO(chart_png), Inches(0.4), Inches(2.3), width=Inches(12.2))

    def _fill_slide_10_org_chart(self, slide: Slide, payload: dict[str, Any]) -> None:
        org = payload.get("org_chart", {})
        by_name = {s.name: s for s in slide.shapes}

        if "Rectangle 3" in by_name and by_name["Rectangle 3"].has_text_frame and org.get("client_sponsor"):
            by_name["Rectangle 3"].text_frame.text = org["client_sponsor"]

        if "Rechteck 33" in by_name and by_name["Rechteck 33"].has_text_frame and org.get("pm_tasks"):
            lines = ["mesakumo Projektleitung – Aufgabenbereiche:", ""] + [f"• {t}" for t in org["pm_tasks"][:6]]
            self._set_paragraphs_preserve_style(by_name["Rechteck 33"], lines)

    def _fill_slide_13_offer(self, slide: Slide, payload: dict[str, Any]) -> None:
        offer = payload.get("offer", {})
        meta = payload.get("meta", {})
        by_name = {s.name: s for s in slide.shapes}

        scope_box = by_name.get("Rectangle 40")
        if scope_box and scope_box.has_text_frame and offer.get("scope_and_effort_bullets"):
            title = meta.get("project_title", "Projektumfang")
            lines = [title, ""] + [f"• {b}" for b in offer["scope_and_effort_bullets"][:7]]
            self._set_paragraphs_preserve_style(scope_box, lines)

        dur_box = by_name.get("Rechteck 10")
        if dur_box and dur_box.has_text_frame and offer.get("duration_and_deadline"):
            dur_box.text_frame.text = offer["duration_and_deadline"]

        lead_box = by_name.get("Rechteck 9")
        if lead_box and lead_box.has_text_frame and offer.get("lead_pitch"):
            lead_box.text_frame.text = offer["lead_pitch"]

        photo_shape = by_name.get("Dominik")
        photo_path = self._find_asset(offer.get("lead_photo_path"))
        if photo_shape and photo_path:
            self._replace_shape_with_picture(slide, photo_shape, photo_path)

    def _fill_slide_14_conditions(self, slide: Slide, payload: dict[str, Any]) -> None:
        cond = payload.get("conditions", {})
        boxes = [
            s for s in slide.shapes
            if s.has_text_frame and s.name.startswith("Rectangle 12")
        ]
        boxes.sort(key=lambda s: s.top)

        entries = [
            ("Projektzeitraum", cond.get("timeframe", "")),
            ("Vergütung", cond.get("remuneration", "")),
            ("Spesen und Reisekosten", cond.get("travel_expenses", "")),
            ("Leistungsbeschreibung und Ihre Mitarbeit", cond.get("scope_reference", "")),
            ("Projektstandort", cond.get("project_location", "")),
        ]

        for box, (heading, body) in zip(boxes, entries):
            if body:
                self._set_paragraphs_preserve_style(box, [heading, body])

    def _fill_slide_17_signature(self, slide: Slide, payload: dict[str, Any]) -> None:
        meta = payload.get("meta", {})
        by_name = {s.name: s for s in slide.shapes}

        rect = by_name.get("Rectangle 40")
        if rect and rect.has_text_frame and meta.get("valid_until"):
            self._replace_text_preserve_format(rect, "[Datum]", meta["valid_until"])
            self._replace_text_preserve_format(rect, "DATUM", meta["valid_until"])

        sample_logo = by_name.get("KuchenMeister")
        client_logo = self._find_asset(meta.get("client_logo_path"))
        if sample_logo:
            if client_logo:
                self._replace_shape_with_picture(slide, sample_logo, client_logo)
            else:
                self._remove_shape(sample_logo)

    # -------------------------------------------------------------------------
    # Dynamic Slide Expansion (Approach & Team)
    # -------------------------------------------------------------------------

    def _expand_approach_slides(
        self, prs: Presentation, template_idx: int, approach_list: list[dict[str, Any]]
    ) -> None:
        if not approach_list:
            self._delete_slide(prs, template_idx)
            return

        for offset, item in enumerate(approach_list):
            self._duplicate_slide(prs, template_idx)
            new_idx = len(prs.slides) - 1
            target_idx = template_idx + 1 + offset
            self._move_slide(prs, new_idx, target_idx)

            slide = prs.slides[target_idx]
            by_name = {s.name: s for s in slide.shapes}
            if "Titel 2" in by_name and by_name["Titel 2"].has_text_frame:
                by_name["Titel 2"].text_frame.text = item.get("title", f"Arbeitspaket {offset + 1}")
            if "Inhaltsplatzhalter 1" in by_name and by_name["Inhaltsplatzhalter 1"].has_text_frame:
                bullets = [f"• {b}" for b in item.get("bullets", [])[:7]]
                self._set_paragraphs_preserve_style(by_name["Inhaltsplatzhalter 1"], bullets)

        self._delete_slide(prs, template_idx)

    def _expand_team_slides(
        self, prs: Presentation, template_idx: int, team_members: list[dict[str, Any]]
    ) -> None:
        if not team_members:
            return

        chunks = [team_members[i:i + 2] for i in range(0, len(team_members), 2)]

        for offset, pair in enumerate(chunks):
            self._duplicate_slide(prs, template_idx)
            new_idx = len(prs.slides) - 1
            target_idx = template_idx + 1 + offset
            self._move_slide(prs, new_idx, target_idx)

            slide = prs.slides[target_idx]
            groups = [s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.GROUP]
            groups.sort(key=lambda g: g.left)

            for card_idx, group in enumerate(groups):
                if card_idx >= len(pair):
                    self._remove_shape(group)
                    continue
                self._populate_team_group(group, pair[card_idx])

        self._delete_slide(prs, template_idx)

    def _populate_team_group(self, group_shape: Any, member: dict[str, Any]) -> None:
        """Populates text sub-shapes inside a grouped consultant card."""
        text_items = [s for s in group_shape.shapes if s.has_text_frame]
        text_items.sort(key=lambda s: (s.top, s.left))

        name_role = f"{member.get('name', '')}\n{member.get('role', '')}".strip()
        bio = member.get("bio", "")
        skills = member.get("skills", [])
        exp = member.get("experience", "")

        if len(text_items) >= 1 and name_role:
            text_items[0].text_frame.text = name_role
        if len(text_items) >= 2 and bio:
            text_items[1].text_frame.text = bio
        if len(text_items) >= 3 and skills:
            self._set_paragraphs_preserve_style(text_items[2], [f"• {sk}" for sk in skills[:5]])
        if len(text_items) >= 4 and exp:
            text_items[3].text_frame.text = exp

    # -------------------------------------------------------------------------
    # Global Cleanup & Text Helpers
    # -------------------------------------------------------------------------

    def _iter_shapes_recursive(self, shapes_container: Any):
        """Yields all shapes including nested shapes inside GROUP shapes."""
        for shape in list(shapes_container):
            yield shape
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from self._iter_shapes_recursive(shape.shapes)

    def _remove_template_annotations(self, prs: Presentation) -> None:
        """Removes all designer instruction callouts across all slides."""
        for slide in prs.slides:
            for shape in list(self._iter_shapes_recursive(slide.shapes)):
                if shape.has_text_frame:
                    txt = shape.text_frame.text.strip()
                    if any(kw in txt for kw in self.ANNOTATION_KEYWORDS):
                        self._remove_shape(shape)

    def _apply_global_replacements(self, prs: Presentation, payload: dict[str, Any]) -> None:
        meta = payload.get("meta", {})
        offer_id = meta.get("offer_id", "AN00XXX")
        client = meta.get("client_name", "Kunde")
        title = meta.get("project_title", "Projekttitel")
        location = meta.get("client_location", "Ulm")
        date_str = meta.get("date", "")

        replacements = [
            ("AN000520", offer_id),
            ("AN000XXX", offer_id),
            ("AN00345", offer_id),
            ("ANXXXXX", offer_id),
            ("Münchner Leukämie Labor", client),
            ("IT-Zielbild und Umsetzungsfahrplan", title),
            ("Kundenname| Projekttitel", f"{client} | {title}"),
            ("Kundenname | Projekttitel", f"{client} | {title}"),
            ("Kundenname", client),
            ("KUNDE", client),
            ("Projekttitel", title),
            ("HAUPTSTANDORT", location),
            ("09.10.2026", date_str),
            ("DATUM", date_str),
        ]

        for slide in prs.slides:
            for shape in self._iter_shapes_recursive(slide.shapes):
                if shape.has_text_frame:
                    for old_s, new_s in replacements:
                        if new_s and old_s != new_s:
                            self._replace_text_preserve_format(shape, old_s, new_s)
                elif shape.has_table:
                    for row in shape.table.rows:
                        for cell in row.cells:
                            for old_s, new_s in replacements:
                                if new_s and old_s != new_s:
                                    self._replace_text_preserve_format(cell, old_s, new_s)

    @staticmethod
    def _replace_text_preserve_format(shape: Any, old_text: str, new_text: str) -> bool:
        if not shape.has_text_frame:
            return False
        any_replaced = False
        for paragraph in shape.text_frame.paragraphs:
            para_replaced = False
            for run in paragraph.runs:
                if old_text in run.text:
                    run.text = run.text.replace(old_text, new_text)
                    para_replaced = True
                    any_replaced = True
            if not para_replaced and old_text in paragraph.text:
                merged = paragraph.text.replace(old_text, new_text)
                if paragraph.runs:
                    paragraph.runs[0].text = merged
                    for r in paragraph.runs[1:]:
                        r.text = ""
                else:
                    paragraph.text = merged
                any_replaced = True
        return any_replaced

    @staticmethod
    def _set_paragraphs_preserve_style(shape: Any, lines: list[str]) -> None:
        if not shape.has_text_frame or not lines:
            return
        tf = shape.text_frame
        first_p = tf.paragraphs[0]
        base_font = first_p.runs[0].font if first_p.runs else None

        font_name = base_font.name if base_font else None
        font_size = base_font.size if base_font else None
        font_rgb = None
        if base_font and base_font.color and base_font.color.type:
            try:
                font_rgb = base_font.color.rgb
            except Exception:
                font_rgb = None

        tf.clear()
        for idx, line in enumerate(lines):
            p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
            run = p.add_run()
            run.text = line
            if font_name:
                run.font.name = font_name
            if font_size:
                run.font.size = font_size
            if font_rgb:
                try:
                    run.font.color.rgb = font_rgb
                except Exception:
                    pass

    @staticmethod
    def _remove_shape(shape: Any) -> None:
        el = shape._element
        if el.getparent() is not None:
            el.getparent().remove(el)

    def _replace_shape_with_picture(self, slide: Slide, shape: Any, image_path: Path) -> None:
        left, top, width = shape.left, shape.top, shape.width
        self._remove_shape(shape)
        slide.shapes.add_picture(str(image_path), left, top, width=width)

    @staticmethod
    def _duplicate_slide(prs: Presentation, source_index: int) -> Slide:
        """
        Safely creates a new slide using python-pptx's standard add_slide API,
        assigns a guaranteed unique PackURI (preventing duplicate 'slide21.xml' entries
        after slide deletions), and copies visual shapes cleanly.
        """
        source_slide = prs.slides[source_index]
        new_slide = prs.slides.add_slide(source_slide.slide_layout)

        # Ensure unique partname across the entire OPC package so zip never gets duplicate names
        existing_uris = {str(part.partname) for part in prs.part.package.iter_parts()}
        idx_num = len(prs.slides) + 1
        while f"/ppt/slides/slide{idx_num}.xml" in existing_uris:
            idx_num += 1
        new_slide.part.partname = PackURI(f"/ppt/slides/slide{idx_num}.xml")

        # Remove default layout placeholders on the newly added slide
        for shape in list(new_slide.shapes):
            sp = shape._element
            if sp.getparent() is not None:
                sp.getparent().remove(sp)

        # Copy image/media relationships from source_slide to new_slide and remap rIds
        r_id_map: dict[str, str] = {}
        for rel_id, rel in source_slide.part.rels.items():
            if "image" in rel.reltype:
                new_r_id = new_slide.part.relate_to(rel.target_part, rel.reltype)
                r_id_map[rel_id] = new_r_id

        r_ns_attrs = (qn("r:embed"), qn("r:id"), qn("r:link"))

        for idx, shape in enumerate(source_slide.shapes):
            new_el = copy.deepcopy(shape._element)
            has_unmapped_rel = False

            for node in new_el.iter():
                if node.tag.endswith("cNvPr"):
                    node.set("id", str(100000 + idx * 100 + (len(prs.slides) % 90)))
                for attr in r_ns_attrs:
                    val = node.get(attr)
                    if val:
                        if val in r_id_map:
                            node.set(attr, r_id_map[val])
                        else:
                            has_unmapped_rel = True

            # Skip shapes whose external XML relationship (e.g., OLE/chart/diagram) wasn't copied
            if not has_unmapped_rel:
                new_slide.shapes._spTree.insert_element_before(new_el, "p:extLst")

        return new_slide

    @staticmethod
    def _move_slide(prs: Presentation, old_index: int, new_index: int) -> None:
        xml_slides = prs.slides._sldIdLst
        slides = list(xml_slides)
        if old_index == new_index or old_index < 0 or old_index >= len(slides):
            return
        target_el = slides[old_index]
        xml_slides.remove(target_el)
        xml_slides.insert(new_index, target_el)

    @staticmethod
    def _delete_slide(prs: Presentation, index: int) -> None:
        sldIdLst = prs.slides._sldIdLst
        if index < 0 or index >= len(sldIdLst):
            return
        sldId = sldIdLst[index]
        rId = sldId.get(qn("r:id"))
        if rId and rId in prs.part.rels:
            prs.part.drop_rel(rId)
        sldIdLst.remove(sldId)

    # -------------------------------------------------------------------------
    # Corporate Gantt Chart Renderer
    # -------------------------------------------------------------------------

    @staticmethod
    def _render_gantt_png(tasks: list[dict[str, Any]]) -> bytes:
        fig, ax = plt.subplots(figsize=(12.0, max(3.0, len(tasks) * 0.55)), dpi=200)
        fig.patch.set_alpha(0.0)
        ax.patch.set_alpha(0.0)

        primary_teal = "#3B8296"
        grid_color = "#CBD5E1"

        y_positions = list(range(len(tasks)))[::-1]
        labels = [t.get("task", f"Phase {i + 1}") for i, t in enumerate(tasks)]

        max_week = 1
        for idx, task in enumerate(tasks):
            y = y_positions[idx]
            try:
                start = max(1, int(task.get("start_week", 1)))
            except (TypeError, ValueError):
                start = 1
            try:
                end = max(start, int(task.get("end_week", start + 1)))
            except (TypeError, ValueError):
                end = start + 1

            duration = end - start + 1
            max_week = max(max_week, end)

            ax.barh(y, duration, left=start - 0.5, height=0.45, color=primary_teal, alpha=0.92)
            pt = task.get("pt", "")
            if pt:
                ax.text(
                    end + 0.65,
                    y,
                    str(pt),
                    va="center",
                    ha="left",
                    fontsize=9,
                    fontweight="bold",
                    color="#475569",
                )

        ax.set_yticks(y_positions)
        ax.set_yticklabels(labels, fontsize=9.5, fontweight="bold", color="#1E293B")
        ax.set_xlim(0.5, max_week + 2.2)
        ax.set_xticks(range(1, max_week + 1))
        ax.set_xticklabels([f"KW {w}" for w in range(1, max_week + 1)], fontsize=8.5, color="#475569")
        ax.xaxis.grid(True, linestyle="--", alpha=0.6, color=grid_color)
        ax.set_axisbelow(True)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        ax.spines["left"].set_color(grid_color)
        ax.spines["bottom"].set_color(grid_color)

        plt.tight_layout()
        out = io.BytesIO()
        plt.savefig(out, format="png", transparent=True, dpi=200)
        plt.close(fig)
        out.seek(0)
        return out.getvalue()