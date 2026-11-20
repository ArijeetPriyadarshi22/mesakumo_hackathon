"""Document and checklist forms; escaped untrusted content throughout."""
from html import escape as h
from ..requirements import KINDS, tender_passages

def documents_view(project, form):
    body = '<p>Upload UTF-8 TXT, text PDF or DOCX (15 MB maximum). Sources are untrusted document content. Inspect extraction completeness before use.</p>'
    if not project.synthetic:
        body += form(project, 0, 'upload', '<label>File<input type="file" name="file" accept=".txt,.pdf,.docx" required></label><label>Role<select name="role"><option value="tender">Tender</option><option value="knowledge">Company knowledge</option><option value="text_module">Reusable text module</option><option value="template">Corporate template (store only)</option></select></label><button>Upload and extract text</button>').replace('<form method=', '<form enctype="multipart/form-data" method=')
    else:
        body += '<p>Create a document project to upload files; fixture records stay separate.</p>'
    for doc in project.documents:
        body += f'<article><b>{h(doc.name)} · {h(doc.role)}</b><p>ID: {h(doc.id)}<br>Version SHA-256: {h(doc.version)}<br>{h(doc.status)}</p><p>{h(doc.error)}</p></article>'
    body += '<h3>Extracted passages</h3><p>DOCX body paragraphs include table cells; pagination, headers, footers, images and text boxes are not supported. No page numbers are inferred. Template rendering is unavailable. Templates are stored only; DOCX export mapping awaits template inspection.</p>'
    for p in project.passages:
        body += f'<details><summary>{h(p.document_id)} · {h(p.location)} · {h(p.document_version[:12])}</summary><pre style="white-space:pre-wrap">{h(p.excerpt)}</pre><p>Passage ID: {h(p.id)}</p></details>'
    return body

def checklist_view(project, form):
    body = '<p>Candidates require human inspection. Structural validation cannot prove semantic correctness or complete obligation recovery. Evidence matching is unavailable.</p>'
    if not project.synthetic:
        body += form(project, 1, 'extract', '<p>This sends current tender passages only to the configured, approved Requesty connection. Candidates are appended; repeat runs may add duplicates. Existing edits are preserved if a call fails.</p><button>Extract candidate requirements via configured provider</button>')
    sources = tender_passages(project)
    def editor(item=None):
        selected = item.source_ids if item else []
        options = ''.join(f'<option value="{h(k)}" {"selected" if item and item.kind == k else ""}>{h(k)}</option>' for k in KINDS)
        fields = f'<input type="hidden" name="target" value="{h(item.id if item else "")}"><label>Statement<textarea name="text" required>{h(item.text if item else "")}</textarea></label><label>Classification<select name="kind">{options}</select></label>'
        fields += '<label>Constraints (one per line)<textarea name="constraints">' + h('\n'.join(item.constraints) if item else '') + '</textarea></label><p>Select current tender sources:</p>'
        for p in sources:
            fields += f'<label><input type="checkbox" name="sources" value="{h(p.id)}" {"checked" if p.id in selected else ""}>{h(p.location)} · {h(p.id)}</label>'
        fields += '<label>Reviewer<input name="reviewer" required></label><label>Provenance / correction reason<input name="note" required></label><button>Save requirement</button>'
        return form(project, 1, 'save_requirement', fields)
    for item in project.requirements:
        body += f'<article><h3>{h(item.id)} · {h(item.text)}</h3><p>{h(item.kind)} · {"Reviewed" if item.reviewed else "Unreviewed"}</p><p>Provenance: {h(item.provenance)}</p><p>Constraints: {h("; ".join(item.constraints) or "None recorded — inspect source")}</p>'
        for source in item.source_ids:
            p = next((p for p in project.passages if p.id == source), None)
            if p:
                body += f'<blockquote id="source-{h(p.id)}"><b>{h(p.document_id)} · {h(p.location)} · version {h(p.document_version[:12])}</b><pre style="white-space:pre-wrap">{h(p.excerpt)}</pre></blockquote>'
                if p.id not in {s.id for s in sources}:
                    body += '<p>Historical source version — update to a current tender source before review.</p>'
        matches = [m for m in project.evidence if m.requirement_id == item.id]
        if not matches:
            body += '<p>No evidence found in supplied records. Evidence search has not run.</p>'
        for match in matches:
            body += f'<p>Evidence quality: {h(match.quality)} · Decision: {h(match.decision)} · {h(match.limitations)}</p>'
            for source in match.source_ids:
                passage = next((p for p in project.passages if p.id == source), None)
                if passage:
                    body += f'<blockquote>{h(passage.document_id)} · {h(passage.location)}<br>{h(passage.excerpt)}</blockquote>'
        body += editor(item)
        body += form(project, 1, 'delete_requirement', f'<input type="hidden" name="target" value="{h(item.id)}"><label>Reviewer<input name="reviewer" required></label><button>Delete requirement</button>') + '</article>'
    body += '<h3>Add a requirement manually</h3>' + editor()
    body += form(project, 1, 'review_checklist', '<p>Inspect the whole tender and checklist first. Reviewing does not guarantee every obligation was found.</p><label>Reviewer<input name="reviewer" required></label><button>Mark requirement checklist reviewed</button>')
    body += '<button disabled>Match company evidence — unavailable</button>'
    return body
