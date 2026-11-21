"""Loopback-only HTML interface, with CSRF and stale-form protection."""
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
import secrets
from email.parser import BytesParser
from email.policy import default
from uuid import uuid4
from ..domain import Project
from ..documents import intake
from ..models import configured_model, ModelUnavailable
from .. import requirements
from .intake import documents_view, checklist_view
from .. import workflow
from ..sample import sample_project

def h(value):
    return escape(str(value), quote=True)

def serve(store, port=8501):
    token = secrets.token_urlsafe(32)
    stages = ['Documents', 'Requirements and evidence', 'Draft and resolve', 'Review and export']

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send_html(self, body, status=200):
            data = body.encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def form(self, project, stage, action, body):
            fields = {'token': token, 'project': project.id if project else '',
                      'revision': project.revision if project else 0, 'stage': stage, 'action': action}
            return '<form method="post" action="/">' + ''.join(
                f'<input type="hidden" name="{k}" value="{h(v)}">' for k, v in fields.items()) + body + '</form>'

        def do_GET(self):
            if urlsplit(self.path).path != '/':
                self.send_html('Not found', 404)
                return
            params = parse_qs(urlsplit(self.path).query)
            try:
                stage = int(params.get('stage', ['0'])[0])
                if stage not in range(4):
                    raise ValueError('Unknown stage')
                project_id = params.get('project', [''])[0]
                project = store.load(project_id) if project_id else None
                body = '<h1>Tender → Proposal</h1><p>Local single-user workspace · Model access requires explicit configuration</p>'
                body += '<aside><h2>Saved projects</h2>'
                for key, name in store.list_projects():
                    body += f'<p><a href="/?project={h(key)}">{h(name)}</a></p>'
                body += self.form(None, 0, 'new_project', '<label>Project name<input name="name" required></label><label>Declared tender scope<input name="scope" required></label><button>Create document project</button>')
                body += self.form(None, 0, 'create', '<button>Create new synthetic sample</button>') + '</aside>'
                if project:
                    body += f'<h2>{h(project.name)}</h2><p class="notice">SYNTHETIC EXERCISE — not company evidence or a real proposal.</p>' if project.synthetic else f'<h2>{h(project.name)}</h2>'
                    body += f'<p>{h(project.scope)}</p><p>Revision {project.revision} · {h(workflow.readiness(project))}</p><nav>'
                    for index, label in enumerate(stages):
                        body += f'<a {"aria-current=page" if index == stage else ""} href="/?{h(urlencode(dict(project=project.id, stage=index)))}">{index+1}. {label}</a> '
                    body += f'</nav><h2>{stages[stage]}</h2>'
                    if stage == 0:
                        body += documents_view(project, self.form)
                    elif stage == 1:
                        body += checklist_view(project, self.form)
                    elif stage == 2:
                        body += '<p>Manual edits save immediately when submitted. Editing clears section source associations and invalidates review and approval. No generated text is shown.</p>'
                        for section in project.sections:
                            body += f'<article><h3>{h(section.title)}</h3><p>Requirements: {h(", ".join(section.requirement_ids))} · Sources: {h(", ".join(section.source_ids) or "Unverified / no source associations")}</p>'
                            body += self.form(project, stage, 'edit', f'<input type="hidden" name="target" value="{h(section.id)}"><label>Section text<textarea name="text" required>{h(section.text)}</textarea></label><button>Save section</button>') + '</article>'
                        for issue in project.issues:
                            body += f'<article><h3>{h(issue.id)} · {"Resolved" if issue.resolved else "Open blocker"}</h3><p>{h(issue.question)} Target: {h(issue.target_id)}</p>'
                            body += self.form(project, stage, 'resolve', f'<input type="hidden" name="target" value="{h(issue.id)}"><label>Human answer (keep synthetic answers within the exercise)<textarea name="answer" required>{h(issue.answer)}</textarea></label><label>Confirmation basis<textarea name="basis" required>{h(issue.basis)}</textarea></label><button>Record answer and basis</button>') + '</article>'
                        body += '<button disabled>Generate — unavailable</button>'
                    else:
                        body += '<p>Manual review records your inspection of the checklist and all sections. It does not certify evidence sufficiency or tender compliance.</p>'
                        for req in project.requirements:
                            linked = [s.title for s in project.sections if req.id in s.requirement_ids and s.text.strip()]
                            body += f'<p>{h(req.id)} → {h(", ".join(linked) or "No response passage")} · {"Reviewed" if req.reviewed else "Unreviewed"}</p>'
                        reasons = workflow.blockers(project)
                        body += '<h3>Approval blockers</h3>' + (''.join(f'<p>{h(r)}</p>' for r in reasons) or '<p>No recorded blockers. Human judgement is still required.</p>')
                        body += self.form(project, stage, 'review', '<label>Reviewer label<input name="reviewer" required></label><button>Record checklist and section review</button>')
                        body += self.form(project, stage, 'approve', '<label>Reviewer label<input name="reviewer" required></label><button>Approve this version</button>')
                        body += '<h3>Review history</h3>'
                        for decision in project.decisions:
                            body += f'<p>{h(decision.decision)} · revision {decision.revision} · {h(decision.reviewer)} · {h(decision.timestamp)}<br>Snapshot: <code>{h(decision.fingerprint[:16])}</code></p>'
                        body += '<p>Corporate-template export is not implemented. Approval creates no file. No template has been supplied.</p><button disabled>Export proposal — unavailable</button>'
                else:
                    body += '<p>Open a saved project or create a clearly labelled synthetic sample to explore the four stages.</p>'
                self.send_html('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>TenderWorkbench</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:0 20px;color:#192b3c;background:#f4f6f8}article,aside{background:white;padding:18px;margin:16px 0;border:1px solid #ccd4dd;border-radius:8px}nav{display:flex;gap:16px;flex-wrap:wrap}a{color:#075c9d}a[aria-current]{font-weight:bold}textarea{width:98%;min-height:100px}label{display:block;margin:12px 0}button,input{padding:9px}button{margin:6px 0}.notice{background:#fff0bd;padding:12px}form{margin:12px 0}code{overflow-wrap:anywhere}</style><body>' + body + '</body></html>')
            except (ValueError, KeyError) as error:
                self.send_html(f'<h1>Cannot open workspace</h1><p>{h(error)}</p><a href="/">Home</a>', 400)

        def do_POST(self):
            try:
                if self.path != '/':
                    raise ValueError('Unknown action path')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16 * 1024 * 1024:
                    raise ValueError('Invalid form size')
                raw = self.rfile.read(length)
                file_content, filename = None, ''
                content_type = self.headers.get('Content-Type', '')
                if content_type.startswith('multipart/form-data'):
                    message = BytesParser(policy=default).parsebytes(('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + raw)
                    data = {}
                    if not message.is_multipart():
                        raise ValueError('Invalid multipart upload.')
                    for part in message.iter_parts():
                        name = part.get_param('name', header='content-disposition')
                        if name == 'file':
                            filename = part.get_filename() or ''
                            file_content = part.get_payload(decode=True)
                        elif name:
                            data.setdefault(name, []).append(part.get_content())
                else:
                    data = parse_qs(raw.decode(), keep_blank_values=True)
                def field(name):
                    return data.get(name, [''])[0]
                if not secrets.compare_digest(field('token'), token):
                    raise ValueError('Invalid session token. Reload the page.')
                action = field('action')
                if action == 'new_project':
                    if not field('name').strip() or not field('scope').strip():
                        raise ValueError('Project name and declared scope required.')
                    project = Project(uuid4().hex, field('name').strip(), False, field('scope').strip())
                    store.create(project)
                elif action == 'create':
                    project = sample_project()
                    store.create(project)
                else:
                    project = store.load(field('project'))
                    expected = int(field('revision'))
                    if expected != project.revision:
                        raise ValueError('Stale form. Reload the project before editing.')
                    if action == 'upload':
                        if project.synthetic:
                            raise ValueError('Upload files to a document project, not a synthetic fixture.')
                        intake(project, store.path.parent, filename, file_content, field('role'))
                    elif action == 'extract':
                        if project.synthetic:
                            raise ValueError('Synthetic fixtures do not use live extraction.')
                        requirements.extract_candidates(project, configured_model())
                    elif action == 'save_requirement':
                        requirements.save_requirement(project, field('target'), field('text'), field('kind'),
                            data.get('sources', []), field('constraints').splitlines(), field('reviewer'), field('note'))
                    elif action == 'delete_requirement':
                        requirements.delete_requirement(project, field('target'), field('reviewer'))
                    elif action == 'review_checklist':
                        requirements.review_checklist(project, field('reviewer'))
                    elif action == 'edit':
                        workflow.edit_section(project, field('target'), field('text'))
                    elif action == 'resolve':
                        workflow.resolve_issue(project, field('target'), field('answer'), field('basis'))
                    elif action == 'review':
                        workflow.review(project, field('reviewer'))
                    elif action == 'approve':
                        workflow.approve(project, field('reviewer'))
                    else:
                        raise ValueError('Unknown action')
                    store.save(project, expected)
                self.send_response(303)
                self.send_header('Location', '/?' + urlencode(dict(project=project.id, stage=field('stage') or '0')))
                self.end_headers()
            except (ValueError, StopIteration, UnicodeError, ModelUnavailable) as error:
                self.send_html(f'<h1>Change not saved</h1><p>{h(error) or "Unknown record"}</p><p>Use Back, then reload to see the current saved version.</p><a href="/">Home</a>', 400)

    server = HTTPServer(('127.0.0.1', port), Handler)
    print(f'Workspace: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
