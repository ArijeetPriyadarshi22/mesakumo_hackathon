"""Run Task 2 from an approved checklist and a local document inventory."""

import argparse
import csv
import hashlib
import io
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory
from unicodedata import normalize

from .documents import MAX_FILE_BYTES, MAX_PPTX_FILE_BYTES, intake
from .domain import EvidenceMatch, Project, Requirement
from .evidence import EvidenceMatchingError, PassageIndex, assess_requirements, resolve_evidence_citations
from .models import LanguageModel


BASE_ROLE = 'Previous project sources'
OPTIONAL_ROLES = ('Team information', 'Historical proposal')
SUPPORTED_EXTENSIONS = {'.pptx', '.pdf', '.docx', '.txt'}
QUALITY = ('sufficient', 'partial', 'none')
REPORT_VERSION = 1
UNSUPPORTED_FORMAT = 'File format is unsupported by the evidence reader.'
INVENTORY_FIELDS = ('Primary role', 'Location type', 'Path relative to participant input folder')


@dataclass(frozen=True)
class ChecklistColumns:
    """Default names for the local fixture. Set a mapping for the real Task 1 export."""

    id: str = 'requirement_id'
    text: str = 'requirement_text'
    approval: str = 'approval'
    constraints: str = 'constraints'
    tender_source: str = 'tender_source'
    source_ids: str = 'tender_source_ids'


def _csv_rows(path: Path, required: tuple[str, ...]):
    content = path.read_text(encoding='utf-8-sig')
    for delimiter in (',', ';', '\t'):
        stream = io.StringIO(content, newline='')
        reader = csv.reader(stream, delimiter=delimiter, strict=True)
        try:
            header = [field.strip() for field in next(reader, [])]
        except csv.Error:
            continue
        if not set(required).issubset(header):
            continue
        if '' in header or len(set(header)) != len(header):
            raise ValueError(f'{path.name} has blank or duplicate column names.')
        try:
            for row in reader:
                if not any(field.strip() for field in row):
                    continue
                if len(row) != len(header):
                    raise ValueError(f'{path.name} row ending at line {reader.line_num} has the wrong number of fields.')
                yield reader.line_num, dict(zip(header, (field.strip() for field in row), strict=True))
        except csv.Error as error:
            raise ValueError(f'{path.name} contains malformed CSV near line {reader.line_num}.') from error
        return
    raise ValueError(f"{path.name} must contain these columns: {', '.join(required)}.")


def _list_field(value: str, path: Path, line: int, column: str) -> list[str]:
    if not value.startswith('['):
        return [part.strip() for part in value.splitlines() if part.strip()]
    try:
        values = json.loads(value)
    except ValueError:
        values = None
    if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f'{path.name} line {line} has an invalid list in {column}.')
    return values


def read_checklist(path: Path, columns: ChecklistColumns, *, approved_export: bool = False):
    """Import approved rows; an approval-less export needs explicit operator attestation."""
    configured_columns = list(asdict(columns).values())
    if any(not isinstance(name, str) or not name.strip() for name in configured_columns):
        raise ValueError('Checklist column mapping must contain nonempty column names.')
    if len(configured_columns) != len(set(configured_columns)):
        raise ValueError('Checklist column names in the mapping must be distinct.')
    required = (columns.id, columns.text)
    if not approved_export:
        required += (columns.approval,)
    requirements, skipped, seen = [], [], set()
    for line, row in _csv_rows(path, required):
        identifier, text = row[columns.id], row[columns.text]
        if not identifier or identifier in seen or not text:
            raise ValueError(f'{path.name} line {line} needs a unique requirement ID and nonempty text.')
        seen.add(identifier)
        state = row.get(columns.approval, 'approved').casefold()
        if state in ('', 'pending', 'unreviewed', 'draft', 'rejected', 'not approved', 'no', 'false', '0'):
            skipped.append(identifier)
            continue
        if state not in ('approved', 'freigegeben', 'yes', 'true', '1'):
            raise ValueError(f'{path.name} line {line} has an unknown approval status.')
        constraints = _list_field(row.get(columns.constraints, ''), path, line, columns.constraints)
        source_ids = _list_field(row.get(columns.source_ids, ''), path, line, columns.source_ids)
        requirements.append(Requirement(
            identifier, text, source_ids, reviewed=True, constraints=constraints,
            provenance=row.get(columns.tender_source, '') or 'Tender source not supplied by Task 1',
        ))
    if not requirements:
        raise ValueError('The checklist has no approved requirements.')
    return requirements, skipped


def _source_path(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if not relative or '\\' in relative or posix.is_absolute() or '..' in posix.parts or PureWindowsPath(relative).drive:
        raise ValueError('Expected a relative path inside the participant directory.')
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('The path resolves outside the participant directory.')
    return path


def _rendition_key(row):
    # The supplied inventory explicitly identifies paired renderings with this note.
    if row['Primary role'] == 'Historical proposal' and 'pptx and pdf versions' in row.get('Notes', '').casefold():
        return (row.get('Folder', ''), row.get('Document type', ''))
    return None


def _load_company_sources(root, inventory, storage, include_roles, include_paths):
    root = root.resolve()
    rows = [row for _, row in _csv_rows(inventory, INVENTORY_FIELDS)]
    path_column = INVENTORY_FIELDS[-1]
    unknown = include_paths - {row[path_column] for row in rows}
    if unknown:
        raise ValueError('A requested company source path is not present in the inventory.')
    # Prefer the presentation over its PDF rendering when both are selected.
    rows.sort(key=lambda row: 0 if Path(row[path_column]).suffix.lower() == '.pptx' else 1)
    project = Project('task2-run', 'Company evidence', False, 'Selected local evidence sources')
    reading, read_paths, read_digests, renditions = [], set(), set(), set()
    for row in rows:
        role, relative = row['Primary role'], row[path_column]
        record = {'path': relative if role != 'Access credentials' else '', 'role': role, 'state': 'skipped'}
        reading.append(record)
        selected = role in ({BASE_ROLE} | include_roles) or relative in include_paths
        if not selected or role not in (BASE_ROLE, *OPTIONAL_ROLES):
            if relative in include_paths:
                raise ValueError('A requested path does not have an eligible evidence role.')
            record['reason'] = 'Role or path not selected for company evidence.'
            continue
        if row['Location type'].casefold() != 'file' or Path(relative).name.startswith(('.', '~$')):
            record['reason'] = 'Archive entry or temporary file.'
            continue
        suffix = Path(relative).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            record.update(state='failed', reason=UNSUPPORTED_FORMAT)
            continue
        rendition = _rendition_key(row)
        if rendition in renditions:
            record['reason'] = 'Another rendering of this historical proposal was already read.'
            continue
        try:
            path = _source_path(root, relative)
            if path in read_paths:
                record['reason'] = 'Duplicate local path.'
                continue
            limit = MAX_PPTX_FILE_BYTES if suffix == '.pptx' else MAX_FILE_BYTES
            if not path.is_file():
                raise ValueError('The selected file is missing or is not a regular file.')
            if path.stat().st_size > limit:
                raise ValueError('The file exceeds the local reader size limit.')
            with path.open('rb') as file:
                content = file.read(limit + 1)
            digest = hashlib.sha256(content).hexdigest()
            if digest in read_digests:
                record['reason'] = 'Duplicate file content.'
                continue
            # Load independently so same-named files in different directories retain separate lineages.
            local = Project(project.id, project.name, False, project.scope)
            unreadable = []
            doc = intake(local, storage, path.name, content, role='knowledge', unreadable_pdf_pages=unreadable)
            if unreadable:
                record['unreadable_pages'] = unreadable
            if doc.error:
                raise ValueError(doc.error)
        except OSError:
            record.update(state='failed', reason='The selected local file could not be read.')
            continue
        except (ValueError, RuntimeError) as error:
            record.update(state='failed', reason=str(error))
            continue
        old_id = doc.id
        key = normalize('NFC', path.relative_to(root).as_posix()).encode('utf-8')
        doc.id = 'D-' + hashlib.sha256(key).hexdigest()[:24]
        project.documents.append(doc)
        for passage in local.passages:
            passage.id = doc.id + passage.id.removeprefix(old_id)
            passage.document_id = doc.id
            project.passages.append(passage)
        record.update(state='read', document_id=doc.id, document_version=doc.version,
                      passages=len(local.passages))
        if unreadable:
            record.update(state='partial', reason='No extractable text on physical pages: '
                          + ', '.join(map(str, unreadable)))
        read_paths.add(path)
        read_digests.add(digest)
        if rendition:
            renditions.add(rendition)
    return project, reading


def _fingerprint(requirement, sources):
    value = json.dumps([requirement, sorted(sources)], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _candidates(project, assessment, source_map):
    quotes = {}
    for finding in assessment.findings:
        quotes.setdefault(finding['source_id'], []).append(finding['quote'])
    candidate_match = EvidenceMatch('', assessment.match.requirement_id, list(quotes), 'none', '')
    return [
        {**asdict(citation), 'path': source_map[citation.document_id]['path'],
         'role': source_map[citation.document_id]['role'],
         'quotes': quotes[citation.passage_id]}
        for citation in resolve_evidence_citations(project, candidate_match)
    ]


def _restore_review(row, previous, passages, source_map):
    if not previous or not previous.get('review'):
        return
    review = previous['review']
    row['review'] = review
    if review.get('fingerprint') != row['fingerprint']:
        return
    present = {item['passage_id']: item for item in row['evidence']}
    for item in previous.get('evidence', []):
        identifier = item['passage_id']
        passage = passages.get(identifier)
        if (identifier in review.get('source_ids', []) and passage
                and passage.document_version == item['document_version']
                and item.get('quotes') and all(quote and quote in passage.excerpt for quote in item['quotes'])):
            if identifier in present:
                quotes = present[identifier]['quotes']
                quotes.extend(quote for quote in item['quotes'] if quote not in quotes)
                continue
            source = source_map[passage.document_id]
            row['evidence'].append({
                'passage_id': identifier, 'document_id': passage.document_id,
                'document_name': PurePosixPath(source['path']).name,
                'document_version': passage.document_version, 'location': passage.location,
                'path': source['path'], 'role': source['role'], 'quotes': item['quotes'],
            })
            present[identifier] = row['evidence'][-1]


def run_workflow(
    checklist: Path, inventory: Path, participant_root: Path, *,
    columns: ChecklistColumns = ChecklistColumns(), approved_export: bool = False,
    include_roles: tuple[str, ...] = (), include_paths: tuple[str, ...] = (),
    model: LanguageModel | None = None, previous_report: dict | None = None, full_scan: bool = False,
) -> dict:
    """Read local sources, run matching, and return the Task 2 review package."""
    requirements, skipped = read_checklist(checklist, columns, approved_export=approved_export)
    report = match_local_requirements(
        requirements, inventory, participant_root, include_roles=include_roles,
        include_paths=include_paths, model=model, previous_report=previous_report, full_scan=full_scan,
    )
    report['checklist'] = {'file': checklist.name, 'columns': asdict(columns),
                           'approved_export_attested': approved_export, 'unapproved_ids': skipped}
    return report


def match_local_requirements(
    requirements: list[Requirement], inventory: Path, participant_root: Path, *,
    include_roles: tuple[str, ...] = (), include_paths: tuple[str, ...] = (),
    model: LanguageModel | None = None, previous_report: dict | None = None, full_scan: bool = False,
    search_readable_on_failure: bool = False,
) -> dict:
    """Match an approved or explicitly reviewed Task 2 requirement list."""
    roles = set(include_roles)
    if roles - set(OPTIONAL_ROLES):
        raise ValueError('Only eligible company evidence roles can be added to matching.')
    if not participant_root.is_dir():
        raise ValueError('Participant input folder does not exist.')
    if not requirements or any(not req.reviewed for req in requirements):
        raise ValueError('Supply a nonempty list of approved requirements.')
    previous_rows = {row['requirement']['id']: row for row in _report_rows(previous_report)} if previous_report else {}
    results = []
    with TemporaryDirectory(prefix='mesakumo-task2-') as folder:
        project, reading = _load_company_sources(
            participant_root, inventory, Path(folder), roles, set(include_paths),
        )
        counts = Counter(source['state'] for source in reading)
        source_map = {source['document_id']: source for source in reading if source['state'] in ('read', 'partial')}
        source_roles = {identifier: item['role'] for identifier, item in source_map.items()}
        passages = {p.id: p for p in project.passages}
        snapshot = [(source['path'], source['role'], source['document_version'])
                    for source in source_map.values()]
        incomplete = bool(counts['failed'] or counts['partial'])
        index = None if full_scan else PassageIndex(project.passages)
        for requirement in requirements:
            data = asdict(requirement)
            row = {'requirement': data, 'fingerprint': _fingerprint(data, snapshot),
                   'search_state': 'incomplete', 'error': '', 'suggested': None, 'evidence': [], 'review': None}
            if counts['failed']:
                row['error'] = 'Selected sources failed to load. Resolve the file issues before matching.'
            if not passages:
                if not row['error']:
                    row['error'] = 'No readable company evidence was selected.'
            elif not counts['failed'] or search_readable_on_failure:
                if incomplete:
                    row['error'] = 'Some selected files or pages could not be read. Findings from readable sources are provisional.'
                previous = previous_rows.get(requirement.id) or {}
                review = previous.get('review') or {}
                keep_exhaustive_review = (review.get('fingerprint') == row['fingerprint']
                                          and review.get('quality') in ('partial', 'none'))
                try:
                    assessment, = assess_requirements(project, [requirement], model, source_roles=source_roles,
                                                      index=index, allow_early_stop=not (incomplete or keep_exhaustive_review))
                    row.update(search_scope=assessment.search_scope, candidate_count=assessment.candidate_count,
                               evaluated_count=assessment.evaluated_count)
                    row['evidence'] = _candidates(project, assessment, source_map)
                    if not incomplete or assessment.match.quality != 'none':
                        row['suggested'] = {
                            'quality': assessment.match.quality, 'source_ids': assessment.match.source_ids,
                            'clauses': assessment.clauses,
                        }
                    if not incomplete:
                        row['search_state'] = 'complete'
                except EvidenceMatchingError as error:
                    row['error'] = ' '.join(filter(None, (row['error'], str(error))))
            _restore_review(row, previous_rows.get(requirement.id), passages, source_map)
            results.append(row)
    requirement_ids = {r.id for r in requirements}
    return {
        'schema_version': REPORT_VERSION,
        'model_mode': 'injected' if model is not None else 'configured',
        'search': {'strategy': 'complete batched scan' if full_scan else 'BM25 retrieval with full-scan fallback',
                   'selected_files': counts['read'] + counts['partial'] + counts['failed'],
                   'partial_files': counts['partial'],
                   'read_files': counts['read'], 'failed_files': counts['failed'],
                   'skipped_inventory_rows': counts['skipped'], 'passages': len(passages)},
        'source_reading': reading, 'results': results,
        'unmatched_prior_reviews': [
            {'requirement_id': identifier, 'review': row['review']}
            for identifier, row in previous_rows.items()
            if row.get('review') and identifier not in requirement_ids
        ],
    }


def verify_local_reference(report, participant_root, document_id, location, quote):
    """Verify a reviewer-supplied passage against the exact selected original version."""
    source = next((item for item in report['source_reading']
                   if item['state'] in ('read', 'partial') and item['document_id'] == document_id), None)
    if source is None:
        raise ValueError('Choose one of the evidence documents from this run.')
    path = _source_path(participant_root.resolve(), source['path'])
    limit = MAX_PPTX_FILE_BYTES if path.suffix.lower() == '.pptx' else MAX_FILE_BYTES
    try:
        with path.open('rb') as original:
            content = original.read(limit + 1)
    except OSError:
        raise ValueError('The selected evidence document is no longer readable.') from None
    if hashlib.sha256(content).hexdigest() != source['document_version']:
        raise ValueError('This evidence document changed. Run a new match before adding citations.')
    with TemporaryDirectory(prefix='mesakumo-reference-') as folder:
        project = Project('review-reference', 'Verify a citation', False, 'Task 2')
        document = intake(project, Path(folder), path.name, content, role='knowledge', unreadable_pdf_pages=[])
        passage = next((p for p in project.passages if p.location.casefold() == location.casefold()), None)
        if document.error or passage is None or not quote or quote not in passage.excerpt:
            raise ValueError('The exact excerpt was not found at that slide, page, or paragraph.')
        return {
            'passage_id': document_id + passage.id.removeprefix(document.id),
            'document_id': document_id, 'document_name': path.name,
            'document_version': passage.document_version, 'location': passage.location,
            'path': source['path'], 'role': source['role'], 'quotes': [quote],
        }


def verify_report_sources(report, participant_root):
    """Prevent downloading citations for a changed or missing original."""
    root = participant_root.resolve()
    for source in report['source_reading']:
        if source['state'] not in ('read', 'partial'):
            continue
        path = _source_path(root, source['path'])
        try:
            with path.open('rb') as file:
                digest = hashlib.file_digest(file, 'sha256').hexdigest()
        except OSError:
            digest = ''
        if digest != source['document_version']:
            raise ValueError(f'{path.name} changed or is missing. Rerun matching before exporting this report.')


def restore_search_status(report):
    """Recover incomplete coverage from reports saved before unsupported files counted as failures."""
    failures = [record for record in report.get('source_reading', [])
                if record['state'] == 'skipped' and record.get('reason') == UNSUPPORTED_FORMAT]
    if failures:
        for record in failures:
            record['state'] = 'failed'
        search = report['search']
        search['selected_files'] += len(failures)
        search['failed_files'] += len(failures)
        search['skipped_inventory_rows'] -= len(failures)
        for row in report['results']:
            row['search_state'] = 'incomplete'
            row['error'] = ' '.join(filter(None, (row['error'], 'A selected file format was unsupported.')))
            if row.get('review'):
                row['review']['fingerprint'] = ''
            if row.get('suggested') and row['suggested']['quality'] == 'none':
                row['suggested'] = None
    return report


def _report_rows(report):
    if (not isinstance(report, dict) or report.get('schema_version') != REPORT_VERSION
            or not isinstance(report.get('results'), list)):
        raise ValueError('Expected a version 1 Task 2 review package.')
    return restore_search_status(report)['results']


def reviewed_handoff(report: dict, *, participant_root: Path | None = None) -> dict:
    """Give drafting only current, complete, human-reviewed decisions."""
    if participant_root is not None:
        verify_report_sources(report, participant_root)
    reviewed, pending = [], []
    for row in _report_rows(report):
        if row.get('excluded'):
            continue
        requirement, review = row['requirement'], row.get('review')
        identifier = requirement['id']
        if row['search_state'] != 'complete' or not review or review.get('fingerprint') != row['fingerprint']:
            reason = 'Search incomplete' if row['search_state'] != 'complete' else 'Unreviewed or stale'
            pending.append({'requirement_id': identifier, 'reason': reason})
            continue
        if review.get('quality') is None:
            pending.append({'requirement_id': identifier, 'reason': 'Review the completed search'})
            continue
        quality, ids, comment = review.get('quality'), review.get('source_ids'), review.get('comment')
        evidence = {item['passage_id']: item for item in row['evidence']}
        if (row.get('search_scope') == 'retrieved candidates' and quality != 'sufficient'):
            raise ValueError('A reviewed Partial or Gap requires a complete scan of the readable passages.')
        if (quality not in QUALITY or not isinstance(ids, list) or not isinstance(comment, str)
                or any(not isinstance(item, str) or item not in evidence for item in ids)
                or len(ids) != len(set(ids)) or bool(ids) != (quality != 'none')):
            raise ValueError(f'Review for requirement {identifier} contains an invalid verdict or evidence selection.')
        reviewed.append({
            'requirement': requirement, 'quality': quality, 'evidence': [evidence[item] for item in ids],
            'suggested_gaps': [item['gap'] for item in row['suggested']['clauses'] if item['gap']],
            'reviewer_comment': comment, 'search_scope': row.get('search_scope', 'full scan'),
        })
    return {'schema_version': REPORT_VERSION, 'reviewed': reviewed, 'pending': pending,
            'source_verification': 'originals rechecked' if participant_root is not None else 'saved matching snapshot'}


class _ReplayModel:
    def __init__(self, responses):
        if not isinstance(responses, list) or any(not isinstance(item, dict) for item in responses):
            raise ValueError('Offline responses must be a JSON array of provider response objects.')
        self.responses = iter(responses)

    def complete(self, prompt):
        try:
            return json.dumps(next(self.responses), ensure_ascii=False)
        except StopIteration:
            raise ValueError('No recorded model response remains.') from None


def _read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _write_output(path, result):
    with open(path, 'x', encoding='utf-8', opener=_private_open) as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
        file.write('\n')


def _private_open(name, flags):
    return os.open(name, flags, 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    run = actions.add_parser('match')
    for option in ('checklist', 'inventory', 'participant-root', 'output'):
        run.add_argument('--' + option, required=True, type=Path)
    run.add_argument('--checklist-columns', type=Path, help='JSON mapping from default field keys to CSV column names')
    run.add_argument('--approved-export', action='store_true', help='Attest this is an approved-only export when no approval column exists')
    run.add_argument('--include-role', action='append', choices=OPTIONAL_ROLES, default=[])
    run.add_argument('--include-source', action='append', default=[], help='Exact inventory path of an additional eligible source')
    run.add_argument('--full-scan', action='store_true', help='Scan all passages in inventory order')
    run.add_argument('--previous-review', type=Path)
    provider = run.add_mutually_exclusive_group(required=True)
    provider.add_argument('--offline-responses', type=Path, help='Replay recorded JSON responses with no network calls')
    provider.add_argument('--live', action='store_true', help='Use the configured provider after approval')
    export = actions.add_parser('export')
    export.add_argument('--participant-root', type=Path, default=os.getenv('MESAKUMO_PARTICIPANT_ROOT'),
                        help='Recheck evidence versions against these originals before export')
    export.add_argument('--review', required=True, type=Path)
    export.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'match':
            columns = ChecklistColumns(**_read_json(args.checklist_columns)) if args.checklist_columns else ChecklistColumns()
            previous = _read_json(args.previous_review) if args.previous_review else None
            model = _ReplayModel(_read_json(args.offline_responses)) if args.offline_responses else None
            result = run_workflow(
                args.checklist, args.inventory, args.participant_root, columns=columns,
                approved_export=args.approved_export, include_roles=tuple(args.include_role),
                include_paths=tuple(args.include_source), model=model, previous_report=previous, full_scan=args.full_scan,
            )
            result['model_mode'] = 'offline replay' if model is not None else 'configured provider'
        else:
            result = reviewed_handoff(_read_json(args.review), participant_root=args.participant_root)
        _write_output(args.output, result)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Task 2: {error}\n')
    print(f'Wrote {args.output}')


if __name__ == '__main__':
    main()
