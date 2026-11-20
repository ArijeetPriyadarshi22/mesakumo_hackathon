"""Typed persisted records; source, coverage and review are separate concepts."""
from dataclasses import asdict, dataclass, field
from typing import Literal
import hashlib
import json

@dataclass
class Document:
    id: str
    name: str
    role: Literal['tender', 'knowledge', 'text_module', 'template']
    status: str = 'Not processed'
    version: str = 'synthetic-v1'
    available_date: str | None = None
    original_path: str = ""
    error: str = ""

@dataclass
class SourcePassage:
    id: str
    document_id: str
    location: str
    excerpt: str
    document_version: str = "synthetic-v1"

@dataclass
class Requirement:
    id: str
    text: str
    source_ids: list[str]
    kind: str = 'deliverable'
    reviewed: bool = False
    constraints: list[str] = field(default_factory=list)
    provenance: str = "Synthetic fixture"
    original_excerpts: list[str] = field(default_factory=list)

@dataclass
class EvidenceMatch:
    id: str
    requirement_id: str
    source_ids: list[str]
    quality: Literal['partial', 'sufficient', 'none']
    limitations: str
    decision: str = 'Unreviewed'

@dataclass
class ProposalSection:
    id: str
    title: str
    text: str
    requirement_ids: list[str]
    source_ids: list[str] = field(default_factory=list)
    reviewed: bool = False

@dataclass
class Issue:
    id: str
    target_id: str
    question: str
    blocking: bool = True
    answer: str = ''
    basis: str = ''
    resolved: bool = False

@dataclass
class ReviewDecision:
    target_id: str
    revision: int
    fingerprint: str
    reviewer: str
    timestamp: str
    decision: str
    note: str = ''

@dataclass
class Project:
    id: str
    name: str
    synthetic: bool
    scope: str
    revision: int = 1
    documents: list[Document] = field(default_factory=list)
    passages: list[SourcePassage] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    evidence: list[EvidenceMatch] = field(default_factory=list)
    sections: list[ProposalSection] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    decisions: list[ReviewDecision] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        for key, record in [('documents', Document), ('passages', SourcePassage),
                            ('requirements', Requirement), ('evidence', EvidenceMatch),
                            ('sections', ProposalSection), ('issues', Issue),
                            ('decisions', ReviewDecision)]:
            value[key] = [record(**item) for item in value.get(key, [])]
        return cls(**value)

    def fingerprint(self):
        content = self.to_dict()
        content.pop('decisions')
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    @property
    def approved(self):
        return bool(self.decisions and self.decisions[-1].decision == 'Approved'
                    and self.decisions[-1].fingerprint == self.fingerprint())
