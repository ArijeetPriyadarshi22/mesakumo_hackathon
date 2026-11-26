"""Template chapter drafts with bounded content and validated claim references."""
import re
from typing import Literal

from pydantic import Field

from .automation_evidence import effective_assessments, proof_span
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
        ids = list(dict.fromkeys(row['citations'][:3] + assessment.get('citations', [])))
        inputs[rid] = dict(text=row['text'], conditions=row['conditions'], response=row['response'],
                           obligation=row['obligation'], citations=ids, evidence=assessment.get('status'),
                           evidence_ids=[pid for pid in ids if pid in evidence])
        for pid in ids:
            item = sources[pid] if pid in sources else evidence[pid]
            if pid in evidence:
                excerpt = assessment.get('proof_excerpts', {}).get(pid) or proof_span(row, item['excerpt'])
                if pid not in citations:
                    citations[pid] = excerpt
                else:
                    current = citations[pid]
                    spans = [current] if isinstance(current, str) else current
                    if not any(excerpt in span for span in spans):
                        citations[pid] = [*spans, excerpt]
            else:
                citations[pid] = item['quote'][:850]
    return inputs, citations


def quote_supported(text, excerpt):
    excerpts = [excerpt] if isinstance(excerpt, str) else excerpt
    quote = ' '.join(text.split())
    return any(quote in ' '.join(span.split()) for span in excerpts)


def check_claims(result, inputs, citations):
    covered, open_rows = set(), set()
    proof_links = {rid: set() for rid in inputs}
    for claim in result['claims']:
        rows = set(claim['requirements'])
        ids = set(claim['sources'])
        if rows - inputs.keys() or ids - citations.keys():
            raise ValueError('Proposal draft used an unknown requirement or source.')
        for rid in rows:
            if ids - set(inputs[rid]['citations']):
                raise ValueError('Proposal draft used a source unrelated to the criterion.')
        if claim['kind'] == 'source fact' and (not ids or not all(
            quote_supported(claim['text'], citations[pid]) for pid in ids
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
        if claim['kind'] == 'source fact':
            for rid in rows:
                proof_links[rid].update(ids)
        if claim['kind'] == 'open':
            open_rows.update(rows)
        covered.update(rows)
    if covered != inputs.keys():
        raise ValueError('Proposal draft omitted a criterion.')
    for rid, row in inputs.items():
        if (row.get('response') == 'proof' and row.get('evidence') == 'confirmed' and rid not in open_rows
                and not set(row['evidence_ids']) <= proof_links[rid]):
            raise ValueError('Proposal omitted confirmed proof. Cite all assessed passages or record an open issue.')
    return result


def draft_chapter(call, role, language, inputs, citations):
    prompt = f"""Draft an editable slide group for chapter {role}, in {language}.
Cover every criterion. Group mutually related work-package steps into clear bullets.
Use kind proposal only for future method; use source fact only for a short verbatim
excerpt with its source ID. If price, timeline, staffing, signatures, capacity, partner commitments
or proof are unconfirmed, use an open issue. Cite each assessed proof passage in a verbatim claim
or record an open issue. Never treat historical tender commitments or
buyer criteria as verified facts about the bidder. Mention sources only in the sources field.
Keep bullets concise and slide-ready.
""" + canonical(dict(criteria=inputs, excerpts=citations))
    return check_claims(call(prompt, Chapter, max_tokens=3000), inputs, citations)


def coverage(criteria, groups, assessments):
    mapping = {item['id']: [] for item in criteria}
    linked_proofs = {item['id']: set() for item in criteria}
    for group in groups:
        for claim in group['claims']:
            for rid in claim['requirements']:
                if rid in mapping:
                    mapping[rid].append(dict(group=group['id'], kind=claim['kind']))
                    if claim['kind'] in ('source fact', 'manual'):
                        linked_proofs[rid].update(claim.get('sources', []))
    for item in criteria:
        rid = item['id']
        needed = item['obligation'] in ('mandatory', 'scored', 'uncertain')
        reason = ''
        if 'uncertain' in (item['scope'], item['obligation'], item['response']):
            reason = 'Confirm classification.'
        elif item['scope'] == 'proposal' and item['response'] == 'none' and needed:
            reason = 'Classify the required proposal response.'
        elif is_proposal(item):
            resolved = item.get('resolution')
            if not mapping[rid] or any(link['kind'] == 'open' for link in mapping[rid]):
                reason = 'Supply proposal content.'
            if item['response'] == 'proof':
                assessment = assessments.get(rid, {})
                if assessment.get('status') != 'confirmed':
                    reason = 'Verify supporting evidence.'
                elif not set(assessment.get('citations', [])) <= linked_proofs[rid]:
                    reason = 'Link the confirmed proof to the proposal response.'
            if item['response'] == 'current_action' and not resolved:
                reason = 'Confirm the tender-specific action.'
        elif item['response'] == 'current_action' and not item.get('resolution'):
            reason = 'Complete or confirm the administrative action.'
        yield dict(criterion=rid, chapters=mapping[rid], issue=reason, blocker=bool(reason and needed))


def field_key(group):
    return fingerprint([group['id'], group['role'], group.get('context_key'), group.get('fields', [])])


def confirm_fields(group, reviewer, basis):
    reviewer, basis = reviewer.strip(), basis.strip()
    if not reviewer or not basis:
        raise ValueError('Name the reviewer and record the supporting decision or source for these fields.')
    return dict(reviewer=reviewer, basis=basis, key=field_key(group))


def blockers(snapshot):
    current_coverage = coverage(snapshot.get('criteria', []), snapshot.get('chapters', []),
                                effective_assessments(snapshot)) if 'criteria' in snapshot else snapshot.get('coverage', [])
    messages = [item['criterion'] + ' · ' + item['issue'] for item in current_coverage if item['blocker']]
    if snapshot.get('language_issue'):
        messages.append(snapshot['language_issue'])
    messages.extend(g['file'] + ' · ' + g['detail'] for g in snapshot.get('reading_gaps', [])
                    if not g.get('resolution'))
    messages.extend(g['file'] + ' · ' + g['detail'] for g in snapshot.get('extraction_gaps', []))
    messages.extend(g['file'] + ' · ' + g['detail'] for g in snapshot.get('generation_gaps', []))
    conflicts = snapshot.get('conflicts', [])
    if fingerprint(conflicts) not in snapshot.get('conflict_reviews', {}):
        messages.extend(conflicts)
    messages.extend(snapshot.get('render_issues', []))
    available = snapshot.get('sources', {}).keys() | snapshot.get('evidence_sources', {}).keys()
    current = {row['id'] for row in snapshot.get('criteria', [])}
    for group in snapshot.get('chapters', []):
        field_review = group.get('field_review') or {}
        if group.get('fields') and (field_review.get('key') != field_key(group)
                                    or not field_review.get('reviewer') or not field_review.get('basis')):
            messages.append(f"Confirm the reviewer and supporting basis for fields in {group['title']}.")
        if any(set(claim['sources']) - available or set(claim['requirements']) - current for claim in group['claims']):
            messages.append(f"Refresh the stale sources or requirements in {group['title']}.")
    return messages
