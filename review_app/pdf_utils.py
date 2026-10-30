from pathlib import Path
from functools import lru_cache
import fitz
from review_app.config import PDF_ROOT


def documents(patient_id, metadata, pdf_root=PDF_ROOT):
    folder = (pdf_root / str(patient_id)).resolve()
    if not folder.is_relative_to(pdf_root.resolve()):
        raise ValueError('Invalid patient ID')
    by_name = {Path(d.get('filename', '')).name: d for d in metadata}
    result = []
    for p in sorted(folder.glob('*.pdf')):
        if not p.resolve().is_relative_to(folder):
            continue
        d = by_name.get(p.name, {})
        with fitz.open(p) as doc:
            result.append({'path': str(p), 'filename': p.name,
                           'document_id': str(d.get('source_document_id', p.name)),
                           'date': d.get('document_date'), 'pages': len(doc)})
    return result


def resolve_source(docs, document_id, page):
    for d in docs:
        if d['document_id'] == str(document_id):
            try:
                number = int(page)
            except (ValueError, TypeError):
                raise ValueError('Evidence has no valid page')
            if not 1 <= number <= d['pages']:
                raise ValueError('Evidence page outside PDF')
            return d['path'], number
    raise ValueError('Evidence document is unavailable; no document guessed')


@lru_cache(maxsize=24)
def render_page(path, page, mtime):
    with fitz.open(path) as doc:
        return doc[page - 1].get_pixmap(matrix=fitz.Matrix(1.4, 1.4)).tobytes('png')
