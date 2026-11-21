"""Inventory allowlist and local reading for the evidence picker."""
import csv
import hashlib
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .discovery import ROOT
from .documents import LocalProcessor, MAX_FILE_BYTES
from .domain import SourcePassage

EVIDENCE_ROLES = ('Previous project sources', 'Team information', 'Historical proposal')
SUPPORTED = {'.txt', '.pdf', '.docx', '.xlsx', '.aidf', '.pptx'}


@dataclass(frozen=True)
class EvidenceSource:
    relative_path: str
    filename: str
    role: str


@dataclass
class EvidenceReading:
    source: EvidenceSource
    status: str
    detail: str = ''
    passages: list[SourcePassage] = field(default_factory=list)
    version: str | None = None


def evidence_inventory(inventory_path=None):
    """Only explicitly classified evidence files are eligible."""
    result = {}
    with (inventory_path or ROOT / 'Mesakumo_Document_Inventory.csv').open(
        encoding='utf-8-sig', newline=''
    ) as inventory:
        for row in csv.DictReader(inventory):
            role = row['Primary role']
            if role not in EVIDENCE_ROLES or row['Location type'] != 'File':
                continue
            relative = unicodedata.normalize('NFC', row['Path relative to participant input folder'])
            parts = relative.split('/')
            if (any(part in ('', '.', '..') or '\\' in part for part in parts)
                    or parts[0].casefold().startswith(('ausschreibung', '99 llm keys'))
                    or 'ausschreibungsunterlagen' in {p.casefold() for p in parts}):
                continue
            filename = unicodedata.normalize('NFC', row['Filename'])
            result[relative] = EvidenceSource(relative, filename, role)
    return dict(sorted(result.items(), key=lambda item: (EVIDENCE_ROLES.index(item[1].role),
                                                          item[1].filename.casefold(), item[0])))


def resolve_evidence_path(directory, relative):
    root = directory.resolve()
    path = root
    for part in PurePosixPath(relative).parts:
        if part in ('/', '..'):
            raise ValueError('Evidence path must stay inside the participant directory.')
        matches = [child for child in path.iterdir()
                   if unicodedata.normalize('NFC', child.name) == part]
        if not matches:
            raise FileNotFoundError(relative)
        if len(matches) != 1:
            raise ValueError('More than one file matches this Unicode inventory path.')
        path = matches[0]
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Symlinks are excluded from evidence sources.')
    if not path.is_file():
        raise FileNotFoundError(relative)
    return path


def read_evidence(directory, inventory, selected):
    """Build a corpus from selected, eligible inventory paths only."""
    readings = []
    for relative in dict.fromkeys(selected):
        source = inventory.get(relative)
        if source is None:
            raise ValueError('Selection includes a file outside the evidence inventory.')
        version = None
        try:
            path = resolve_evidence_path(directory, relative)
            suffix = path.suffix.lower()
            limit = 64 * 1024 * 1024 if suffix == '.pptx' else MAX_FILE_BYTES
            if path.stat().st_size > limit:
                raise ValueError(f'File exceeds the {limit // 1024**2} MB local reading limit.')
            document_id = 'E-' + hashlib.sha256(relative.encode()).hexdigest()[:24]
            with tempfile.TemporaryDirectory() as folder:
                snapshot = Path(folder) / ('source' + suffix)
                shutil.copyfile(path, snapshot)
                if snapshot.stat().st_size > limit:
                    raise ValueError('File grew past the local reading limit during capture.')
                with snapshot.open('rb') as file:
                    version = hashlib.file_digest(file, 'sha256').hexdigest()
                if suffix not in SUPPORTED:
                    readings.append(EvidenceReading(
                        source, 'Unsupported', f'No reader for {suffix or "this file type"}.', version=version))
                    continue
                passages = LocalProcessor().extract(snapshot, document_id)
            readings.append(EvidenceReading(source, 'Read', passages=passages, version=version))
        except FileNotFoundError:
            readings.append(EvidenceReading(source, 'Missing', 'Inventory file was not found.'))
        except ValueError as exc:
            readings.append(EvidenceReading(source, 'Unreadable', str(exc), version=version))
        except Exception as exc:
            readings.append(EvidenceReading(source, 'Unreadable', f'Local reader failed ({type(exc).__name__}).', version=version))
    return readings
