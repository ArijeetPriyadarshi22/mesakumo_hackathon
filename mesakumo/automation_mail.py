"""Durable link-only notifications with a configurable local SMTP transport."""
import json
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from uuid import uuid4

from .automation_store import fingerprint


def review_assignment(snapshot):
    suggested = snapshot.get('reviewer', {}).get('match')
    override = snapshot.get('reviewer_override')
    matched = override or (suggested and suggested['name'])
    if override:
        reason = 'Reviewer selected in the project workspace.'
    elif suggested:
        reason = f"Profile matches {len(suggested['reasons'])} core criteria."
    else:
        reason = 'No single credible reviewer match. Send to local triage.'
    return dict(reviewer=matched or 'Local triage', triage=not matched, reason=reason)


def notice_for(key, project, snapshot, port):
    previous = project['snapshot'].get('review_episode')
    episode = previous if previous and not project['approval'] else uuid4().hex
    snapshot['review_episode'] = episode
    return dict(episode=episode, project=project['snapshot'].get('project_name') or snapshot['project_name'],
                **review_assignment(snapshot),
                url=f'http://localhost:{port}/?project={key}&tab=review')


def smtp_transport(payload, *, recipient, sender, message_id):
    message = EmailMessage()
    message['Subject'] = 'Tender proposal ready for review'
    message['From'] = sender
    message['To'] = recipient
    message['Message-ID'] = f'<{message_id}@tenderworkbench.local>'
    message.set_content(f"{payload['project']}\nReviewer: {payload['reviewer']}\n"
                        f"{payload['reason']}\n\n{payload['url']}\n")
    host = os.environ.get('MESAKUMO_SMTP_HOST')
    auth = os.environ.get('MESAKUMO_SMTP_USER')
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, int(os.environ.get('MESAKUMO_SMTP_PORT') or 465),
                          context=context, timeout=20) as smtp:
        if auth:
            smtp.login(auth, os.environ.get('MESAKUMO_SMTP_PASSWORD') or '')
        smtp.send_message(message)


def dispatch(store, transport=smtp_transport, *, recipient=None, triage=None, sender=None):
    recipient = recipient or os.environ.get('MESAKUMO_REVIEW_RECIPIENT') or store.setting('review_recipient')
    triage = triage or os.environ.get('MESAKUMO_TRIAGE_RECIPIENT') or store.setting('triage_recipient')
    sender = sender or os.environ.get('MESAKUMO_SMTP_SENDER')
    if not sender or (transport is smtp_transport and not os.environ.get('MESAKUMO_SMTP_HOST')):
        return False
    now, reservation = time.time(), uuid4().hex
    with store.write() as db:
        # A process crash after SMTP delivery but before the SQLite commit is ambiguous.
        db.execute("""
            UPDATE review_outbox SET state='uncertain', diagnostic='Interrupted during delivery. Verify receipt.'
            WHERE state='sending' AND due<=?
        """, (now,))
        selected = db.execute("""
            SELECT o.* FROM review_outbox o JOIN automation_projects p ON p.id=o.project
            WHERE o.state IN ('queued','retry') AND o.due<=? AND p.checksum IS NOT NULL
                AND json_extract(p.snapshot, '$.review_episode')=o.episode
                AND CASE WHEN json_extract(o.payload, '$.triage') THEN ? ELSE ? END
            ORDER BY o.due,o.project LIMIT 1
        """, (now, bool(triage), bool(recipient))).fetchone()
        if selected is None:
            return False
        payload = json.loads(selected['payload'])
        address = triage if payload['triage'] else recipient
        key = (selected['project'], selected['episode'])
        db.execute("""
            UPDATE review_outbox SET state='sending',token=?,attempts=attempts+1,due=?
            WHERE project=? AND episode=?
        """, (reservation, now + 120, *key))
    state, diagnostic = 'sent', None
    attempt = selected['attempts'] + 1
    try:
        transport(payload, recipient=address, sender=sender, message_id=fingerprint(key))
    except (smtplib.SMTPConnectError, smtplib.SMTPHeloError, smtplib.SMTPSenderRefused,
            smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError, ConnectionRefusedError) as error:
        code = getattr(error, 'smtp_code', 0)
        if isinstance(error, smtplib.SMTPRecipientsRefused):
            code = next(iter(error.recipients.values()), (0,))[0]
        state = 'retry' if attempt < 3 and (400 <= code < 500 or isinstance(error, ConnectionRefusedError)) else 'failed'
        diagnostic = 'SMTP rejected the message.'
    except (smtplib.SMTPAuthenticationError, ValueError):
        state, diagnostic = 'failed', 'Check SMTP authentication and address settings.'
    except Exception:
        state, diagnostic = 'uncertain', 'Delivery was interrupted. Verify receipt before retrying.'
    with store.write() as db:
        # A reassignment during an attempt only changes a definitively rejected delivery.
        if state == 'retry':
            current = store.project(key[0], db=db)['snapshot']
            if current.get('review_episode') == key[1] and 'project_name' in current:
                payload.update(review_assignment(current))
                db.execute('UPDATE review_outbox SET payload=? WHERE project=? AND episode=? AND token=?',
                           (json.dumps(payload), *key, reservation))
        db.execute("""
            UPDATE review_outbox SET state=?, diagnostic=?,token=NULL,due=?
            WHERE project=? AND episode=? AND state='sending' AND token=?
        """, (state, diagnostic, time.time() + (30 * 2 ** (attempt - 1) if state == 'retry' else 0), *key, reservation))
    return True


def statuses(store, project):
    with store.connect() as db:
        return [dict(row) for row in db.execute("""
            SELECT state,attempts,diagnostic FROM review_outbox WHERE project=? ORDER BY rowid DESC
        """, (project,))]
