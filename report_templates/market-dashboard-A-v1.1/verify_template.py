from pathlib import Path
from hashlib import sha256
from bs4 import BeautifulSoup
import json, sys
root=Path(__file__).parent
m=json.loads((root/'manifest.json').read_text())
raw=(root/m['source_file']).read_bytes()
soup=BeautifulSoup(raw,'html.parser')
css=soup.find('style').get_text()
assert sha256(raw).hexdigest()==m['source_sha256'],'SOURCE_HASH_CHANGED'
assert sha256(css.encode()).hexdigest()==m['css_sha256'],'CSS_HASH_CHANGED'
assert [x.get('id') for x in soup.select('section[id^=part]')]==[x['id'] for x in m['layout']['sections']],'SECTIONS_CHANGED'
assert len(soup.select('.index-card'))==2,'INDEX_CARD_LAYOUT_CHANGED'
assert soup.select('.stock'),'STOCK_CARD_MISSING'
assert soup.select('.industry-item'),'INDUSTRY_CARD_MISSING'
assert soup.select('.decision'),'DECISION_CARD_MISSING'
assert '@media(max-width:520px)' in css,'MOBILE_MEDIA_MISSING'
print('PASS: A v1.1 frozen source, CSS, sections and cards')
