"""Add calculated formula results without replacing formulas or other ZIP parts."""
import io
import math
import posixpath
from zipfile import ZipFile

from lxml import etree

SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
XML_NS = {'s': SHEET_NS}
ERRORS = {'#NULL!', '#DIV/0!', '#VALUE!', '#REF!', '#NAME?', '#NUM!', '#N/A', '#GETTING_DATA'}


def checked_archive(data):
    archive = ZipFile(io.BytesIO(data))
    entries = archive.infolist()
    if (len(entries) > 10000 or sum(p.file_size for p in entries) > 100 * 1024 * 1024
            or len({p.filename for p in entries}) != len(entries)):
        archive.close()
        raise ValueError('Workbook package exceeds the supported size or has duplicate parts.')
    return archive


def xml(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Workbook XML declarations are not supported.')
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def cache_formula_results(data, workbook, calculator, error_type):
    """Patch only formula cells' result/type; preserve every formula and ZIP member."""
    with checked_archive(data) as archive:
        rels = xml(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {r.get('Id'): r.get('Target') for r in rels if r.get('TargetMode') != 'External'}
        sheets = xml(archive.read('xl/workbook.xml')).find(f'{{{SHEET_NS}}}sheets')
        available = {ws.title for ws in workbook.worksheets}
        replacements = {}
        for sheet in sheets:
            name = sheet.get('name')
            if name not in available:
                continue
            target = targets.get(sheet.get(f'{{{REL_NS}}}id'), '')
            path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('xl', target))
            if not path.startswith('xl/worksheets/') or path not in archive.namelist():
                raise ValueError('Workbook worksheet relationship is invalid.')
            body = xml(archive.read(path))
            cells = body.findall('s:sheetData/s:row/s:c', XML_NS)
            changed = False
            for cell in cells:
                formula = cell.find(f'{{{SHEET_NS}}}f')
                if formula is None:
                    continue
                result = calculator.cell(name, cell.get('r'))
                if isinstance(result, error_type):
                    kind = 'e'
                    value = str(result) if result in ERRORS else {'#CYCLE!': '#REF!', '#LIMIT!': '#VALUE!'}.get(str(result), '#NAME?')
                elif isinstance(result, bool):
                    kind, value = 'b', '1' if result else '0'
                elif result is None:
                    # An Excel reference to an empty cell evaluates to numeric zero.
                    kind, value = 'n', '0'
                elif isinstance(result, (int, float)):
                    if not math.isfinite(result):
                        kind, value = 'e', '#NUM!'
                    else:
                        kind, value = 'n', repr(result)
                elif isinstance(result, str):
                    kind, value = 'str', result
                else:
                    kind, value = 'e', '#VALUE!'
                cell.set('t', kind)
                for old in list(cell.findall(f'{{{SHEET_NS}}}v')) + list(cell.findall(f'{{{SHEET_NS}}}is')):
                    cell.remove(old)
                cached = etree.Element(f'{{{SHEET_NS}}}v')
                cached.text = value
                cell.insert(list(cell).index(formula) + 1, cached)
                changed = True
            if changed:
                replacements[path] = etree.tostring(body, encoding='UTF-8')
        if not replacements:
            return data
        output = io.BytesIO()
        with ZipFile(output, 'w') as result:
            for part in archive.infolist():
                result.writestr(part, replacements.get(part.filename, archive.read(part.filename)))
        return output.getvalue()
