"""Versioned local corpus, bounded proof assessment and reviewer suggestions."""
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import Field

from .automation_requirements import PARSER, Structured
from .automation_store import canonical, fingerprint
from .documents import MAX_FILE_BYTES
from .domain import SourcePassage
from .evidence import EvidenceReading, evidence_inventory, read_evidence, resolve_evidence_path
from .evidence_matching import LexicalIndex, current_action, terms

ASSESSMENT = 'proof-2'


class Proof(Structured):
    criterion: str
    status: Literal['confirmed', 'partial', 'missing', 'search incomplete']
    citations: list[str]
    reason: str = Field(min_length=1, max_length=450)
    rejected: list[str]


class Assessments(Structured):
    assessments: list[Proof]


def manifest(root, inventory):
    versions = {}
    for relative in inventory:
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


def corpus(store, root, inventory, versions):
    result = []
    root_key = 'corpus-' + fingerprint(str(root.resolve()))
    for relative, version in versions.items():
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


def shortlist(index, item):
    ids = index.search(' '.join([item['text'], *item['conditions']]), limit=5)
    citations = {pid: index.citation(pid) for pid in ids}
    for item in citations.values():
        item['excerpt'] = item['excerpt'][:1600]
    return citations


def assess(call, group):
    payload = {row['id']: dict(criterion=row['text'], conditions=row['conditions'], candidates=candidates)
               for row, candidates in group}
    prompt = """Assess proof for each criterion using only its listed candidate excerpts.
Confirmed requires explicit evidence satisfying all conditions. Partial requires a cited
subset. Record unsupported candidate IDs in rejected. Historical bids describe proposals
and cannot establish delivery, current staffing, signatures, prices or commitments.
Use search incomplete for insufficient search. Cite only candidate IDs for that criterion.
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


def match(store, key, token, call, model_key, criteria, readings, reviews, progress):
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
        if manual and all(p in index.entries for p in manual['citations']):
            result[rid] = manual
            valid_manual.add(rid)
        elif cached := store.cached(key, 'proof', cache_key):
            result[rid] = cached
        elif not candidates:
            result[rid] = dict(criterion=rid, status='search incomplete', citations=[], rejected=[],
                               reason='Lexical search returned no candidate. Review the local evidence corpus.')
        else:
            pending.append((row, candidates, cache_key))
    for offset in range(0, len(pending), 4):
        progress('Evidence', len(result), total)
        batch = pending[offset:offset + 4]
        judgments = assess(call, [(row, candidates) for row, candidates, _ in batch])
        for row, _, cache_key in batch:
            rid = row['id']
            judgment = judgments[rid]
            store.save_unit(key, 'proof', cache_key, judgment, token)
            result[rid] = judgment
    for rid, judgment in result.items():
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
            not isinstance(p, dict) or set(p) != {'name', 'role', 'profile_path', 'location'}
            or any(not isinstance(v, str) or not v.strip() for v in p.values()) for p in profiles
        ):
            raise ValueError
        return profiles, hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, TypeError):
        raise ValueError('Reviewer profiles must be a JSON list with name, role, profile_path and location.') from None


def detected_profiles(readings):
    """Recognize the named instructor forms and CVs in the eligible inventory."""
    result = []
    for reading in readings:
        if reading.source.role != 'Team information' or not reading.passages:
            continue
        first = reading.passages[0]
        filename = reading.source.filename.casefold()
        name = ''
        role = ''
        if 'dozentenblatt' in filename:
            match = re.search(r'Persönliche Angaben[^\S\n]*\n([^,\n]+),\s*([^,\n]+)', first.excerpt)
            if match:
                name = f'{match[2].strip()} {match[1].strip()}'
                role = 'Dozentin' if 'dozentin' in filename else 'Dozent'
        elif 'lebenslauf' in filename:
            line = first.excerpt.strip().splitlines()[0]
            if re.fullmatch(r'(?:Dr\. )?[^\W\d_]+(?:[- ][^\W\d_]+){1,4}', line):
                name, role = line, 'Beratung'
        if name:
            result.append(dict(name=name, role=role, profile_path=reading.source.relative_path, location=first.location))
    return result


def suggest_reviewer(profiles, criteria, index):
    core = [row for row in criteria if row['scope'] == 'proposal'
            and row['obligation'] in ('mandatory', 'scored') and row['response'] in ('method', 'proof')]
    ranked = []
    for profile in profiles:
        candidates = [index.citation(pid) for pid, (source, _) in index.entries.items()
                      if source.role == 'Team information' and source.relative_path == profile['profile_path']]
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
            ranked.append(dict(name=profile['name'], role=profile['role'], reasons=fit,
                               score=len(fit) * 2 + sum(reason['role'] for reason in fit),
                               citations=list(dict.fromkeys([identity['passage_id'], *(r['source'] for r in fit)]))))
    ranked.sort(key=lambda p: (-p['score'], p['name']))
    tied = len(ranked) > 1 and ranked[0]['score'] == ranked[1]['score']
    match = ranked[0] if ranked and not tied else None
    return dict(match=match, candidates=ranked, tied=tied)
