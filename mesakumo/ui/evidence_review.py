"""Task 2 review page, local persistence, and CSV export."""

import csv
import hashlib
import io
import json
import os
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from uuid import uuid4

from ..domain import Requirement
from ..evidence import EVIDENCE_SYSTEM_INSTRUCTION
from ..evidence_workflow import (
    OPTIONAL_ROLES, ChecklistColumns, match_local_requirements, read_checklist, verify_local_reference,
    restore_search_status, verify_report_sources,
)
from ..models import DisabledModel, configured_model


LABELS = {'sufficient': 'Strong', 'partial': 'Partial', 'none': 'Gap'}


PAGE_STYLES = """
* { box-sizing: border-box; }
body { max-width: 55rem; margin: auto; padding: 1.5rem clamp(1rem, 4vw, 2.5rem) 4rem;
       font: 1rem/1.55 system-ui, sans-serif; color: #182f35; background: #f4f6f4; overflow-wrap: anywhere; }
header, section { margin: 1rem 0; padding: clamp(1rem, 3vw, 1.5rem); background: white;
                  border: 1px solid #ccd7d2; border-radius: .55rem; }
header { border-top: .3rem solid #14645b; }
h1, h2, h3 { line-height: 1.2; margin: 0 0 .75rem; }
p { margin: .65rem 0; }
article { padding: 1.25rem 0; border-top: 2px solid #dce5df; margin-top: 1.1rem; }
fieldset { border: 1px solid #becfc6; border-radius: .3rem; margin: .8rem 0; min-width: 0; }
legend { font-weight: 650; }
label { display: block; margin: .75rem 0; }
textarea, select, input:not([type=checkbox]) { display: block; max-width: 100%; margin-top: .3rem;
                                              padding: .45rem; }
textarea { width: 100%; min-height: 4rem; }
input, select, button, textarea { font: inherit; }
button, a.primary { display: inline-block; cursor: pointer; padding: .45rem .9rem; border-radius: .3rem;
                    border: 1px solid #80978b; background: white; color: #173f37; font-weight: 650; }
button.primary, a.primary { background: #14594e; color: white; border-color: #14594e; text-decoration: none; }
button:disabled { opacity: .6; cursor: default; }
a { color: #125e53; }
details { margin: .8rem 0; }
summary { cursor: pointer; color: #244c45; font-weight: 620; }
blockquote { margin: .4rem 0 1rem 1.3rem; padding: .5rem .85rem; border-left: 3px solid #559883;
             background: #f2f7f4; white-space: pre-wrap; }
[role=status] { background: #fff1d6; padding: .6rem .8rem; }
:focus-visible { outline: 3px solid #9c541b; outline-offset: 2px; }
"""

def _row_anchor(identifier):
    return 'requirement-' + identifier.encode('utf-8').hex()


def h(value):
    return escape(str(value), quote=True)


@dataclass(frozen=True)
class EvidenceSettings:
    inventory: Path
    participant_root: Path
    columns: ChecklistColumns = ChecklistColumns()

    @classmethod
    def from_environment(cls):
        inventory = os.environ.get('MESAKUMO_DOCUMENT_INVENTORY')
        root = os.environ.get('MESAKUMO_PARTICIPANT_ROOT')
        if not inventory or not root:
            return None
        mapping = os.environ.get('MESAKUMO_CHECKLIST_COLUMNS')
        columns = ChecklistColumns(**json.loads(Path(mapping).expanduser().read_text(encoding='utf-8-sig'))) if mapping else ChecklistColumns()
        return cls(Path(inventory).expanduser(), Path(root).expanduser(), columns)


class EvidenceReviewStore:
    """Keep report edits separate from Task 1's approved checklist."""

    def __init__(self, project_store):
        self.store = project_store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS task2_reviews '
                       '(project_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)')

    def load(self, project_id):
        with self.store.connect() as db:
            row = db.execute('SELECT revision, payload FROM task2_reviews WHERE project_id = ?',
                             (project_id,)).fetchone()
        return (row[0], restore_search_status(json.loads(row[1]))) if row else (0, None)

    def save(self, project_id, revision, report):
        with self.store.connect() as db:
            result = db.execute(
                'INSERT INTO task2_reviews VALUES (?, ?, ?) '
                'ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision, payload=excluded.payload '
                'WHERE task2_reviews.revision = ?',
                (project_id, revision + 1, json.dumps(report, ensure_ascii=False), revision),
            )
            if result.rowcount != 1:
                raise ValueError('The Task 2 report changed. Reload before saving.')


def _upstream_key(project):
    contents = json.dumps([asdict(item) for item in project.requirements], sort_keys=True)
    return hashlib.sha256(contents.encode()).hexdigest()


def _effective_review(row):
    review = row.get('review')
    if (review and review['fingerprint'] == row['fingerprint']
            and (row['search_state'] != 'complete' or review.get('quality') in LABELS)):
        return review
    return None


def _checked_csv_cell(value):
    value = str(value)
    if value.lstrip().startswith(('=', '+', '-', '@', '＝', '＋', '－', '＠')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def evidence_csv(report):
    """Export the current report as one row per included requirement."""
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow([
        'requirement_id', 'requirement', 'tender_source', 'constraints', 'category',
        'suggested_category', 'review_state', 'match', 'reference', 'evidence',
        'gap', 'reviewer_comment', 'search_state', 'search_issue', 'tender_source_ids',
        'search_scope', 'candidate_passages', 'evaluated_passages',
    ])
    for row in report['results']:
        if row.get('excluded'):
            continue
        req, suggestion = row['requirement'], row.get('suggested') or {}
        review = _effective_review(row)
        incomplete = row['search_state'] != 'complete'
        verdict = (review or suggestion).get('quality')
        selected = set((review or suggestion).get('source_ids', []))
        evidence = [item for item in row['evidence'] if item['passage_id'] in selected]
        clauses = suggestion.get('clauses', [])
        matched = [clause['support'] for clause in clauses
                   if clause['support'] and set(clause['source_ids']).issubset(selected)]
        if not matched:
            matched = [quote for item in evidence for quote in item['quotes']]
        values = [
            req['id'], req['text'], req['provenance'], '\n'.join(req['constraints']),
            'Incomplete' if incomplete else LABELS.get(verdict, ''),
            LABELS.get(suggestion.get('quality'), ''),
            ('Provisional' if incomplete else 'Reviewed') if review else ('Stale' if row.get('review') else 'Unreviewed'),
            '\n'.join(matched),
            '\n'.join(f"{item['path']} · {item['location']} · {item['document_version']}" for item in evidence),
            json.dumps(evidence, ensure_ascii=False),
            '\n'.join(clause['gap'] for clause in clauses if clause['gap']),
            (review or row.get('review') or {}).get('comment', ''), row['search_state'], row['error'],
            json.dumps(req['source_ids'], ensure_ascii=False),
            row.get('search_scope', ''), row.get('candidate_count', ''), row.get('evaluated_count', ''),
        ]
        writer.writerow(map(_checked_csv_cell, values))
    return stream.getvalue().encode('utf-8-sig')


class EvidencePage:
    def __init__(self, project_store, token, settings, model_factory=None):
        self.reviews = EvidenceReviewStore(project_store)
        self.token = token
        self.settings = settings
        self.model_factory = model_factory or (
            lambda: configured_model(system_instruction=EVIDENCE_SYSTEM_INSTRUCTION)
        )

    def url(self, project_id, *, requirement=None, **params):
        url = '/evidence?' + urlencode({'project': project_id, **params})
        return url + '#' + _row_anchor(requirement) if requirement else url

    def _form(self, project, revision, action, content, *, file_upload=False):
        encoding = ' enctype="multipart/form-data"' if file_upload else ''
        inputs = ''.join(f'<input type="hidden" name="{key}" value="{h(value)}">'
                         for key, value in {
                             'token': self.token, 'project': project.id,
                             'revision': revision, 'action': action,
                         }.items())
        return f'<form method="post" action="/evidence"{encoding}>{inputs}{content}</form>'

    def _model(self):
        issue = self._setup_issue()
        if issue:
            raise ValueError(issue)
        model = self.model_factory()
        if isinstance(model, DisabledModel):
            raise ValueError('Set up the approved Team 1 Requesty EU settings in TASK2_README.md, '
                             'then restart the workspace with python -m mesakumo.')
        return model

    def _match(self, items, roles, previous, full_scan=False):
        model = self._model()
        match = match_local_requirements(
            items, self.settings.inventory, self.settings.participant_root, include_roles=roles,
            model=model, previous_report=previous, search_readable_on_failure=True, full_scan=full_scan,
        )
        match['roles'] = list(roles)
        return match

    @staticmethod
    def _check_upstream(project, report):
        if report.get('upstream') and report['upstream'] != _upstream_key(project):
            raise ValueError('The Task 1 checklist changed. Match the currently approved requirements again.')

    def change(self, project, data, filename=None, content=None):
        field = lambda name: data.get(name, [''])[0]
        revision, report = self.reviews.load(project.id)
        if int(field('revision')) != revision:
            raise ValueError('The Task 2 report changed. Reload before saving.')
        if project.synthetic:
            raise ValueError('Open a document project to run Task 2 on company sources.')
        action = field('action')
        if action == 'match':
            roles = tuple(data.get('roles', []))
            skipped = []
            if filename:
                if Path(filename).suffix.casefold() != '.csv' or not content:
                    raise ValueError('Choose a nonempty approved requirements CSV.')
                if not self.settings:
                    self._model()
                with TemporaryDirectory(prefix='mesakumo-checklist-') as storage:
                    path = Path(storage) / 'approved-checklist.csv'
                    path.write_bytes(content)
                    items, skipped = read_checklist(
                        path, self.settings.columns, approved_export=field('approved_export') == 'yes',
                    )
                source, upstream = Path(filename).name, None
            else:
                items = project.requirements
                if not items or not all(req.reviewed for req in items):
                    raise ValueError('Choose the approved Task 1 CSV or approve the project checklist before running matching.')
                source, upstream = 'Approved project checklist', _upstream_key(project)
            previous = report
            report = self._match(items, roles, report)
            report.update(checklist_name=source, upstream=upstream, unapproved_ids=skipped)
            # A match against a new Task 1 export restores edits and exclusions for identical requirements.
            if previous:
                prior = {row['requirement']['id']: row for row in previous['results']}
                for row in report['results']:
                    old = prior.get(row['requirement']['id'])
                    if old and old['requirement'] == row['requirement']:
                        row['excluded'] = old.get('excluded', False)
        elif report is None:
            raise ValueError('Run Task 2 matching before reviewing results.')
        else:
            self._check_upstream(project, report)
            if action in ('add_requirement', 'rerun'):
                if action == 'rerun':
                    items = [Requirement(**row['requirement']) for row in report['results'] if not row.get('excluded')]
                else:
                    if not field('text').strip():
                        raise ValueError('Enter a requirement before adding it.')
                    items = [Requirement('T2-' + uuid4().hex[:10], field('text').strip(), [], reviewed=True,
                                         constraints=field('constraints').splitlines(),
                                         provenance=field('tender_source').strip() or 'Added in Task 2 review')]
                fresh = self._match(items, tuple(report['roles']), report, full_scan=field('full_scan') == 'yes')
                if report['source_reading'] != fresh['source_reading']:
                    for row in report['results']:
                        row['search_state'] = 'incomplete'
                        row['error'] = 'Local evidence changed. Rerun this requirement before relying on the result.'
                if action == 'rerun':
                    removed = [row for row in report['results'] if row.get('excluded')]
                    report.update(fresh | {'results': fresh['results'] + removed})
                else:
                    report.update(search=fresh['search'], source_reading=fresh['source_reading'])
                    report['results'].extend(fresh['results'])
            else:
                row = next((r for r in report['results'] if r['requirement']['id'] == field('target')), None)
                if row is None:
                    raise ValueError('Unknown report requirement.')
                if action in ('exclude', 'restore'):
                    row['excluded'] = action == 'exclude'
                elif action == 'save':
                    self._save_review(report, row, data)
                else:
                    raise ValueError('Unknown Task 2 action.')
        self.reviews.save(project.id, revision, report)

    def _save_review(self, report, row, data):
        field = lambda name: data.get(name, [''])[0].strip()
        if row.get('excluded'):
            raise ValueError('Restore this requirement before editing it.')
        ids = list(dict.fromkeys(data.get('citations', [])))
        choices = {item['passage_id']: item for item in row['evidence']}
        if any(identifier not in choices for identifier in ids):
            raise ValueError('Choose one of the verified references on the report.')
        if field('document') or field('location') or field('quote'):
            if not self.settings:
                raise ValueError('Local source settings are required to verify a new reference.')
            citation = verify_local_reference(
                report, self.settings.participant_root, field('document'), field('location'), field('quote'),
            )
            identifier = citation['passage_id']
            if identifier in choices:
                quotes = choices[identifier]['quotes']
                if citation['quotes'][0] not in quotes:
                    quotes.extend(citation['quotes'])
            else:
                row['evidence'].append(citation)
            if identifier not in ids:
                ids.append(identifier)
        quality = field('quality') if row['search_state'] == 'complete' else None
        if row.get('search_scope') == 'retrieved candidates' and quality in ('partial', 'none'):
            raise ValueError('Rerun with all readable passages before saving a Partial or Gap verdict.')
        if row['search_state'] == 'complete' and (
                quality not in LABELS or bool(ids) != (quality != 'none')):
            raise ValueError('Select evidence for Strong or Partial, or clear the evidence for Gap.')
        row['review'] = {'quality': quality, 'source_ids': ids, 'comment': field('comment'),
                         'fingerprint': row['fingerprint']}

    def download(self, project):
        _, report = self.reviews.load(project.id)
        if report is None:
            raise ValueError('Match a requirement checklist before downloading a report.')
        self._check_upstream(project, report)
        if self.settings is None:
            raise ValueError('Configure the participant input folder to verify the report’s source versions.')
        verify_report_sources(report, self.settings.participant_root)
        return evidence_csv(report)

    def _setup_issue(self):
        if (not self.settings or not self.settings.inventory.is_file()
                or not self.settings.participant_root.is_dir()):
            return ('Set MESAKUMO_DOCUMENT_INVENTORY to the local inventory CSV and '
                    'MESAKUMO_PARTICIPANT_ROOT to the participant folder. See TASK2_README.md, '
                    'then restart the workspace with python -m mesakumo.')
        return ''

    def _match_form(self, project, revision, report):
        ready = bool(project.requirements) and all(req.reviewed for req in project.requirements)
        columns = self.settings.columns if self.settings else ChecklistColumns()
        if ready:
            hint = f'Leave the picker empty to use this project’s {len(project.requirements)} approved requirements.'
        else:
            hint = 'Choose the approved CSV from Task 1. This project has no fully approved checklist yet.'
        descriptions = {
            'Team information': 'Consultant profiles and qualification records, including certificates.',
            'Historical proposal': 'Earlier bids and attachments. A proposed activity alone does not establish delivery.',
        }
        role_fields = ''.join(
            f'<label><input type="checkbox" name="roles" value="{h(role)}" '
            f'{"checked" if report and role in report.get("roles", []) else ""}> '
            f'<strong>{h(role)}</strong> — {h(descriptions[role])}</label>'
            for role in OPTIONAL_ROLES
        )
        issue = self._setup_issue()
        fields = (
            '<section><h2>1. Choose requirements</h2>'
            f'<p id="checklist-help">{h(hint)}</p><label><strong>Approved Task 1 CSV</strong><br>'
            f'<input type="file" name="file" accept=".csv" aria-describedby="checklist-help" '
            f'{"" if ready else "required"}></label>'
            '<label><input type="checkbox" name="approved_export" value="yes"> '
            'Task 1 confirmed every row is approved and this CSV has no approval column.</label>'
            '<details><summary>CSV format and headers</summary><p>Use a UTF-8 checklist with one requirement per row. '
            f'The configured headers are <code>{h(columns.id)}</code>, <code>{h(columns.text)}</code> and '
            f'<code>{h(columns.approval)}</code>. Rows marked draft or rejected are skipped. '
            'Comma, semicolon and tab delimiters are supported.</p></details></section>'
            '<section><h2>2. Choose sources and run</h2><p>The supplied inventory includes the two previous-project '
            'PowerPoint decks automatically. You can include these additional local sources.</p>'
            '<fieldset><legend>Optional source groups</legend>' + role_fields + '</fieldset>'
            '<p>Matching sends the requirement and selected evidence passages to the configured model.</p>'
            + (f'<p role="status">{h(issue)}</p>' if issue else '')
            + f'<button class="primary" {"disabled" if issue else ""}>Run evidence matching</button></section>'
        )
        return self._form(project, revision, 'match', fields, file_upload=True)

    def render(self, project):
        revision, report = self.reviews.load(project.id)
        home = '/?' + urlencode({'project': project.id, 'stage': 1})
        body = (f'<nav><a href="{h(home)}">Back to requirements</a></nav><header><h1>Evidence Matching</h1>'
                '<p>Bring an approved requirements checklist, find company evidence for each requirement, '
                'then review and export your findings.</p>'
                f'<p>Project · {h(project.name)}</p></header>')
        if project.synthetic:
            return body + '<p><a href="/">Create or open a document project</a> to use your approved checklist.</p>'
        form = self._match_form(project, revision, report)
        if report is None:
            return (body + form + '<section><h2>3. Review evidence and gaps</h2>'
                    '<p>Run matching to see a suggested verdict and sources for each requirement.</p></section>'
                    '<section><h2>4. Save and download</h2><p>After matching, save each review here and '
                    'download the findings as a separate CSV.</p></section>')
        try:
            self._check_upstream(project, report)
        except ValueError as error:
            return body + f'<p role="status">{h(error)}</p>' + form
        body += '<details><summary>Change checklist or sources for a new run</summary>' + form + '</details>'
        search = report['search']
        groups = ', '.join(['Previous project sources', *report.get('roles', [])])
        body += (f'<section><h2>3. Review evidence and gaps</h2>'
                 f'<p><strong>{h(report["checklist_name"])}</strong> · {h(groups)}</p>'
                 f'<p>{search["read_files"]} files read · {search.get("partial_files", 0)} partially read · '
                 f'{search["failed_files"]} failed · {search["passages"]} readable passages</p>'
                 '<p><b>Strong</b> means all necessary parts have support. <b>Partial</b> means some parts '
                 'still lack support. <b>Gap</b> means a completed search found no support. '
                 'An <b>Incomplete</b> search leaves any findings provisional.</p>')
        if report.get('unapproved_ids'):
            body += f'<p>Unapproved checklist rows skipped: {len(report["unapproved_ids"])}.</p>'
        issues = [s for s in report['source_reading'] if s['state'] in ('partial', 'failed')]
        if issues:
            body += '<p><strong>Source coverage issues</strong></p><ul>' + ''.join(
                f'<li>{h(s["path"])} — {h(s.get("reason", s["state"]))}</li>' for s in issues) + '</ul>'
        for row in report['results']:
            body += self._render_row(project, revision, report, row)
        active = [row for row in report['results'] if not row.get('excluded')]
        pending = [row for row in active if not _effective_review(row)]
        incomplete = sum(row['search_state'] != 'complete' for row in active)
        body += (f'</section><section><h2>4. Save and download</h2><p>{len(active)} included requirements · '
                 f'{len(pending)} need a saved review · {incomplete} have an incomplete search.</p>'
                 + (f'<a href="#{_row_anchor(pending[0]["requirement"]["id"])}">Go to first row needing review</a>'
                    if pending else '')
                 + '<p>Choose <strong>Save review</strong> on each row you edit. The download includes every '
                 'included row as last saved. Unreviewed suggestions and provisional findings keep their status.</p>'
                 f'<a class="primary" href="{h(self.url(project.id, download="csv"))}">Download saved report (CSV)</a></section>')
        details = ''.join(f'<li>{h(s["path"] or s["role"])} · {h(s["state"])} {h(s.get("reason", ""))}</li>'
                          for s in report['source_reading'])
        body += ('<details><summary>Source diagnostics and report adjustments</summary>'
                 f'<p>{h(search["strategy"])} · {search["skipped_inventory_rows"]} inventory entries skipped</p>'
                 f'<details><summary>Source reading report</summary><ul>{details}</ul></details>')
        body += self._form(project, revision, 'rerun', '<label><input type="checkbox" name="full_scan" value="yes"> '
                           'Scan all readable passages in original order</label><button>Rerun current requirements</button>')
        body += self._form(project, revision, 'add_requirement',
                           '<h3>Add a requirement to this report</h3><label>Requirement<textarea name="text" required></textarea></label>'
                           '<label>Constraints, one per line<textarea name="constraints"></textarea></label>'
                           '<label>Buyer reference<input name="tender_source"></label><button>Add and match requirement</button>')
        return body + '</details>'

    def _render_row(self, project, revision, report, row):
        req, suggestion = row['requirement'], row.get('suggested') or {}
        review = _effective_review(row)
        target = f'<input type="hidden" name="target" value="{h(req["id"])}">'
        heading = f'<article id="{_row_anchor(req["id"])}"><h3>{h(req["id"])} · {h(req["text"])}</h3><p>Buyer reference: {h(req["provenance"])}</p>'
        if row.get('excluded'):
            return heading + self._form(project, revision, 'restore', target + '<button>Restore requirement</button>') + '</article>'
        if req.get('constraints'):
            heading += '<p>Mandatory parts: ' + h(' · '.join(req['constraints'])) + '</p>'
        if not review:
            heading += '<p><strong>Needs review</strong></p>'
        elif row['search_state'] != 'complete':
            heading += '<p><strong>Saved provisional review</strong></p>'
        if row['search_state'] != 'complete':
            heading += f'<p role="status"><strong>Search incomplete:</strong> {h(row["error"])}</p>'
        if row.get('search_scope'):
            heading += (f'<details><summary>Search coverage</summary><p>{h(row["search_scope"])} · {row["candidate_count"]} shortlisted · '
                        f'{row["evaluated_count"]} passages evaluated</p></details>')
        if suggestion:
            label = 'Suggested' if row['search_state'] == 'complete' else 'Provisional'
            heading += f'<p>{label}: <strong>{h(LABELS[suggestion["quality"]])}</strong></p>'
            for clause in suggestion['clauses']:
                if clause['support']:
                    heading += f'<p>Supported: {h(clause["support"])}</p>'
                if clause['gap']:
                    heading += f'<p>Missing: {h(clause["gap"])}</p>'
        if review and row['search_state'] == 'complete' and review['quality']:
            heading += f'<p>Saved verdict: <strong>{h(LABELS[review["quality"]])}</strong></p>'
        elif row.get('review') and not review:
            heading += '<p>Earlier review is stale. Check the current sources before saving again.</p>'
        current = review or suggestion
        fields = target
        if row['search_state'] == 'complete':
            verdicts = ''.join(
                f'<option value="{key}" {"selected" if current.get("quality") == key else ""}>{label}</option>'
                for key, label in LABELS.items()
            )
            fields += '<label>Review verdict<select name="quality">' + verdicts + '</select></label>'
        fields += '<fieldset><legend>Company evidence · select references to keep</legend>'
        for item in row['evidence']:
            identifier = item['passage_id']
            fields += (f'<label><input type="checkbox" name="citations" value="{h(identifier)}" '
                       f'{"checked" if identifier in current.get("source_ids", []) else ""}> '
                       f'{h(item["path"])} · {h(item["location"])}</label>'
                       + ''.join(f'<blockquote>{h(quote)}</blockquote>' for quote in item['quotes']))
        if not row['evidence']:
            fields += '<p>No verified reference was suggested.</p>'
        fields += '</fieldset>'
        documents = ''.join(f'<option value="{h(s["document_id"])}">{h(s["path"])}</option>'
                            for s in report['source_reading'] if s['state'] in ('read', 'partial'))
        if documents:
            fields += ('<details><summary>Add evidence from an original file</summary>'
                       '<label>Evidence file<select name="document"><option value="">Select a file</option>'
                       + documents + '</select></label>'
                       '<label>Exact location<input name="location" placeholder="Slide 18 or Page 3"></label>'
                       '<label>Exact excerpt<textarea name="quote"></textarea></label></details>')
        comment = (row.get('review') or {}).get('comment', '')
        fields += f'<label>Comment or follow-up<textarea name="comment">{h(comment)}</textarea></label><button>Save review</button>'
        return (heading + self._form(project, revision, 'save', fields)
                + '<details><summary>Adjust this report</summary>'
                + self._form(project, revision, 'exclude', target + '<button>Remove requirement from report</button>')
                + '</details></article>')
