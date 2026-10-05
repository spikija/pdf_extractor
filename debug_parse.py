import sys
sys.path.insert(0, 'C:\\app\\pdf_extractor\\src')
from caa_pdf_extractor.utils import _extract_blocks, _extract_tables, _is_bold
from pathlib import Path
import pymupdf

pdf_path = Path('C:\\app\\pdf_extractor\\pdf_db\\2473679415\\1.pdf')
with pymupdf.open(str(pdf_path)) as doc:
    page = doc[0]
    blocks = _extract_blocks(page)
    print('blocks count:', len(blocks))
    for b in blocks[:5]:
        text = b.get('text', '')[:100]
        possible = b.get('possible_heading', False)
        print(f'block text: {text}, possible_heading: {possible}')
    
    tables = _extract_tables(page)
    print('tables count:', len(tables))
    for t in tables:
        print('  table rows:', t)