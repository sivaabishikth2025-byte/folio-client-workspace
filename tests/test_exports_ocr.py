import io
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from PIL import Image

import backend.app as module
from backend import ocr, cloud
from backend.documents import apply_edits, inspect_document, spreadsheet_download
from backend.storage import Store
from backend.xlsx_export import checked_archive, xml

SEED = Path(__file__).resolve().parents[1] / 'seed/clients/Northwind Trading'
WORKBOOK = SEED / 'Finance/Management accounts Aug 2026.xlsx'
SCAN = SEED / 'Inbox/Scan 14 Oct 2026.pdf'


def workbook_bytes(wb):
    out=io.BytesIO();wb.save(out);return out.getvalue()


@pytest.fixture
def api(tmp_path,monkeypatch):
    for key in ('VERCEL','WORKSPACE_PASSWORD','COGNITO_CLIENT_ID','FOLIO_S3_BUCKET','FOLIO_OCR_ENABLED'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(module,'store',Store(tmp_path))
    module.parsed_small.cache_clear()
    return TestClient(module.app)


def management_url(api):
    docs=api.get('/api/clients/northwind').json()['documents']
    doc=next(d for d in docs if 'Management accounts' in d['name'])
    return f'/api/clients/northwind/documents/{doc["id"]}',doc


def test_download_caches_existing_formula_values_without_changing_original(api):
    url,doc=management_url(api)
    response=api.get(url+'/download');assert response.status_code==200
    values=load_workbook(io.BytesIO(response.content),data_only=True)
    assert values['Income statement']['B8'].value==pytest.approx(2105970/6018750)
    assert values['Debtors']['C9'].value==pytest.approx(717505.75)
    formulas=load_workbook(io.BytesIO(response.content))
    before=load_workbook(WORKBOOK)
    for ws in before:
        for row in ws:
            for cell in row:
                other=formulas[ws.title][cell.coordinate]
                assert other.value==cell.value
                assert other.number_format==cell.number_format
                assert other._style==cell._style
    with ZipFile(WORKBOOK) as original,ZipFile(io.BytesIO(response.content)) as saved:
        assert original.namelist()==saved.namelist()
        for name in original.namelist():
            if not name.startswith('xl/worksheets/'):
                assert original.read(name)==saved.read(name)
    assert api.get(url+'/download?original=true').content==WORKBOOK.read_bytes()
    assert api.get(url).json()['revision']==doc['revision']


def test_accepted_edit_has_updated_caches_in_saved_bytes_and_history(api):
    url,doc=management_url(api)
    proposal=api.post('/api/clients/northwind/proposals',json={'document_id':doc['id'],'revision':doc['revision'],'changes':[{'sheet':'Income statement','cell':'B3','value':5000000}]}).json()
    assert api.post('/api/clients/northwind/proposals/'+proposal['id']+'/accept').status_code==200
    saved=api.get(url+'/download?original=true').content
    values=load_workbook(io.BytesIO(saved),data_only=True)
    assert values['Income statement']['B5'].value==6206450
    assert values['Income statement']['B8'].value==pytest.approx(2293670/6206450)
    assert values['Debtors']['C9'].value==717505.75
    assert load_workbook(io.BytesIO(saved))['Income statement']['B5'].value=='=SUM(B3:B4)'
    assert api.get(url+'/download?revision=1&original=true').content==WORKBOOK.read_bytes()


def test_formula_cache_types_errors_and_literal_error_strings():
    wb=Workbook();ws=wb.active
    cases={'A1':('=2+3',5,'n'),'A2':('=1=1',True,'b'),'A3':('="a & b"','a & b','s'),
           'A4':('="#REF!"','#REF!','s'),'A5':('=1/0','#DIV/0!','e'),
           'A6':('=NoSuchFunction(1)','#NAME?','e'),'A7':('=B7',0,'n'),
           'A8':('=A8','#REF!','e'),'A9':('=B9','#REF!','e')}
    ws['B9']='#REF!'
    for ref,(formula,_,_) in cases.items():ws[ref]=formula
    data=spreadsheet_download(workbook_bytes(wb))
    values=load_workbook(io.BytesIO(data),data_only=True)
    formulas=load_workbook(io.BytesIO(data))
    for ref,(formula,value,kind) in cases.items():
        assert values.active[ref].value==value,(ref,values.active[ref].value)
        assert values.active[ref].data_type==kind
        assert formulas.active[ref].value==formula


def test_formula_caches_follow_workbook_relationships():
    wb=Workbook();wb.active.title='First';wb.active['A1']='=2+3'
    ws=wb.create_sheet('Second');ws['A1']="='First'!A1*2"
    raw=workbook_bytes(wb);output=io.BytesIO()
    with ZipFile(io.BytesIO(raw)) as before,ZipFile(output,'w',ZIP_DEFLATED) as after:
        for part in before.infolist():
            name=part.filename;data=before.read(name)
            if name=='xl/_rels/workbook.xml.rels':data=data.replace(b'sheet2.xml',b'custom.xml')
            if name=='xl/worksheets/sheet2.xml':name='xl/worksheets/custom.xml'
            after.writestr(name,data)
    saved=spreadsheet_download(output.getvalue())
    assert load_workbook(io.BytesIO(saved),data_only=True)['Second']['A1'].value==10
    with ZipFile(io.BytesIO(saved)) as result:
        assert 'xl/worksheets/custom.xml' in result.namelist()
        assert b"'First'!A1*2" in result.read('xl/worksheets/custom.xml')


def test_export_rejects_entity_declarations_and_duplicate_parts():
    with pytest.raises(ValueError,match='declarations'):
        xml(b'<!DOCTYPE root [<!ENTITY external SYSTEM "file:///private">]><root>&external;</root>')
    out=io.BytesIO()
    with ZipFile(out,'w') as archive:
        archive.writestr('xl/a','a')
        with pytest.warns(UserWarning):archive.writestr('xl/a','b')
    with pytest.raises(ValueError,match='duplicate'):checked_archive(out.getvalue())


def test_s3_workbook_download_does_not_skip_recalculation(api,monkeypatch):
    url,doc=management_url(api)
    module.store.mutate('northwind',lambda s:next(d for d in s['documents'] if d['id']==doc['id']).update(blob='s3:files/test-object'))
    monkeypatch.setattr(cloud,'read',lambda key:WORKBOOK.read_bytes())
    monkeypatch.setattr(cloud,'download_url',lambda key,name:'https://storage.invalid/original')
    response=api.get(url+'/download');assert response.status_code==200
    assert load_workbook(io.BytesIO(response.content),data_only=True)['Debtors']['C9'].value==717505.75
    assert api.get(url+'/download?original=true',follow_redirects=False).status_code==303


def test_large_prepared_export_uses_private_expiring_s3_prefix(api,monkeypatch):
    url,_=management_url(api);saved=[]
    monkeypatch.setenv('VERCEL','1');monkeypatch.setenv('FOLIO_S3_BUCKET','test-bucket')
    monkeypatch.setattr(module.security,'authenticated',lambda *args:True)
    monkeypatch.setattr(module,'spreadsheet_download',lambda raw:b'x'*(4*1024*1024+1))
    monkeypatch.setattr(cloud,'client',lambda service:SimpleNamespace(put_object=lambda **kw:saved.append(kw)))
    monkeypatch.setattr(cloud,'download_url',lambda key,name:'https://storage.invalid/prepared')
    response=api.get(url+'/download',follow_redirects=False)
    assert response.status_code==303
    assert saved[0]['Key'].startswith('incoming/northwind/exports/')
    assert saved[0]['Bucket']=='test-bucket'
    assert len(saved[0]['Body'])>4*1024*1024


def test_ocr_uses_only_confident_bounded_line_regions(monkeypatch):
    def detect(**kwargs):
        assert kwargs=={'Document':{'Bytes':b'image'}}
        return {'Blocks':[
            {'BlockType':'PAGE','Text':'Whole source'},
            {'BlockType':'WORD','Text':'Word'},
            {'BlockType':'LINE','Text':'Short delivered: 6 cases','Confidence':99.2,'Geometry':{'BoundingBox':{'Left':.1,'Top':.35,'Width':.7,'Height':.03}}},
            {'BlockType':'LINE','Text':'Uncertain','Confidence':10,'Geometry':{'BoundingBox':{'Left':.1,'Top':.2,'Width':.7,'Height':.03}}},
            {'BlockType':'LINE','Text':'Invalid','Confidence':99,'Geometry':{'BoundingBox':{'Left':-1,'Top':.2,'Width':.7,'Height':.03}}}]}
    monkeypatch.setattr(cloud,'client',lambda service:SimpleNamespace(detect_document_text=detect))
    lines=ocr.textract_lines(b'image')
    assert lines==[{'text':'Short delivered: 6 cases','confidence':99.2,'rect':[.098,.348,.802,.382]}]


def test_scan_source_has_line_coordinates_and_visual_crosscheck(api,monkeypatch):
    monkeypatch.setattr(ocr,'detect_lines',lambda data:[{'text':'Short delivered: 6 x olive oil 5L (backorder)','rect':[.09,.35,.85,.39],'confidence':99}])
    state,_=module.store.read('northwind');context,sources,images=module.source_map(state)
    line=next(v for v in sources.values() if v.get('ocr'))
    assert line['granularity']=='line' and line['locator']=='page1:ocr0'
    assert line['rect']==[.09,.35,.85,.39]
    assert line['revision']==1 and line['hash']
    assert len(images)==1 and 'exact supplied OCR line IDs' in images[0]['label']
    assert not any(v['locator']=='page1:image' and 'Scan 14' in v['name'] for v in sources.values())


def test_transient_ocr_failure_is_not_cached(api,monkeypatch):
    calls=[]
    def detect(data):
        calls.append(1)
        if len(calls)==1:raise ocr.OCRUnavailable(retryable=True)
        return [{'text':'Recovered line','rect':[.1,.3,.7,.34],'confidence':98}]
    monkeypatch.setattr(ocr,'detect_lines',detect)
    raw=SCAN.read_bytes()
    first=module.parsed(raw,SCAN.name);assert first['blocks'][0]['granularity']=='page'
    second=module.parsed(raw,SCAN.name);assert second['blocks'][0]['granularity']=='line'
    assert module.parsed(raw,SCAN.name)==second
    assert len(calls)==2


def test_blank_scan_remains_whole_page(api,monkeypatch):
    monkeypatch.setattr(ocr,'detect_lines',lambda data:[])
    result=inspect_document(SCAN.read_bytes(),SCAN.name)
    assert result['blocks'][0]['locator']=='page1:image'
    assert result['blocks'][0]['granularity']=='page'
    assert result['blocks'][0]['rect']==[0,0,1,1]


def test_provider_failure_falls_back_without_exposing_service_error(monkeypatch):
    monkeypatch.setattr(ocr,'cache_mode',lambda:(True,None))
    def fail(data):raise ClientError({'Error':{'Code':'ThrottlingException','Message':'Private provider details'}},'DetectDocumentText')
    monkeypatch.setattr(ocr,'textract_lines',fail)
    with pytest.raises(ocr.OCRUnavailable) as exc:ocr.detect_lines(b'image')
    assert exc.value.retryable and 'Private' not in str(exc.value)


def test_tesseract_words_group_into_lines(monkeypatch):
    import pytesseract
    out=io.BytesIO();Image.new('RGB',(100,200)).save(out,format='PNG')
    words={'text':['Short','delivered','uncertain'],'conf':[99,98,10],
           'block_num':[1,1,1],'par_num':[1,1,1],'line_num':[1,1,2],
           'left':[10,40,10],'top':[50,50,80],'width':[25,45,40],'height':[10,10,10]}
    monkeypatch.setattr(pytesseract,'image_to_data',lambda *a,**k:words)
    assert ocr.tesseract_lines(out.getvalue())==[{'text':'Short delivered','confidence':98,'rect':[.098,.248,.852,.302]}]
