"""Deterministic manual workflow rules. Navigation does not imply readiness."""
from datetime import datetime, timezone
from .domain import Project, ReviewDecision

def invalidate(project: Project):
    project.revision += 1
    # Conservative: any content edit requires all sections to be reviewed again.
    for section in project.sections:
        section.reviewed = False

def edit_section(project, section_id, text):
    section = next(s for s in project.sections if s.id == section_id)
    if not text.strip():
        raise ValueError('Section text must not be empty.')
    if section.text != text:
        section.text = text
        section.source_ids = []  # Free edits cannot retain implied claim support.
        invalidate(project)

def resolve_issue(project, issue_id, answer, basis):
    if not answer.strip() or not basis.strip():
        raise ValueError('An answer and its confirmation basis are required.')
    issue = next(i for i in project.issues if i.id == issue_id)
    issue.answer, issue.basis, issue.resolved = answer.strip(), basis.strip(), True
    invalidate(project)

def blockers(project):
    reasons = [i.question for i in project.issues if i.blocking and not i.resolved]
    if not project.requirements or not all(r.reviewed for r in project.requirements):
        reasons.append('Review the requirement checklist.')
    if not project.sections or not all(s.reviewed and s.text.strip() for s in project.sections):
        reasons.append('Review every proposal section.')
    return reasons

def review(project, reviewer):
    if not reviewer.strip():
        raise ValueError('A reviewer label is required.')
    from .requirements import check_reviewable
    check_reviewable(project)
    for requirement in project.requirements:
        requirement.reviewed = True
    for section in project.sections:
        section.reviewed = True
    project.revision += 1
    project.decisions.append(ReviewDecision(project.id, project.revision, project.fingerprint(),
        reviewer.strip(), datetime.now(timezone.utc).isoformat(), 'Reviewed',
        'Manual checklist and section review; evidence adequacy remains separate.'))

def approve(project, reviewer):
    if not reviewer.strip():
        raise ValueError('A reviewer label is required.')
    reasons = blockers(project)
    if reasons:
        raise ValueError('Approval blocked: ' + ' '.join(reasons))
    project.revision += 1
    project.decisions.append(ReviewDecision(project.id, project.revision, project.fingerprint(),
        reviewer.strip(), datetime.now(timezone.utc).isoformat(), 'Approved'))

def readiness(project):
    if project.approved:
        return 'Approved (synthetic exercise)' if project.synthetic else 'Approved'
    return 'Draft in progress' if blockers(project) else 'Ready for final review'
