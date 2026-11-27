"""Bounded OCR line extraction; coordinates always come from an OCR engine."""
import io
import math
import os
import shutil

from botocore.exceptions import BotoCoreError, ClientError

from . import cloud


class OCRUnavailable(Exception):
    def __init__(self, retryable=False):
        self.retryable = retryable
        super().__init__('Text locations are unavailable; use the page image.')


def cache_mode():
    return (os.getenv('FOLIO_OCR_ENABLED') == '1', shutil.which('tesseract'))


def normalized(lines):
    result = []
    for line in lines[:1000]:
        text = str(line.get('text', '')).strip()[:2000]
        rect = line.get('rect', [])
        confidence = line.get('confidence', 0)
        if (not text or len(rect) != 4 or not all(isinstance(n,(int,float)) and math.isfinite(n) for n in rect)
                or not isinstance(confidence,(int,float)) or not math.isfinite(confidence) or confidence < 40):
            continue
        left,top,right,bottom = rect
        if not (0 <= left < right <= 1.001 and 0 <= top < bottom <= 1.001):
            continue
        result.append({'text':text,'rect':[round(max(0,left-.002),6),round(max(0,top-.002),6),
                                          round(min(1,right+.002),6),round(min(1,bottom+.002),6)],
                       'confidence':round(min(100,confidence),1)})
    return sorted(result,key=lambda line:(round(line['rect'][1],3),line['rect'][0]))


def tesseract_lines(data):
    import pytesseract
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        width,height = image.size
        words = pytesseract.image_to_data(image,output_type=pytesseract.Output.DICT,timeout=15)
    groups = {}
    for i,text in enumerate(words['text']):
        if not text.strip() or float(words['conf'][i]) < 40:
            continue
        key = (words['block_num'][i],words['par_num'][i],words['line_num'][i])
        left,top = words['left'][i],words['top'][i]
        right,bottom = left+words['width'][i],top+words['height'][i]
        group = groups.setdefault(key,{'words':[],'rect':[left,top,right,bottom],'confidence':100})
        group['words'].append(text)
        group['rect'] = [min(group['rect'][0],left),min(group['rect'][1],top),max(group['rect'][2],right),max(group['rect'][3],bottom)]
        group['confidence'] = min(group['confidence'],float(words['conf'][i]))
    return normalized([{'text':' '.join(g['words']),'confidence':g['confidence'],
                         'rect':[g['rect'][0]/width,g['rect'][1]/height,g['rect'][2]/width,g['rect'][3]/height]} for g in groups.values()])


def textract_lines(data):
    response = cloud.client('textract').detect_document_text(Document={'Bytes':data})
    lines = []
    for block in response.get('Blocks',[]):
        if block.get('BlockType') != 'LINE':
            continue
        box = block.get('Geometry',{}).get('BoundingBox',{})
        if not all(isinstance(box.get(k),(int,float)) for k in ('Left','Top','Width','Height')):
            continue
        lines.append({'text':block.get('Text',''),'confidence':block.get('Confidence',0),
                      'rect':[box['Left'],box['Top'],box['Left']+box['Width'],box['Top']+box['Height']]})
    return normalized(lines)


def detect_lines(data):
    if len(data)>5*1024*1024:
        raise OCRUnavailable()
    enabled,tesseract = cache_mode()
    if tesseract:
        try:
            lines = tesseract_lines(data)
            if lines or not enabled:
                return lines
        except (RuntimeError, OSError, ValueError):
            if not enabled:
                raise OCRUnavailable(retryable=True) from None
    if enabled:
        try:
            return textract_lines(data)
        except (ClientError,BotoCoreError):
            # This provider boundary deliberately falls back to visual reading.
            # Failed calls must not become permanently cached page-wide citations.
            raise OCRUnavailable(retryable=True) from None
    raise OCRUnavailable()
