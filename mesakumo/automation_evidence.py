"""Versioned local corpus, bounded proof assessment and reviewer suggestions."""
import hashlib
import json
import re
from dataclasses import asdict
from typing import Literal

from pydantic import Field

from .automation_model import recoverable_content
from .automation_requirements import PARSER, Structured
from .automation_store import canonical, fingerprint
from .documents import MAX_FILE_BYTES
from .domain import SourcePassage
from .evidence import EvidenceReading, read_evidence, resolve_evidence_path
from .evidence_matching import LexicalIndex, current_action, terms
from .gemini import ProviderError

ASSESSMENT = 'proof-3'
CANDIDATE_LIMIT = 8


class Proof(Structured):
    criterion: str
    status: Literal['confirmed', 'partial', 'missing', 'search incomplete']
    citations: list[str]
    reason: str = Field(min_length=1, max_length=450)
    rejected: list[str]


class Assessments(Structured):
    assessments: list[Proof]


def manifest(root, inventory, *, check_active=None):
    versions = {}
    for relative in inventory:
        if check_active is not None:
            check_active()
        try:
            path = resolve_evidence_path(root, relative)
            limit = 64 * 1024**2 if path.suffix.lower() == '.pptx' else MAX_FILE_BYTES
            if path.stat().st_size > limit:
                versions[relative] = 'oversized'
                continue
            with path.open('rb') as stream:
                versions[relative] = hashlib.file_digest(stream, 'sha256').hexdigest()
        except (OSError, ValueError):
            versions[relative] = 'unavailable'
    return versions


def corpus(store, root, inventory, versions, *, check_active=None):
    result = []
    root_key = 'corpus-' + fingerprint(str(root.resolve()))
    for relative, version in versions.items():
        if check_active is not None:
            check_active()
        key = fingerprint([PARSER, relative, version, inventory[relative].role])
        row = store.cached(root_key, 'index', key)
        if row is None:
            item, = read_evidence(root, inventory, [relative])
            if item.version is not None and item.version != version:
                raise ValueError('A knowledge file changed while being read. Prepare proposals again.')
            row = dict(status=item.status, detail=item.detail, version=item.version,
                       passages=[asdict(p) for p in item.passages])
            store.save_unit(root_key, 'index', key, row, None)
        result.append(EvidenceReading(inventory[relative], row['status'], row['detail'],
                                      [SourcePassage(**p) for p in row['passages']], row['version']))
    return result


def eligible(item):
    return (item['scope'] == 'proposal' and item['response'] == 'proof'
            and not current_action(dict(text=item['text'], constraints=item['conditions'])))


def review_basis(criterion, citation_ids, sources):
    return dict(
        requirement=fingerprint([criterion['id'], ' '.join(criterion['text'].split()),
                                 sorted(' '.join(item.split()) for item in criterion['conditions']),
                                 criterion['scope'], criterion['obligation'], criterion['response'],
                                 sorted(criterion['citations'])]),
        sources={pid: sources[pid]['document_version'] for pid in citation_ids if pid in sources},
    )


def applicable_review(review, criterion, sources):
    return bool(review.get('basis') and all(pid in sources for pid in review['citations'])
                and review['basis'] == review_basis(criterion, review['citations'], sources))


def effective_assessments(snapshot):
    assessments = dict(snapshot.get('evidence', {}))
    for row in snapshot.get('criteria', []):
        rid = row['id']
        manual = snapshot.get('evidence_edits', {}).get(rid)
        if manual and assessments.get(rid) == manual and not applicable_review(
            manual, row, snapshot.get('evidence_sources', {})
        ):
            assessments.pop(rid, None)
    return assessments


def proof_span(row, text, limit=1600):
    if len(text) <= limit:
        return text
    query = set(terms(' '.join([row['text'], *row['conditions']])))
    starts = [0, *range(limit // 2, len(text) - limit, limit // 2), len(text) - limit]
    return max((text[start:start + limit] for start in starts), key=lambda span: len(query & set(terms(span))))


def shortlist(index, item):
    combined = ' '.join([item['text'], *item['conditions']])
    primary = index.search(combined, limit=CANDIDATE_LIMIT)
    focused = (pid for query in dict.fromkeys([item['text'], *item['conditions']]) if query != combined
               for pid in index.search(query, limit=3))
    ranked = list(dict.fromkeys([*primary, *focused]))
    by_file = {}
    for pid in ranked:
        source, _ = index.entries[pid]
        by_file.setdefault(source.relative_path, pid)
    ids = list(dict.fromkeys([*primary[:3], *by_file.values(), *ranked]))[:CANDIDATE_LIMIT]
    citations = {pid: index.citation(pid) for pid in ids}
    for citation in citations.values():
        citation['excerpt'] = proof_span(item, citation['excerpt'])
    return citations


def assess(call, group):
    payload = {row['id']: dict(criterion=row['text'], conditions=row['conditions'], candidates=candidates)
               for row, candidates in group}
    prompt = """Assess each criterion using its listed excerpts. Look for equivalent meaning,
including paraphrases, different terminology, and evidence spread across several passages.
Use confirmed when the cited passages together substantiate the full requirement, including any
thresholds, dates, and other explicit conditions. Use partial when the material credibly supports
one part of the requirement or shows related experience or transferable skills. Cite the passages
and explain briefly what they support and what remains unverified. A historical offer describes
what was proposed. Use evidence of actual delivery for delivery requirements and current sources
for current staffing, signatures, prices, or commitments. Use search incomplete when the excerpts
do not support a judgment and coverage is insufficient. Use missing when the reviewed material
offers no credible support and coverage is adequate. Cite only IDs listed for that criterion.
Put unrelated or contradicted candidates in rejected.
""" + canonical(payload)
    assessed = call(prompt, Assessments, max_tokens=2400)['assessments']
    expected = {row['id']: candidates for row, candidates in group}
    if len(assessed) != len(expected) or {row['criterion'] for row in assessed} != expected.keys():
        raise ValueError('Evidence assessment must return one row per requested criterion.')
    for row in assessed:
        candidates = expected[row['criterion']]
        if (set(row['citations'] + row['rejected']) - candidates.keys()
                or set(row['citations']) & set(row['rejected'])):
            raise ValueError('Evidence assessment used an invalid candidate citation.')
        positive = row['status'] in ('confirmed', 'partial')
        if positive != bool(row['citations']):
            raise ValueError('Evidence assessment requires citations for supported proof.')
    return {row['criterion']: row for row in assessed}


def match(store, key, token, call, model_key, criteria, readings, reviews, progress, *, gaps=None):
    index = LexicalIndex(readings)
    all_read = all(reading.status == 'Read' for reading in readings)
    result, pending, reviewed, candidates_by_id, valid_manual = {}, [], set(), {}, set()
    selected = [row for row in criteria if eligible(row)]
    total = len(selected)
    for row in selected:
        rid = row['id']
        candidates = shortlist(index, row)
        candidates_by_id[rid] = candidates
        reviewed.update(candidates)
        cache_key = fingerprint([ASSESSMENT, model_key, row, candidates])
        manual = reviews.get(rid)
        manual_sources = {pid: index.citation(pid) for pid in manual['citations'] if pid in index.entries} if manual else {}
        if manual and applicable_review(manual, row, manual_sources):
            result[rid] = manual
            valid_manual.add(rid)
        elif cached := store.cached(key, 'proof', cache_key):
            result[rid] = cached
        elif not candidates:
            result[rid] = dict(criterion=rid, status='search incomplete', citations=[], rejected=[],
                               reason='Lexical search returned no candidate. Review the local evidence corpus.')
        else:
            pending.append((row, candidates, cache_key))
    def assess_batch(batch):
        try:
            judgments = assess(call, [(row, candidates) for row, candidates, _ in batch])
        except (ProviderError, ValueError) as error:
            if not recoverable_content(error):
                raise
            if len(batch) > 1:
                split = len(batch) // 2
                assess_batch(batch[:split])
                assess_batch(batch[split:])
            else:
                rid = batch[0][0]['id']
                if gaps is not None:
                    gaps.append(dict(file='Evidence ' + rid, detail=str(error)))
                result[rid] = dict(criterion=rid, status='search incomplete', citations=[], rejected=[],
                                   reason='Assessment did not validate. Resume preparation or review the proof.')
            return
        for row, _, cache_key in batch:
            rid = row['id']
            judgment = dict(judgments[rid], origin='automatic')
            store.save_unit(key, 'proof', cache_key, judgment, token)
            result[rid] = judgment

    for offset in range(0, len(pending), 4):
        progress('Evidence', len(result), total)
        assess_batch(pending[offset:offset + 4])
    for rid, judgment in result.items():
        if judgment['citations'] and rid not in valid_manual:
            result[rid] = judgment = dict(judgment, origin='automatic',
                                          proof_excerpts={pid: candidates_by_id[rid][pid]['excerpt']
                                                          for pid in judgment['citations']})
        if judgment['status'] == 'missing' and rid not in valid_manual:
            candidates = candidates_by_id[rid]
            if (not all_read or len(candidates) < len(index.entries)
                    or any(len(index.entries[pid][1].excerpt) > 1600 for pid in candidates)):
                judgment.update(status='search incomplete', reason=judgment['reason'] + ' Search was bounded.')
    used = {pid for row in result.values() for pid in row['citations'] + row['rejected'] if pid in index.entries}
    return result, {pid: index.citation(pid) for pid in sorted(used | reviewed)}, index


def profile_data(path):
    if not path.is_file():
        return [], None
    try:
        data = path.read_bytes()
        profiles = json.loads(data)
        if not isinstance(profiles, list) or any(
            not isinstance(p, dict) or not {'name', 'role', 'profile_path', 'location'} <= p.keys()
            or set(p) - {'name', 'role', 'profile_path', 'location', 'locations'}
            or any(not isinstance(p[key], str) or not p[key].strip() for key in ('name', 'role', 'profile_path', 'location'))
            or ('locations' in p and (not isinstance(p['locations'], list)
                                      or any(not isinstance(v, str) or not v.strip() for v in p['locations'])))
            for p in profiles
        ):
            raise ValueError
        return profiles, hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, TypeError):
        raise ValueError('Reviewer profiles must be a JSON list with name, role, profile_path and location.') from None


COMBINED_PROFILES = 'Input Angebote_Referenzen und Beraterprofile.pptx'


def profile_source(relative_path, role):
    return role == 'Team information' or relative_path == COMBINED_PROFILES


def detected_profiles(readings):
    """Recognize explicitly identified individual records and the supplied profile slide layout."""
    result = []
    for reading in readings:
        source = reading.source
        if not profile_source(source.relative_path, source.role) or not reading.passages:
            continue
        if source.relative_path == COMBINED_PROFILES:
            for passage in reading.passages:
                if not re.fullmatch(r'Slide (?:[3-9]|1[0-2])', passage.location):
                    continue
                lines = [line.strip() for line in passage.excerpt.splitlines() if line.strip()]
                if not any('Einsatzbereiche und Kompetenzen' in line for line in lines):
                    continue
                for name, role in zip(lines[:4], lines[1:5]):
                    if (re.fullmatch(r"(?:Dr\. )?[^\W\d_]+(?:[- '][^\W\d_]+){1,5}", name)
                            and re.search(r'partner|manager|consultant', role, re.I)):
                        result.append(dict(name=name, role=role, profile_path=source.relative_path,
                                           location=passage.location))
                        break
            continue
        first = reading.passages[0]
        filename = source.filename.casefold()
        name, role = '', ''
        if 'dozentenblatt' in filename:
            match = re.search(r'Persönliche Angaben[^\S\n]*\n([^,\n]+),\s*([^,\n]+)', first.excerpt)
            if match:
                name = f'{match[2].strip()} {match[1].strip()}'
                role = 'Dozentin' if 'dozentin' in filename else 'Dozent'
        elif 'lebenslauf' in filename and first.excerpt.strip():
            line = first.excerpt.strip().splitlines()[0]
            if re.fullmatch(r'(?:Dr\. )?[^\W\d_]+(?:[- ][^\W\d_]+){1,4}', line):
                name, role = line, 'Beratung'
        if name:
            result.append(dict(name=name, role=role, profile_path=source.relative_path, location=first.location,
                               locations=[passage.location for passage in reading.passages]))
    return result


def suggest_reviewer(profiles, criteria, index):
    core = [row for row in criteria if row['scope'] == 'proposal'
            and row['obligation'] in ('mandatory', 'scored') and row['response'] in ('method', 'proof')]
    ranked = {}
    for profile in profiles:
        path = profile['profile_path']
        locations = {profile['location'], *profile.get('locations', [])}
        other_locations = {location for other in profiles if other['profile_path'] == path
                           and other['name'].casefold() != profile['name'].casefold()
                           for location in (other['location'], *other.get('locations', []))}
        candidates = [index.citation(pid) for pid, (source, passage) in index.entries.items()
                      if source.relative_path == path and profile_source(path, source.role)
                      and passage.location in locations - other_locations]
        passages = [(item, set(terms(item['excerpt']))) for item in candidates]
        identity = next((item for item, tokens in passages if item['location'] == profile['location']
                         and set(terms(profile['name'])) <= tokens), None)
        if identity is None:
            continue
        profile_terms = set().union(*(tokens for _, tokens in passages))
        generic = {'projekt', 'angebot', 'project', 'proposal', 'tender', 'ausschreibung'}
        role_terms = set(terms(profile['role'])) & profile_terms
        fit = []
        for row in core:
            query = set(terms(row['text'])) - generic
            best, tokens = max(passages, key=lambda passage: len(query & passage[1]))
            overlap = query & tokens
            if len(overlap) >= 2:
                fit.append(dict(criterion=row['id'], terms=sorted(overlap), role=bool(overlap & role_terms),
                                source=best['passage_id']))
        if fit:
            item = dict(name=profile['name'], role=profile['role'], reasons=fit,
                        score=len(fit) * 2 + sum(reason['role'] for reason in fit),
                        citations=list(dict.fromkeys([identity['passage_id'], *(r['source'] for r in fit)])))
            name_key = profile['name'].casefold()
            if name_key not in ranked or item['score'] > ranked[name_key]['score']:
                ranked[name_key] = item
    ranked = sorted(ranked.values(), key=lambda p: (-p['score'], p['name']))
    tied = len(ranked) > 1 and ranked[0]['score'] == ranked[1]['score']
    match = ranked[0] if ranked and not tied else None
    return dict(match=match, candidates=ranked, tied=tied)
