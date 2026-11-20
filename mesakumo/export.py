"""Corporate rendering awaits inspection of the supplied template."""
from typing import Protocol
from .domain import Project

class ProposalExporter(Protocol):
    def render(self, project: Project) -> bytes: ...

class UnconfiguredExporter:
    def render(self, project: Project) -> bytes:
        raise NotImplementedError('Export is not implemented. No proposal file was created.')
