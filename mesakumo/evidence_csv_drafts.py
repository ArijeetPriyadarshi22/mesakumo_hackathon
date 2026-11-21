"""Draft proposal sections from an evidence CSV, outside the app, into handover files.

Accepts both evidence CSV layouts produced on Evidence Matching:
- the approved export ("Approve and Save Evidence"), with structured "Evidence citations";
- the plain review table download (8 columns), whose "file · location" references are
  matched back to the exact passages in the participant folder. A reference counts as
  found only if exactly one passage matches, preferring the one whose text appears in
  the row's Evidence column.

Rows without a verdict (Status other than Judged/Reviewed) are skipped and listed.
Output goes to <out>.md (readable) and <out>.csv (one row per section). Nothing is
written to the app database: these drafts are proposals for a person to check, and
for the table download they rest on evidence verdicts nobody has approved.

    python -m mesakumo.evidence_csv_drafts EVIDENCE.csv --out exports/ausschreibung-2-drafts
"""
import argparse
import csv
import json
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .discovery import participant_directory
from .documents import LocalProcessor
from .models import DisabledModel, ModelUnavailable, configure_team_one, configured_model
from .proposal import PROSE_KINDS, check_wording, draft_row, open_items

FITS = ('Strong match', 'Partial match', 'No match')
DONE = ('Judged', 'Reviewed')


def _norm(text):
    return ' '.join(unicodedata.normalize('NFC', text or '').split())


def _references(cell):
    """'file · location' lines; the location itself may contain ' · ' (e.g. 'Sheet X · Row 3')."""
    refs = []
    for line in (cell or '').splitlines():
        if ' · ' in line:
            name, location = line.split(' · ', 1)
            refs.append((name.strip(), location.strip()))
    return refs


class SourceFinder:
    """Finds the exact passage for a 'file · location' reference in the participant folder."""

    def __init__(self, root):
        self.root = Path(root)
        self.by_name, self.parsed = {}, {}
        for path in self.root.rglob('*'):
            if path.is_file() and '99 LLM keys' not in path.parts:  # Never read key files.
                self.by_name.setdefault(unicodedata.normalize('NFC', path.name), []).append(path)

    def _passages(self, path):
        if path not in self.parsed:
            try:
                self.parsed[path] = {p.location: p.excerpt for p in LocalProcessor().extract(path, 'D')}
            except Exception:
                self.parsed[path] = {}
        return self.parsed[path]

    def find(self, name, location, context=''):
        """Return (path, excerpt) or None. name may be a bare file name or a relative path."""
        name = unicodedata.normalize('NFC', name)
        candidates = [p for p in self.by_name.get(Path(name).name, [])
                      if unicodedata.normalize('NFC', p.as_posix()).endswith(name)]
        found = [(p, self._passages(p)[location]) for p in candidates if location in self._passages(p)]
        if len(found) > 1:  # Same file name in several folders: keep the one the matcher actually saw.
            seen = _norm(context)
            found = [f for f in found if _norm(f[1])[:300] in seen] or found
            if len({_norm(f[1]) for f in found}) > 1:
                return None
        return found[0] if found else None


def load_rows(csv_path, finder):
    """Return (drafting rows, skipped [(row number, requirement, reason)])."""
    with open(csv_path, encoding='utf-8-sig', newline='') as handle:
        records = list(csv.DictReader(handle))
    rows, skipped = [], []
    for n, record in enumerate(records, 1):
        text = (record.get('Requirement') or '').strip()
        status, fit = record.get('Status', ''), record.get('Evidence fit', '')
        if status not in DONE or fit not in FITS:
            skipped.append((n, text, f'no evidence verdict (status {status or "missing"})'))
            continue
        corpus, missing = {}, []
        if record.get('Evidence citations'):  # Approved export: structured citations.
            for c in json.loads(record['Evidence citations']):
                corpus[c['passage_id']] = c
        else:  # Table download: match references back to the original files.
            for name, location in _references(record.get('Evidence sources')):
                hit = finder.find(name, location, record.get('Evidence', ''))
                if hit is None:
                    missing.append(f'{name} · {location}')
                    continue
                path, excerpt = hit
                pid = f'{path.relative_to(finder.root).as_posix()}#{location}'
                corpus[pid] = dict(passage_id=pid, filename=path.name, location=location, excerpt=excerpt,
                                   relative_path=path.relative_to(finder.root).as_posix())
        if fit != 'No match' and not corpus:
            skipped.append((n, text, 'no cited evidence could be found in the original files'))
            continue
        if record.get('Tender citations'):
            tender = [dict(filename=c.get('filename', ''), location=c.get('location', ''),
                           original_excerpt=c.get('original_excerpt', '')) for c in json.loads(record['Tender citations'])]
        else:
            tender = []
            for name, location in _references(record.get('Tender sources')):
                hit = finder.find(name, location)
                tender.append(dict(filename=Path(name).name, location=location, original_excerpt=hit[1] if hit else ''))
        note = (record.get('Gap / next action') or '').strip()
        rows.append(dict(
            number=n, missing=missing, status=status,
            requirement=dict(id=record.get('Requirement ID') or f'row-{n}', text=text, kind=record.get('Category', ''),
                             obligation_level='', constraints=[]),
            tender_sources=tender, citation_ids=list(corpus), corpus=corpus, fit=fit,
            # A Strong match note says what is met; only Partial / No match notes are gaps to carry forward.
            notes=note if fit == 'Strong match' else '', gap='' if fit == 'Strong match' else note,
            current_action=''))
    return rows, skipped


def draft_all(rows, model, workers=4, on_done=None, retries=1):
    def one(row):
        error = ''
        for _ in range(1 + retries):  # A rejected answer is usually a format slip; try once more.
            try:
                return row, draft_row(row, row['corpus'], model), ''
            except ModelUnavailable as failure:
                return row, None, str(failure)
            except ValueError as failure:
                error = str(failure)
        return row, None, error
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(one, rows):
            results.append(result)
            if on_done:
                on_done(len(results), len(rows))
    return results


def write_outputs(out, source_csv, results, skipped, reviewed_source):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    warning = ('' if reviewed_source else
               '**Unreviewed evidence:** the evidence verdicts in the source file were made by the matcher and '
               'never approved by a person. Treat every section as a first draft to be checked.\n\n')
    drafted = [r for r in results if r[1]]
    lines = [f'# Proposal section drafts', '',
             f'Source: `{Path(source_csv).name}` · created {stamp} · {len(drafted)} drafted, '
             f'{len(results) - len(drafted)} failed, {len(skipped)} skipped.', '',
             warning + 'Each `[OFFEN: …]` marks a point a person must confirm or supply before the text can be used. '
             '“Check wording” flags phrases (e.g. *benannt*, *Hauptdozentin*, *erbracht*, *zertifiziert*) that often '
             'carry an earlier offer\'s staffing or a commitment over; it is a reading aid, not a complete check.', '']
    with open(out.with_suffix('.csv'), 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['Source row', 'Requirement', 'Category', 'Evidence fit', 'Section title', 'Section text',
                         'Open items', 'Check wording', 'Cited evidence', 'Tender sources',
                         'Unresolved evidence references', 'Draft origin', 'Error'])
        for row, draft, error in sorted(results, key=lambda r: r[0]['number']):
            cited = [row['corpus'][pid] for pid in (draft or {}).get('citation_ids', [])]
            cited_text = '\n'.join(f"{c['filename']} · {c['location']}" for c in cited)
            tender = '\n'.join(f"{s['filename']} · {s['location']}" for s in row['tender_sources'])
            writer.writerow([row['number'], row['requirement']['text'], row['requirement']['kind'], row['fit'],
                             draft['title'] if draft else '', draft['text'] if draft else '',
                             '\n'.join(open_items(draft['text'])) if draft else '',
                             ', '.join(check_wording(draft['text'])) if draft else '', cited_text, tender,
                             '\n'.join(row['missing']), draft['origin'] if draft else '', error])
            lines += [f"## {row['number']}. {draft['title'] if draft else row['requirement']['text'][:120]}", '',
                      f"*Requirement:* {row['requirement']['text']}  ",
                      f"*Category:* {row['requirement']['kind']} · *Evidence fit:* {row['fit']}  ",
                      f"*Tender:* {'; '.join(tender.splitlines()) or '—'}  ",
                      f"*Cited evidence:* {'; '.join(cited_text.splitlines()) or '—'}", '']
            if row['missing']:
                lines += [f"*Evidence references not found in the original files:* {'; '.join(row['missing'])}", '']
            if draft and check_wording(draft['text']):
                lines += [f"⚠ *Check wording:* {', '.join(check_wording(draft['text']))} — may carry an earlier "
                          'offer\'s roles or a commitment over into this tender.', '']
            lines += ([draft['text'], ''] if draft else [f'**Not drafted:** {error}', ''])
    if skipped:
        lines += ['## Not drafted', '', '| Source row | Requirement | Reason |', '|---|---|---|']
        lines += [f"| {n} | {text[:140].replace('|', '/')} | {reason} |" for n, text, reason in skipped]
    out.with_suffix('.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return out.with_suffix('.md'), out.with_suffix('.csv')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('csv', help='Evidence CSV from Evidence Matching')
    parser.add_argument('--out', required=True, help='Output path without extension, e.g. exports/ausschreibung-2-drafts')
    parser.add_argument('--categories', default=','.join(PROSE_KINDS),
                        help=f'Comma-separated categories to draft, or "all" (default: {",".join(PROSE_KINDS)})')
    parser.add_argument('--limit', type=int, help='Draft at most this many rows (for a trial run)')
    parser.add_argument('--workers', type=int, default=4, help='Parallel model calls (default 4)')
    args = parser.parse_args(argv)

    finder = SourceFinder(participant_directory())
    rows, skipped = load_rows(args.csv, finder)
    if args.categories != 'all':
        wanted = {c.strip() for c in args.categories.split(',')}
        other = [r for r in rows if r['requirement']['kind'] not in wanted]
        skipped += [(r['number'], r['requirement']['text'], f"category {r['requirement']['kind']} not selected") for r in other]
        rows = [r for r in rows if r['requirement']['kind'] in wanted]
    if args.limit is not None:
        skipped += [(r['number'], r['requirement']['text'], 'beyond --limit') for r in rows[args.limit:]]
        rows = rows[:args.limit]
    reviewed_source = all(r['status'] == 'Reviewed' for r in rows) and bool(rows)
    calls = sum(r['fit'] != 'No match' for r in rows)
    print(f'{len(rows)} rows to draft ({calls} model calls), {len(skipped)} skipped.', flush=True)

    configure_team_one()
    model = configured_model()
    if calls and isinstance(model, DisabledModel):
        sys.exit('Model access is not configured. Set REQUESTY_API_KEY or provide the Team 1 key file.')
    results = draft_all(rows, model, args.workers,
                        on_done=lambda done, total: print(f'  {done}/{total}', end='\r', flush=True))
    md, out_csv = write_outputs(args.out, args.csv, results, sorted(skipped), reviewed_source)
    failed = sum(1 for r in results if not r[1])
    print(f'\nDrafted {len(results) - failed}, failed {failed}. Wrote {md} and {out_csv}')


if __name__ == '__main__':
    main()
