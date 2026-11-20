"""Hand-authored fixtures, never extracted or generated results."""
from uuid import uuid4
from .domain import (Project, Document, SourcePassage, Requirement, EvidenceMatch,
                     ProposalSection, Issue)

def sample_project():
    return Project(id=uuid4().hex, name='SYNTHETIC — Workshop proposal exercise', synthetic=True,
        scope='Fictional workshop tender and fictional reference. No Mesakumo facts. Hand-authored records; no parsing, search or generation has run.',
        documents=[Document('D1', 'Fictional tender', 'tender', 'Synthetic fixture — not parsed'),
                   Document('D2', 'Fictional reference', 'knowledge', 'Synthetic fixture — not parsed')],
        passages=[SourcePassage('P1', 'D1', 'Synthetic section 1', 'Describe a workshop approach and provide a reference with completion date.'),
                  SourcePassage('P2', 'D2', 'Synthetic reference paragraph 1', 'The fictional team facilitated a knowledge discovery workshop. Completion date is not recorded.')],
        requirements=[Requirement('R1', 'Describe a workshop approach.', ['P1']),
                      Requirement('R2', 'Provide a reference including completion date.', ['P1'])],
        evidence=[EvidenceMatch('E1', 'R2', ['P2'], 'partial', 'Workshop experience described; completion date absent.')],
        sections=[ProposalSection('S1', 'Proposed approach',
            'SYNTHETIC proposed approach: facilitate a discovery workshop to agree the prototype scope. This is an illustrative proposal, not a company commitment.', ['R1']),
                  ProposalSection('S2', 'Reference',
            'SYNTHETIC: a fictional workshop reference is available, but its completion date is unresolved.', ['R2'], ['P2'])],
        issues=[Issue('I1', 'R2', 'What is the reference completion date? Supply a confirmation basis or keep unresolved.')])
