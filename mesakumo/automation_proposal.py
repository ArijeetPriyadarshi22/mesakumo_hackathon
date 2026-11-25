"""Template chapter drafts with bounded content and validated claim references."""
import re
from typing import Literal

from pydantic import Field

from .automation_requirements import CHAPTERS, Structured
from .automation_store import canonical, fingerprint

PLAN_VERSION = 'slide-groups-3'
TITLES = {
    'de': ('Ausgangslage', 'Vorgehensweise', 'Ergebnisse', 'Mitwirkung',
           'Zeitplan', 'Projektteam', 'Angebot und Preise', 'Anlagen'),
    'en': ('Context', 'Approach', 'Outcomes', 'Participation', 'Timeline',
           'Team', 'Pricing', 'Annexes'),
}


class Claim(Structured):
    text: str = Field(min_length=1, max_length=230)
    kind: Literal['proposal', 'source fact', 'open']
    requirements: list[str] = Field(min_length=1)
    sources: list[str]


class Chapter(Structured):
    title: str = Field(min_length=1, max_length=90)
    claims: list[Claim] = Field(min_length=1, max_length=16)


def is_proposal(row):
    return row['scope'] == 'proposal' and row['response'] != 'none'


def chapter_units(criteria):
    for chapter in CHAPTERS:
        rows = [row for row in criteria if is_proposal(row) and row['chapter'] == chapter]
        for start in range(0, len(rows), 6):
            block = rows[start:start + 6]
            key = chapter + '-' + fingerprint([r['id'] for r in block])[:16]
            yield chapter, key, block


def plan_context(rows, sources, evidence, assessments):
    inputs, citations = {}, {}
    for row in rows:
        rid = row['id']
        assessment = assessments.get(rid, {})
        ids = list(dict.fromkeys(row['citations'][:3] + assessment.get('citations', [])[:2]))
        inputs[rid] = dict(text=row['text'], conditions=row['conditions'], response=row['response'],
                           obligation=row['obligation'], citations=ids, evidence=assessment.get('status'),
                           evidence_ids=[pid for pid in ids if pid in evidence])
        for pid in ids:
            item = sources[pid] if pid in sources else evidence[pid]
            citations[pid] = item.get('quote', item.get('excerpt', ''))[:850]
    return inputs, citations


def check_claims(result, inputs, citations):
    covered = set()
    for claim in result['claims']:
        rows = set(claim['requirements'])
        ids = set(claim['sources'])
        if rows - inputs.keys() or ids - citations.keys():
            raise ValueError('Proposal draft used an unknown requirement or source.')
        for rid in rows:
            if ids - set(inputs[rid]['citations']):
                raise ValueError('Proposal draft used a source unrelated to the criterion.')
        if claim['kind'] == 'source fact' and (not ids or not all(
            ' '.join(claim['text'].split()) in ' '.join(citations[pid].split()) for pid in ids
        )):
            raise ValueError('Factual proposal claims require a verbatim source excerpt.')
        if claim['kind'] == 'source fact' and any(
            inputs[rid]['response'] == 'proof' and not ids <= set(inputs[rid]['evidence_ids']) for rid in rows
        ):
            raise ValueError('Proof claims require evidence citations.')
        if claim['kind'] == 'source fact' and any(inputs[rid]['response'] == 'current_action' for rid in rows):
            raise ValueError('Current actions need a reviewer confirmation.')
        if claim['kind'] == 'proposal' and any(inputs[rid]['response'] != 'method' for rid in rows):
            raise ValueError('Use cited source facts or mark a gap for criteria requiring proof or a current action.')
        if claim['kind'] == 'proposal' and re.search(
            r'\b(?:already|previously|confirmed|signed|verfügbar|unterzeichnet|garantiert|bereits|nachweislich)\b|[€$]',
            claim['text'], re.IGNORECASE
        ):
            raise ValueError('A proposed method contains a factual or current commitment.')
        covered.update(rows)
    if covered != inputs.keys():
        raise ValueError('Proposal draft omitted a criterion.')
    return result


def draft_chapter(call, role, language, inputs, citations):
    prompt = f"""Draft an editable slide group for chapter {role}, in {language}.
Cover every criterion. Group mutually related work-package steps into clear bullets.
Use kind proposal only for future method; use source fact only for a short verbatim
excerpt with its source ID. If price, timeline, staffing, signatures, capacity, partner commitments
or proof are unconfirmed, use an open issue. Never treat historical tender commitments or
buyer criteria as verified facts about the bidder. Mention sources only in the sources field.
Keep bullets concise and slide-ready.
""" + canonical(dict(criteria=inputs, excerpts=citations))
    return check_claims(call(prompt, Chapter, max_tokens=3000), inputs, citations)


def coverage(criteria, groups, assessments):
    mapping = {item['id']: [] for item in criteria}
    for group in groups:
        for claim in group['claims']:
            for rid in claim['requirements']:
                if rid in mapping:
                    mapping[rid].append(dict(group=group['id'], kind=claim['kind']))
    for item in criteria:
        rid = item['id']
        needed = item['obligation'] in ('mandatory', 'scored', 'uncertain')
        reason = ''
        if 'uncertain' in (item['scope'], item['obligation'], item['response']):
            reason = 'Confirm classification.'
        elif is_proposal(item):
            resolved = item.get('resolution')
            if not mapping[rid] or any(link['kind'] == 'open' for link in mapping[rid]):
                reason = 'Supply proposal content.'
            if item['response'] == 'proof' and assessments.get(rid, {}).get('status') != 'confirmed':
                reason = 'Verify supporting evidence.'
            if item['response'] == 'current_action' and not resolved:
                reason = 'Confirm the tender-specific action.'
        elif item['response'] == 'current_action' and not item.get('resolution'):
            reason = 'Complete or confirm the administrative action.'
        yield dict(criterion=rid, chapters=mapping[rid], issue=reason, blocker=bool(reason and needed))


def blockers(snapshot):
    messages = [item['criterion'] + ' · ' + item['issue'] for item in snapshot.get('coverage', [])
                if item['blocker']]
    messages.extend(g['file'] + ' · ' + g['detail'] for g in snapshot.get('reading_gaps', [])
                    if not g.get('resolution'))
    messages.extend(g['file'] + ' · ' + g['detail'] for g in snapshot.get('extraction_gaps', []))
    conflicts = snapshot.get('conflicts', [])
    if fingerprint(conflicts) not in snapshot.get('conflict_reviews', {}):
        messages.extend(conflicts)
    messages.extend(snapshot.get('render_issues', []))
    available = snapshot.get('sources', {}).keys() | snapshot.get('evidence_sources', {}).keys()
    current = {row['id'] for row in snapshot.get('criteria', [])}
    for group in snapshot.get('chapters', []):
        if any(set(claim['sources']) - available or set(claim['requirements']) - current for claim in group['claims']):
            messages.append(f"Refresh the stale sources or requirements in {group['title']}.")
    return messages
