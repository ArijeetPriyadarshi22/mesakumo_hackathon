"""Transactional local snapshots with optimistic concurrency protection."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .domain import Project

class ConflictError(ValueError):
    pass

class ProjectStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, revision INTEGER, payload TEXT)')

    @contextmanager
    def connect(self):
        # sqlite3's own context manager commits or rolls back but never closes. Open
        # connections keep the file locked on Windows, so close explicitly afterwards.
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def list_projects(self):
        with self.connect() as db:
            return [(key, json.loads(payload)['name']) for key, payload in
                    db.execute('SELECT id, payload FROM projects ORDER BY id')]

    def load(self, project_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise ValueError('Project not found')
        return Project.from_dict(json.loads(row[0]))

    def create(self, project: Project):
        with self.connect() as db:
            db.execute('INSERT INTO projects VALUES (?, ?, ?)',
                       (project.id, project.revision, json.dumps(project.to_dict())))

    def save(self, project: Project, expected_revision: int):
        with self.connect() as db:
            cursor = db.execute('UPDATE projects SET revision=?, payload=? WHERE id=? AND revision=?',
                                (project.revision, json.dumps(project.to_dict()), project.id, expected_revision))
            if cursor.rowcount != 1:
                raise ConflictError('Project changed in another tab. Reload before editing.')
