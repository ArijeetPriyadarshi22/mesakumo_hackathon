"""Fill the inspected edition of Angebotstemplate in its existing slide designs."""
import copy
import hashlib
import io
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

TEMPLATE_SHA256 = 'c6db0bee73294c0b3b6a5ce514158fd97526a98e7761f1c4ede90c3de20d26d3'
RENDERER = 'template-fill-4'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS = {'p': P, 'a': A, 'r': R}
PRESENTATION = 'ppt/presentation.xml'

# Slide number, title, content, footer, retained decorative elements, line and text budgets.
SLOTS = {
    'context': (5, 5, 10, 7, (), 4, 170),
    'approach': (6, 3, 2, 8, (), 4, 210),
    'outcomes': (7, 2, 6, 13, (5, 7), 4, 160),
    'participation': (8, 3, 6, 18, (), 3, 180),
    'timeline': (9, 2, 175, 9, (48,), 3, 150),
    'team': (12, 3, 29, 4, (), 2, 150),
    'pricing': (15, 3, 10, 28, (), 5, 145),
    'annexes': (6, 3, 2, 8, (), 4, 210),
}


def xml(data):
    from lxml import etree

    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def serialize(node):
    from lxml import etree

    return etree.tostring(node, encoding='UTF-8', xml_declaration=True, standalone=True)


def tag(namespace, local):
    return '{' + namespace + '}' + local


def child(parent, namespace, name):
    from lxml import etree

    return etree.SubElement(parent, tag(namespace, name))


def set_text(shape, lines):
    body = shape.find('p:txBody', NS)
    if body is None:
        raise ValueError('Template shape has no editable text body.')
    existing = body.findall('a:p', NS)
    sample = existing[0] if existing else None
    for paragraph in existing:
        body.remove(paragraph)
    for line in lines or ['']:
        paragraph = child(body, A, 'p')
        if sample is not None:
            ppr = sample.find('a:pPr', NS)
            if ppr is not None:
                paragraph.append(copy.deepcopy(ppr))
        run = child(paragraph, A, 'r')
        style = sample.find('a:r/a:rPr', NS) if sample is not None else None
        if style is not None:
            style = copy.deepcopy(style)
            highlight = style.find('a:highlight', NS)
            if highlight is not None:
                style.remove(highlight)
            run.append(style)
        child(run, A, 't').text = line


def shape_id(shape):
    properties = next(iter(shape.xpath('./*/p:cNvPr', namespaces=NS)), None)
    return int(properties.get('id')) if properties is not None else None


def fill_slide(data, values, keep):
    root = xml(data)
    tree = root.find('p:cSld/p:spTree', NS)

    def clean(parent):
        for node in list(parent):
            if node.tag in (tag(P, 'nvGrpSpPr'), tag(P, 'grpSpPr')):
                continue
            sid = shape_id(node)
            if sid in values:
                set_text(node, values[sid])
            elif sid in keep:
                continue
            elif node.tag == tag(P, 'grpSp'):
                clean(node)
                if not any(shape_id(shape) is not None for shape in node):
                    parent.remove(node)
            else:
                parent.remove(node)
    clean(tree)
    present = {shape_id(node) for node in root.findall('.//p:sp', NS)}
    if values.keys() - present:
        raise ValueError('Template is missing a mapped shape.')
    for element in root.xpath('.//p:custDataLst | .//p:extLst | .//a:extLst', namespaces=NS):
        if element.getparent() is not None:
            element.getparent().remove(element)
    return root


def rels_of(part):
    directory, name = posixpath.split(part)
    return posixpath.join(directory, '_rels', name + '.rels')


def resolve(part, target):
    if target.startswith('/'):
        path = target.lstrip('/')
    else:
        path = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
    if path.startswith('../'):
        raise ValueError('Relationship attempts to leave the PowerPoint package.')
    return path


def package_parts(files):
    visited = {'[Content_Types].xml'}
    pending = ['']
    while pending:
        part = pending.pop()
        if part in visited:
            continue
        if part:
            if part not in files:
                raise ValueError('Missing internal PowerPoint relationship target.')
            visited.add(part)
        rels = rels_of(part)
        if rels not in files or rels in visited:
            continue
        visited.add(rels)
        for rel in xml(files[rels]):
            if rel.get('TargetMode') != 'External':
                pending.append(resolve(part, rel.get('Target')))
    types = xml(files['[Content_Types].xml'])
    for item in list(types):
        part = item.get('PartName')
        if part and part.lstrip('/') not in visited:
            types.remove(item)
    return {name: files[name] for name in visited if name != '[Content_Types].xml'} | {
        '[Content_Types].xml': serialize(types)}


def clean_shared_templates(files, english):
    """Keep template layout inheritance and corporate assets while clearing prompt samples."""
    for name in list(files):
        if not name.startswith(('ppt/slideLayouts/slideLayout', 'ppt/slideMasters/slideMaster')) or not name.endswith('.xml'):
            continue
        root = xml(files[name])
        rel_path = rels_of(name)
        rels = xml(files[rel_path]) if rel_path in files else None
        if rels is not None:
            discard = {rel.get('Id') for rel in rels if rel.get('Type').endswith(('/oleObject', '/tags'))}
            for element in root.xpath('.//p:graphicFrame | .//p:custDataLst', namespaces=NS):
                if discard & {v for node in element.iter() for k, v in node.attrib.items()
                              if k.startswith('{' + R + '}')}:
                    element.getparent().remove(element)
            used = {v for element in root.iter() for k, v in element.attrib.items() if k.startswith('{' + R + '}')}
            for rel in list(rels):
                if rel.get('Id') in discard and rel.get('Id') not in used:
                    rels.remove(rel)
            files[rel_path] = serialize(rels)
        for shape in root.xpath('.//p:sp[p:nvSpPr/p:nvPr/p:ph]', namespaces=NS):
            for text in shape.xpath('./p:txBody//a:t', namespaces=NS):
                text.text = ''
        if name.endswith('/slideLayout6.xml'):
            for shape in root.xpath('.//p:sp', namespaces=NS):
                sid = shape_id(shape)
                if sid == 5:
                    set_text(shape, ['PROPOSAL' if english else 'ANGEBOT'])
                    for style in shape.xpath('./p:txBody//a:rPr[@sz]', namespaces=NS):
                        style.set('sz', '3200')
                elif sid in (9, 10):
                    shape.getparent().remove(shape)
        files[name] = serialize(root)


def page_values(role, page, english, fields):
    """Put short lines into the editable boxes already present on each role slide."""
    if role == 'context':
        labels = ('Context', 'Challenges', 'Objective', 'Conditions') if english else (
            'Hintergrund', 'Herausforderungen', 'Ziel', 'Bedingungen')
        values = {}
        for label, body, heading, text in zip((11, 12, 13, 14), (10, 15, 16, 17), labels, page):
            values[label], values[body] = [heading], [text]
        return values, ()
    if role == 'participation':
        slots = ((10, 13, 14), (9, 12, 15), (8, 11, 16))
        return ({slot[0]: [line] for slot, line in zip(slots, page)},
                tuple(sid for slot, _ in zip(slots, page) for sid in slot[1:]))
    if role == 'team':
        slots = [(29, 9), (45, 43)]
        values = {slot[0]: [line] for slot, line in zip(slots, page)}
        for i, field in enumerate(fields[:2]):
            values[(27, 44)[i]] = [' · '.join(field)]
        return values, tuple(slot[1] for slot, _ in zip(slots, page))
    if role == 'pricing':
        slots = [(10, 15), (7, 18), (8, 25), (9, 21), (11, 12)]
        values = {slot[0]: [line] for slot, line in zip(slots, page)}
        if fields:
            values[10] = [' · '.join(field) for field in fields] + values.get(10, [])
        return values, tuple(slot[1] for slot in slots if slot[0] in values)
    if role == 'timeline':
        values = {175: page}
        if fields:
            values[63] = ['Milestones' if english else 'Meilensteine']
            values[43] = ['Date' if english else 'Termin']
            for (left, right), field in zip(((20, 45), (21, 44), (25, 46)), fields):
                values[left], values[right] = [field[0]], [field[1]]
        return values, (48,)
    return {}, ()


def _paginate(claims, lines, length):
    import textwrap

    content = []
    for claim in claims:
        text = claim['text']
        if claim['kind'] == 'open':
            text = '⚠ ' + text
        content.extend(textwrap.wrap(text, width=length, break_on_hyphens=False) or [''])
    return [content[i:i + lines] for i in range(0, len(content), lines)]


def render(template, title, phase, snapshot):
    if hashlib.sha256(template).hexdigest() != TEMPLATE_SHA256:
        raise ValueError('This template revision has no verified shape map.')
    with ZipFile(io.BytesIO(template)) as deck:
        if sum(item.file_size for item in deck.infolist()) > 160 * 1024**2:
            raise ValueError('Template exceeds the expanded package limit.')
        files = {part: deck.read(part) for part in deck.namelist() if not part.endswith('/')}
    english = snapshot.get('language') == 'en'
    clean_shared_templates(files, english)
    presentation = xml(files[PRESENTATION])
    listing = presentation.find('p:sldIdLst', NS)
    for item in list(listing):
        listing.remove(item)
    for item in presentation.xpath('./p:extLst | ./p:custShowLst | ./p:notesMasterIdLst', namespaces=NS):
        presentation.remove(item)
    content_types = xml(files['[Content_Types].xml'])
    content_ns = 'http://schemas.openxmlformats.org/package/2006/content-types'
    relpath = rels_of(PRESENTATION)
    relations = xml(files[relpath])
    keep_types = ('slideMaster', 'presProps', 'viewProps', 'tableStyles', 'theme', 'font')
    for row in list(relations):
        if row.get('Type').rsplit('/', 1)[-1] not in keep_types:
            relations.remove(row)

    def add_slide(source, values, keep=()):
        index = len(listing) + 1
        original = f'ppt/slides/slide{source}.xml'
        dest = f'ppt/slides/proposal{index}.xml'
        filled = fill_slide(files[original], values, keep)
        files[dest] = serialize(filled)
        override = child(content_types, content_ns, 'Override')
        override.set('PartName', '/' + dest)
        override.set('ContentType', 'application/vnd.openxmlformats-officedocument.presentationml.slide+xml')
        root = xml(files[rels_of(original)])
        used = {value for node in filled.iter() for key, value in node.attrib.items()
                if key.startswith('{' + R + '}')}
        for rel in list(root):
            if not rel.get('Type').endswith('/slideLayout') and rel.get('Id') not in used:
                root.remove(rel)
        files[rels_of(dest)] = serialize(root)
        rid = f'rIdProposal{index}'
        rel = child(relations, REL, 'Relationship')
        rel.attrib.update({'Id': rid, 'Type': R + '/slide', 'Target': 'slides/' + Path(dest).name})
        entry = child(listing, P, 'sldId')
        entry.set('id', str(255 + index))
        entry.set(tag(R, 'id'), rid)

    facts = snapshot.get('facts', {})
    client = facts.get('client', {}).get('value', '')
    suffix = ' · ' + phase if phase != 'single' else ''
    scope = facts.get('scope', {}).get('value', title + suffix)
    add_slide(1, {3: [scope[:100]], 4: [client[:90]]})
    issues = []
    for group in snapshot.get('chapters', []):
        role = group['role']
        if role not in SLOTS:
            issues.append(f"Unsupported template role {role}.")
            continue
        source, heading, body, footer, keep, lines, length = SLOTS[role]
        if any(len(word) > length for claim in group['claims'] for word in claim['text'].split()):
            issues.append(f"Review wrapping in {group['title']}. A word exceeds the mapped text width.")
        for index, page in enumerate(_paginate(group['claims'], lines, length), 1):
            chapter_title = group['title'] + (f' ({index})' if index > 1 else '')
            fields = group.get('fields', []) if index == 1 else []
            mapped, decorations = page_values(role, page, english, fields)
            values = {heading: [chapter_title], footer: [title[:65]]}
            values.update(mapped or {body: page})
            if role == 'outcomes':
                values[7] = ['Outcomes' if english else 'Ergebnisse']
            add_slide(source, values, (*keep, *decorations))
    files['[Content_Types].xml'] = serialize(content_types)
    files[PRESENTATION] = serialize(presentation)
    files[relpath] = serialize(relations)
    # Drop sample content in original notes, slide variants, and document properties.
    package_root = xml(files['_rels/.rels'])
    for item in list(package_root):
        if not item.get('Type').endswith('/officeDocument'):
            package_root.remove(item)
    files['_rels/.rels'] = serialize(package_root)
    files = package_parts(files)
    output = io.BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as deck:
        for name, data in sorted(files.items()):
            # Fixed timestamps keep renders comparable across app restarts.
            deck.writestr(ZipInfo(name, (1980, 1, 1, 0, 0, 0)), data, compress_type=ZIP_DEFLATED)
    with ZipFile(output) as deck:
        if deck.testzip() is not None:
            raise ValueError('The generated PowerPoint package failed its integrity check.')
    return output.getvalue(), issues


def preview_pdf(deck):
    office = os.environ.get('MESAKUMO_SOFFICE') or shutil.which('soffice') or shutil.which('libreoffice')
    if not office and Path('/Applications/LibreOffice.app/Contents/MacOS/soffice').is_file():
        office = '/Applications/LibreOffice.app/Contents/MacOS/soffice'
    if not office:
        return None
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'proposal.pptx').write_bytes(deck)
        try:
            subprocess.run([office, '-env:UserInstallation=' + (root / 'profile').as_uri(),
                            '--headless', '--convert-to', 'pdf', '--outdir', directory,
                            str(root / 'proposal.pptx')], capture_output=True, check=True, timeout=90)
            return (root / 'proposal.pdf').read_bytes()
        except (OSError, subprocess.SubprocessError):
            return None


def open_local_deck(directory, deck):
    """Open an exact draft copy using the workstation's presentation application."""
    if sys.platform != 'darwin' and os.name != 'nt':
        raise ValueError('Download the draft to open it in your presentation application.')
    target = directory / 'deck_previews'
    target.mkdir(parents=True, exist_ok=True)
    checksum = hashlib.sha256(deck).hexdigest()
    path = target / f'Proposal-DRAFT-{checksum[:16]}.pptx'
    path.write_bytes(deck)
    try:
        if sys.platform == 'darwin':
            subprocess.run(['open', '-a', 'Microsoft PowerPoint', str(path)], check=True,
                           timeout=15, capture_output=True)
        else:
            os.startfile(path)
    except (OSError, subprocess.SubprocessError):
        raise ValueError('PowerPoint could not be opened. Download the draft to open it manually.') from None
