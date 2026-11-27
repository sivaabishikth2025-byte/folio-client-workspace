import os, asyncio
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
for key in ('VERCEL','BLOB_READ_WRITE_TOKEN','COGNITO_CLIENT_ID','WORKSPACE_PASSWORD','FOLIO_S3_BUCKET','FOLIO_OCR_ENABLED'):
    os.environ[key]=''
os.environ['DATA_DIR']=str(Path.cwd()/'test-results'/('evidence-ui-storage-'+uuid4().hex[:8]))
os.environ['AWS_BEDROCK_ENABLED']='1'
import backend.app as app
# Deliberate OCR fixture for deterministic browser layout checks, not production
# extraction. Real Textract behavior is covered separately on the hosted app.
app.ocr.detect_lines=lambda data:[{'text':'Short delivered: 6 x olive oil 5 L (back order)',
    'rect':[.091565,.349501,.862489,.379581],'confidence':99.2}]
import fitz
output=Path('test-results/evidence-multipage.pdf')
output.parent.mkdir(exist_ok=True)
from PIL import Image
Image.new('RGB',(48,48),'#e7decd').save('test-results/evidence-pixel.png')
with fitz.open('seed/clients/Northwind Trading/Finance/Invoice INV-1043.pdf') as original,fitz.open() as fixture:
    fixture.insert_pdf(original)
    fixture.insert_pdf(original)
    output.write_bytes(fixture.tobytes())

async def answer(client,question,context,*args):
    if question.startswith('Slow'):await asyncio.sleep(3)
    def select(name,locator=None,page=None):
        doc=next(d for d in context if name in d['name'])
        blocks=doc['blocks']
        return next(b for b in blocks if (not locator or b['locator']==locator) and (not page or b.get('page')==page))
    citations=[];proposals=[];scope='client'
    if 'General' in question:scope='general'
    elif 'Sheet' in question:
        citations=[select('Management accounts','Income statement!B8'),select('Management accounts','Debtors!C10')]
    elif 'PDF' in question:citations=[select('Evidence multipage',page=2)]
    elif 'Scan' in question:citations=[select('Scan 14')]
    elif 'Image' in question:citations=[select('Evidence pixel')]
    else:citations=[select('Company details','p2')]
    if 'Propose' in question:
        source=citations[0]
        proposals=[{'document_id':source['document_id'],'title':'Fixture review','reason':'Explicit browser test','changes':[{'paragraph':'p2','value':'Fixture-only proposed text','source_ids':[source['id']]}]}]
    text='Fixture general answer.' if not citations else 'Fixture answer [1].'+(' Another finding [2].' if len(citations)>1 else '')
    text+=' [External link](https://example.com) ![remote image](https://example.com/pixel.png) `code [1]`'
    result={'answer':text,'scope':scope,'citations':[c['id'] for c in citations],'proposals':proposals}
    yield {'type':'text','text':text}
    yield {'type':'result','result':result}
app.answer_stream=answer
import uvicorn
uvicorn.run(app.app,host='127.0.0.1',port=8008)
