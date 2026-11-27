"""Draft slide groups and track items that need reviewer confirmation."""
import re
import textwrap
from typing import Literal

from .automation_evidence import effective_assessments, proof_span
from .automation_requirements import CHAPTERS, Structured
from .automation_store import canonical, fingerprint

PLAN_VERSION = 'slide-groups-4'
TITLES = {
    'de': ('Ausgangslage', 'Vorgehensweise', 'Ergebnisse', 'Mitwirkung',
           'Zeitplan', 'Projektteam', 'Angebot und Preise', 'Anlagen'),
    'en': ('Context', 'Approach', 'Outcomes', 'Participation', 'Timeline',
           'Team', 'Pricing', 'Annexes'),
}


class Claim(Structured):
    text: str
    kind: Literal['proposal', 'source fact', 'open']
    requirements: list[str]
    sources: list[str]


class Chapter(Structured):
    title: str
    claims: list[Claim]


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


def supplemental_context(rows, index, assessments):
    context = {}
    for row in rows:
        query = ' '.join([row['text'], *row['conditions']])
        assessed = set(assessments.get(row['id'], {}).get('citations', []))
        for pid in index.search(query, limit=8):
            source, passage = index.entries[pid]
            if pid not in assessed and source.role in ('Previous project sources', 'Historical proposal'):
                context[row['id']] = dict(file=source.filename, role=source.role,
                                          excerpt=proof_span(row, passage.excerpt, limit=650))
                break
    return context


def quote_supported(text, excerpt):
    excerpts = [excerpt] if isinstance(excerpt, str) else excerpt
    quote = ' '.join(text.split())
    return any(quote in ' '.join(span.split()) for span in excerpts)


def fallback_claims(inputs):
    return [dict(text=part, kind='open', requirements=[rid], sources=[],
                 review_issues=['Complete the draft response for this requirement.'])
            for rid, row in inputs.items()
            for part in textwrap.wrap(row['text'], width=230, break_on_hyphens=False)]


def review_claims(result, inputs, citations, default_title):
    claims, covered = [], set()
    for claim in result['claims']:
        text = ' '.join(claim['text'].split())
        if not text:
            continue
        issues = []
        rows = list(dict.fromkeys(rid for rid in claim['requirements'] if rid in inputs))
        if not rows or set(claim['requirements']) - inputs.keys():
            issues.append('Verify which tender requirements this wording addresses.')
        sources = [pid for pid in dict.fromkeys(claim['sources']) if pid in citations and rows
                   and all(pid in inputs[rid]['citations'] for rid in rows)]
        if set(claim['sources']) - set(sources):
            issues.append('Replace missing or unrelated citations with supporting sources.')
        if claim['kind'] == 'source fact':
            if not sources or not all(quote_supported(text, citations[pid]) for pid in sources):
                issues.append('Verify that the cited material supports this factual wording.')
            if any(inputs[rid]['response'] == 'proof' and not set(sources) <= set(inputs[rid]['evidence_ids'])
                   for rid in rows):
                issues.append('Verify that proof claims cite assessed evidence.')
            if any(inputs[rid]['response'] == 'current_action' for rid in rows):
                issues.append('Confirm the action or commitment for this tender.')
        if claim['kind'] == 'proposal':
            if any(inputs[rid]['response'] != 'method' for rid in rows):
                issues.append('Confirm any required proof or tender-specific action addressed by this wording.')
            if re.search(
                r'\b(?:already|previously|confirmed|signed|verfügbar|unterzeichnet|garantiert|bereits|nachweislich)\b|[€$]',
                text, re.IGNORECASE
            ):
                issues.append('Check the factual or current commitments in this wording.')
        if claim['kind'] == 'open':
            issues.append('Verify this draft wording.')
        for part in textwrap.wrap(text, width=230, break_on_hyphens=False):
            item = dict(claim, text=part, requirements=rows, sources=sources)
            if issues:
                item['review_issues'] = issues
            claims.append(item)
        covered.update(rows)
    missing = {rid: row for rid, row in inputs.items() if rid not in covered}
    claims.extend(fallback_claims(missing))
    title = ' '.join(result['title'].split()) or default_title
    return dict(title=title[:90], claims=claims)


def draft_chapter(call, role, language, inputs, citations, context):
    prompt = f"""Draft an editable slide group for chapter {role}, in {language}.
Write useful slide-ready content for every criterion. Use the tender and assessed evidence, the
historical examples supplied as additional context, and general professional knowledge to propose
concrete methods and deliverables. Use kind proposal for future methods and source fact for sourced
facts. Cite supporting passage IDs from excerpts. Use historical examples for inspiration; they do
not establish delivery or current commitments. Retain useful draft wording as kind open when facts,
dates, staffing, prices, signatures, partner commitments or proof need reviewer confirmation. State
what could be proposed or confirmed without inventing specifics. Keep the title within 90 characters
and each bullet within 230 characters.
""" + canonical(dict(criteria=inputs, excerpts=citations, additional_context=context))
    default_title = TITLES['en' if language == 'English' else 'de'][CHAPTERS.index(role)]
    return review_claims(call(prompt, Chapter, max_tokens=3000), inputs, citations, default_title)


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
        yield dict(criterion=rid, chapters=mapping[rid], issue=reason, needs_review=bool(reason and needed))


def field_key(group):
    return fingerprint([group['id'], group['role'], group.get('context_key'), group.get('fields', [])])


def confirm_fields(group, reviewer, basis):
    reviewer, basis = reviewer.strip(), basis.strip()
    if not reviewer or not basis:
        raise ValueError('Name the reviewer and record the supporting decision or source for these fields.')
    return dict(reviewer=reviewer, basis=basis, key=field_key(group))


def review_issues(snapshot):
    current_coverage = coverage(snapshot.get('criteria', []), snapshot.get('chapters', []),
                                effective_assessments(snapshot)) if 'criteria' in snapshot else snapshot.get('coverage', [])
    messages = [item['criterion'] + ' · ' + item['issue'] for item in current_coverage
                if item.get('needs_review', item.get('blocker'))]
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
        for claim in group['claims']:
            if claim['kind'] != 'manual' and claim.get('review_issues'):
                reference = ' / '.join(claim['requirements']) or group['title']
                messages.extend(f"{reference} · {issue}" for issue in claim['review_issues'])
        field_review = group.get('field_review') or {}
        if group.get('fields') and (field_review.get('key') != field_key(group)
                                    or not field_review.get('reviewer') or not field_review.get('basis')):
            messages.append(f"Confirm the reviewer and supporting basis for fields in {group['title']}.")
        if any(set(claim['sources']) - available or set(claim['requirements']) - current for claim in group['claims']):
            messages.append(f"Refresh the stale sources or requirements in {group['title']}.")
    return messages
