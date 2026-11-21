"""Render approved proposal text in the supplied PowerPoint template's layouts."""
import io
import posixpath
import textwrap
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
TYPES = 'http://schemas.openxmlformats.org/package/2006/content-types'
for prefix, uri in [('p', P), ('a', A), ('r', R)]:
    ET.register_namespace(prefix, uri)

COVER = 'ppt/slideLayouts/slideLayout6.xml'
CONTENT = 'ppt/slideLayouts/slideLayout7.xml'
MASTER = 'ppt/slideMasters/slideMaster5.xml'
NOTES_MASTER = 'ppt/notesMasters/notesMaster1.xml'
WORDMARK = 'ppt/media/image5.png'
MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
PPT = 'application/vnd.openxmlformats-officedocument.presentationml.'
DRAW = 'application/vnd.openxmlformats-officedocument.theme+xml'
MAX_TEMPLATE_BYTES = 20 * 1024**2
MAX_SLIDES = 1600
NS = {'p': P, 'a': A}


def _tag(ns, name):
    return f'{{{ns}}}{name}'


def _add(element, ns, tag, **attrs):
    return ET.SubElement(element, _tag(ns, tag), attrs)


def _xml(element):
    data = ET.tostring(element, encoding='utf-8', xml_declaration=True)
    ET.fromstring(data)  # Reject illegal XML characters before saving an artifact.
    return data


def _rel_name(part):
    folder, name = posixpath.split(part)
    return posixpath.join(folder, '_rels', name + '.rels')


def _relationships(rows):
    root = ET.Element(_tag(REL, 'Relationships'))
    for rid, kind, target in rows:
        _add(root, REL, 'Relationship', Id=rid, Type=R + '/' + kind, Target=target)
    return _xml(root)


def _source_relationships(archive, part):
    return [(rel.attrib['Id'], rel.attrib['Type'].rsplit('/', 1)[-1], rel.attrib['Target'])
            for rel in ET.fromstring(archive.read(_rel_name(part)))
            if rel.get('TargetMode') != 'External']


def _shape_id(shape):
    info = shape.find('.//p:cNvPr', NS)
    return info.get('id') if info is not None else None


def _layout_part(archive, name, kept, name_expected=None):
    root = ET.fromstring(archive.read(name))
    common = root.find('p:cSld', NS)
    if common is None or (name_expected and common.get('name') != name_expected):
        raise ValueError('The corporate template layout does not match the configured mapping.')
    tree = common.find('p:spTree', NS)
    shapes = {_shape_id(shape): shape for shape in tree if _shape_id(shape)}
    if not kept.issubset(shapes):
        raise ValueError('The corporate template is missing required branding shapes.')
    if any(shape.find('.//a:t', NS) is not None for key, shape in shapes.items() if key in kept):
        raise ValueError('A corporate branding shape unexpectedly contains sample text.')
    for shape in list(tree):
        if shape.tag not in (_tag(P, 'nvGrpSpPr'), _tag(P, 'grpSpPr')) and _shape_id(shape) not in kept:
            tree.remove(shape)
    for child in list(root):
        if child.tag not in {_tag(P, tag) for tag in ('cSld', 'clrMap', 'clrMapOvr', 'txStyles',
                                                     'notesStyle', 'sldLayoutIdLst')}:
            root.remove(child)
    for node in root.iter():
        for key in list(node.attrib):
            if key.endswith('}Ignorable') or (key.startswith('{' + R + '}') and key.endswith('}id')
                                              and node.tag != _tag(P, 'sldLayoutId')):
                node.attrib.pop(key)
        for child in list(node):
            if child.tag == _tag(P, 'extLst'):
                node.remove(child)
    return root


def _template_parts(archive):
    parts, types = {}, {}
    specs = [
        (COVER, {'11', '2', '3'}, 'Angebot - Titelfolie', {'slideMaster', 'image'}, 'slideLayout'),
        (CONTENT, set(), 'Title and Content', {'slideMaster'}, 'slideLayout'),
        (MASTER, set(), None, {'slideLayout', 'theme'}, 'slideMaster'),
        (NOTES_MASTER, {'4'}, None, {'theme'}, 'notesMaster'),
    ]
    for name, kept, layout, kinds, extension in specs:
        root = _layout_part(archive, name, kept, layout)
        refs = {value for node in root.iter() for key, value in node.attrib.items()
                if key.startswith('{' + R + '}')}
        relationships = []
        for rid, kind, target in _source_relationships(archive, name):
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if kind not in kinds or (kind == 'slideLayout' and resolved not in (COVER, CONTENT)):
                continue
            if kind == 'image' and rid not in refs:
                continue
            if kind in ('image', 'theme'):
                folder = 'ppt/media/' if kind == 'image' else 'ppt/theme/'
                if not resolved.startswith(folder):
                    raise ValueError('Invalid corporate template asset path.')
                parts[resolved] = archive.read(resolved)
                if kind == 'theme':
                    types[resolved] = DRAW
            relationships.append((rid, kind, target))
        available = {rid for rid, _, _ in relationships}
        if name == MASTER:
            layouts = root.find('p:sldLayoutIdLst', NS)
            for item in list(layouts):
                if item.get(_tag(R, 'id')) not in available:
                    layouts.remove(item)
            if len(layouts) != 2:
                raise ValueError('The corporate master does not contain both required layouts.')
            refs = {value for node in root.iter() for key, value in node.attrib.items()
                    if key.startswith('{' + R + '}')}
        if (not refs.issubset(available)
                or (name in (MASTER, NOTES_MASTER) and not any(kind == 'theme' for _, kind, _ in relationships))
                or (name in (COVER, CONTENT) and
                    not any(kind == 'slideMaster' and target == '../slideMasters/slideMaster5.xml'
                            for _, kind, target in relationships))):
            raise ValueError('A required corporate template relationship is missing.')
        if name == COVER:
            if not any(kind == 'image' and target == '../media/image5.png'
                       for _, kind, target in relationships):
                raise ValueError('The corporate wordmark is missing from the cover mapping.')
            tree = root.find('p:cSld/p:spTree', NS)
            tree.remove(next(shape for shape in tree if _shape_id(shape) == '3'))
            relationships = [(rid, kind, target) for rid, kind, target in relationships
                             if target != '../media/image5.png']
        parts[name], parts[_rel_name(name)] = _xml(root), _relationships(relationships)
        types[name] = PPT + extension + '+xml'

    original = ET.fromstring(archive.read('ppt/presentation.xml'))
    size = original.find('p:sldSz', NS)
    notes_size = original.find('p:notesSz', NS)
    if size is None or notes_size is None or (size.get('cx'), size.get('cy')) != ('12192000', '6858000'):
        raise ValueError('The corporate template dimensions do not match the configured layout.')
    return parts, types, size, notes_size


def _new_canvas(kind):
    root = ET.Element(_tag(P, kind))
    tree = _add(_add(root, P, 'cSld'), P, 'spTree')
    props = _add(tree, P, 'nvGrpSpPr')
    _add(props, P, 'cNvPr', id='1', name='')
    _add(props, P, 'cNvGrpSpPr')
    _add(props, P, 'nvPr')
    _add(tree, P, 'grpSpPr')
    _add(_add(root, P, 'clrMapOvr'), A, 'masterClrMapping')
    return root, tree


def _wordmark(tree, cover):
    """Use the original PNG at the cover and master footer positions."""
    picture = _add(tree, P, 'pic')
    properties = _add(picture, P, 'nvPicPr')
    _add(properties, P, 'cNvPr', id=str(len(tree)), name='mesakumo wordmark')
    _add(properties, P, 'cNvPicPr')
    _add(properties, P, 'nvPr')
    fill = _add(picture, P, 'blipFill')
    _add(fill, A, 'blip', **{_tag(R, 'embed'): 'rId3'})
    _add(_add(fill, A, 'stretch'), A, 'fillRect')
    geometry = _add(picture, P, 'spPr')
    transform = _add(geometry, A, 'xfrm')
    if cover:
        x, y, width = 8497380, 5839505, 3030000
    else:
        x, y, width = 10532918, 6574517, 1320000
    _add(transform, A, 'off', x=str(x), y=str(y))
    _add(transform, A, 'ext', cx=str(width), cy=str(round(width * 548 / 4847)))
    _add(_add(geometry, A, 'prstGeom', prst='rect'), A, 'avLst')


def _text_box(tree, name, lines, xywh=None, *, size=22, bold=False, color='tx1', placeholder=None):
    shape = _add(tree, P, 'sp')
    props = _add(shape, P, 'nvSpPr')
    _add(props, P, 'cNvPr', id=str(len(tree)), name=name)
    if placeholder:
        _add(props, P, 'cNvSpPr')
        _add(_add(props, P, 'nvPr'), P, 'ph', type=placeholder, idx='1')
    else:
        _add(props, P, 'cNvSpPr', txBox='1')
        _add(props, P, 'nvPr')
    geometry = _add(shape, P, 'spPr')
    if xywh:
        x, y, width, height = [str(round(value * 914400)) for value in xywh]
        transform = _add(geometry, A, 'xfrm')
        _add(transform, A, 'off', x=x, y=y)
        _add(transform, A, 'ext', cx=width, cy=height)
        _add(_add(geometry, A, 'prstGeom', prst='rect'), A, 'avLst')
    body = _add(shape, P, 'txBody')
    _add(body, A, 'bodyPr', wrap='none', lIns='0', rIns='0', tIns='0', bIns='0')
    _add(body, A, 'lstStyle')
    for line in lines:
        paragraph = _add(body, A, 'p')
        paragraph_style = _add(paragraph, A, 'pPr')
        _add(_add(paragraph_style, A, 'lnSpc'), A, 'spcPts', val=str((size + 5) * 100))
        run = _add(paragraph, A, 'r')
        style = _add(run, A, 'rPr', lang='de-DE', sz=str(size * 100), b='1' if bold else '0')
        _add(_add(style, A, 'solidFill'), A, 'schemeClr', val=color)
        _add(style, A, 'latin', typeface='Open Sans')
        _add(run, A, 't').text = line


def _wrap(text, columns):
    # Use conservative width estimates for wide glyphs and long unbroken strings.
    def width(char):
        return 2 if unicodedata.east_asian_width(char) in ('W', 'F') or char in 'MW@%' else 1

    lines = []
    for line in textwrap.wrap(text, columns, break_on_hyphens=False):
        while sum(map(width, line)) > columns:
            end, length = 0, 0
            for ch in line:
                length += width(ch)
                if length > columns:
                    break
                end += 1
            end = line.rfind(' ', 0, end + 1) if ' ' in line[:end] else end
            lines.append(line[:end])
            line = line[end:].lstrip()
        lines.append(line)
    return lines


def _pages(package):
    name = package.get('project_name') or package['project_id']
    if not isinstance(name, str) or not name.strip():
        raise ValueError('The approved package has no project name or ID.')
    name_lines = _wrap(name, 42)
    cover_name = name_lines if len(name_lines) <= 3 else []
    yield True, 'Angebot', cover_name, ['Angebot · Paket ' + str(package['revision']), name]
    if not cover_name:
        for start in range(0, len(name_lines), 12):
            yield False, 'Angebot', name_lines[start:start+12], ['Angebot · Paket ' + str(package['revision']), name]
    for number, section in enumerate(package['sections'], 1):
        title, text = section['title'], section['text']
        if any(not isinstance(value, str) or not value.strip() for value in (title, text)):
            raise ValueError('Every approved section needs a nonempty title and text.')
        if len(_wrap(title, 48)) == 1 and '\n' not in title:
            heading, title_lines = title, []
        else:
            heading, title_lines = f'Abschnitt {number}', [*_wrap(title, 67), '']
        lines = title_lines + [line for paragraph in text.splitlines()
                               for line in (_wrap(paragraph, 67) or [''])]
        notes = [f"Anforderung {section['requirement_id']}", title]
        for field, label in [('tender_sources', 'Ausschreibung'), ('citations', 'Unternehmensnachweis')]:
            for source in section.get(field, []):
                location = ' · '.join(str(source[key]) for key in
                                      ('filename', 'location', 'passage_id', 'document_version') if source.get(key))
                notes.append(f'{label}: {location}')
        for start in range(0, len(lines), 12):
            slide_title = heading if not start else f'Abschnitt {number} · Fortsetzung'
            yield False, slide_title, lines[start:start+12], notes


def render_proposal(package, template_bytes):
    """Build a new deck from a minimal set of safe, branded template parts."""
    if not package.get('sections'):
        raise ValueError('The approved proposal package contains no sections to render.')
    if len(template_bytes) > MAX_TEMPLATE_BYTES:
        raise ValueError('The corporate template exceeds the supported size limit.')
    try:
        with ZipFile(io.BytesIO(template_bytes)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 120 * 1024**2:
                raise ValueError('The expanded corporate template exceeds the supported size limit.')
            parts, types, size, notes_size = _template_parts(archive)
        presentation = ET.Element(_tag(P, 'presentation'))
        _add(_add(presentation, P, 'sldMasterIdLst'), P, 'sldMasterId',
             id='2147483648', **{_tag(R, 'id'): 'rId1'})
        _add(_add(presentation, P, 'notesMasterIdLst'), P, 'notesMasterId', **{_tag(R, 'id'): 'rId2'})
        slide_ids = _add(presentation, P, 'sldIdLst')
        presentation.extend([size, notes_size])
        rels = [('rId1', 'slideMaster', 'slideMasters/slideMaster5.xml'),
                ('rId2', 'notesMaster', 'notesMasters/notesMaster1.xml')]
        for n, (cover, title, lines, notes) in enumerate(_pages(package), 1):
            if n > MAX_SLIDES:
                raise ValueError('The package exceeds the supported slide limit.')
            slide, slides_tree = _new_canvas('sld')
            _wordmark(slides_tree, cover)
            if cover:
                _text_box(slides_tree, 'Offer title', [title], (0.95, 2.87, 6.6, 0.7), size=40,
                          bold=True, color='bg1')
                if lines:
                    _text_box(slides_tree, 'Tender', lines, (0.95, 4.49, 6.6, 1.4), size=25, bold=True)
            else:
                _text_box(slides_tree, 'Section title', [title], (0.37, 0.40, 12.6, 0.65),
                          size=25, color='accent1')
                _text_box(slides_tree, 'Approved text', lines, (0.37, 1.19, 12.6, 5.55))
            note, note_tree = _new_canvas('notes')
            _text_box(note_tree, 'Sources', notes, placeholder='body', size=12)
            slide_path = f'ppt/slides/slide{n}.xml'
            note_path = f'ppt/notesSlides/notesSlide{n}.xml'
            parts[slide_path], parts[note_path] = _xml(slide), _xml(note)
            types[slide_path], types[note_path] = PPT + 'slide+xml', PPT + 'notesSlide+xml'
            parts[_rel_name(slide_path)] = _relationships([
                ('rId1', 'slideLayout', '../' + (COVER if cover else CONTENT).removeprefix('ppt/')),
                ('rId2', 'notesSlide', f'../notesSlides/notesSlide{n}.xml'),
                ('rId3', 'image', '../' + WORDMARK.removeprefix('ppt/')),
            ])
            parts[_rel_name(note_path)] = _relationships([
                ('rId1', 'notesMaster', '../notesMasters/notesMaster1.xml'),
                ('rId2', 'slide', f'../slides/slide{n}.xml'),
            ])
            rid = f'rId{n+2}'
            rels.append((rid, 'slide', f'slides/slide{n}.xml'))
            _add(slide_ids, P, 'sldId', id=str(255+n), **{_tag(R, 'id'): rid})
    except (KeyError, AttributeError, TypeError, RuntimeError, ET.ParseError, BadZipFile) as exc:
        raise ValueError('The corporate template or approved proposal has an invalid PPTX mapping.') from exc

    parts['ppt/presentation.xml'] = _xml(presentation)
    parts['ppt/_rels/presentation.xml.rels'] = _relationships(rels)
    parts['_rels/.rels'] = _relationships([('rId1', 'officeDocument', 'ppt/presentation.xml')])
    types['ppt/presentation.xml'] = MIME + '.main+xml'
    manifest = ET.Element(_tag(TYPES, 'Types'))
    for extension, mime in {'rels': 'application/vnd.openxmlformats-package.relationships+xml',
                            'xml': 'application/xml', 'jpeg': 'image/jpeg',
                            'png': 'image/png', 'svg': 'image/svg+xml'}.items():
        _add(manifest, TYPES, 'Default', Extension=extension, ContentType=mime)
    for name, mime in sorted(types.items()):
        _add(manifest, TYPES, 'Override', PartName='/' + name, ContentType=mime)
    parts['[Content_Types].xml'] = _xml(manifest)
    output = io.BytesIO()
    with ZipFile(output, 'w') as archive:
        for name, content in sorted(parts.items()):
            archive.writestr(ZipInfo(name), content, compress_type=ZIP_DEFLATED)
    return output.getvalue()
