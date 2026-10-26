"""Display an existing mesh with the current viewer; never rebuild geometry."""
import argparse
import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--model', required=True, type=Path)
parser.add_argument('--name', required=True)
parser.add_argument('--label', required=True)
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_-]+', args.name):
    parser.error('name must contain only letters, numbers, underscore or hyphen')
source = args.model.resolve()
model = json.loads(source.read_text(encoding='utf-8-sig'))
model.setdefault('metadata', {})['visual_review_label'] = args.label
destination = HERE / 'ui' / 'review-preview' / args.name
(destination / 'vendor').mkdir(parents=True, exist_ok=True)
template = (HERE / 'viewer.template.html').read_text(encoding='utf-8')
payload = json.dumps(model, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
page = template.replace('__MODEL_DATA__', payload)
page = page.replace('</head>', '<style>footer{display:none}</style></head>')
(destination / 'model.html').write_text(page, encoding='utf-8')
shutil.copyfile(HERE / 'viewer.css', destination / 'viewer.css')
for filename in ('viewer-runtime.js', 'LICENSE-three.txt'):
    shutil.copyfile(HERE / 'vendor' / filename, destination / 'vendor' / filename)
(destination / 'source.json').write_text(json.dumps({
    'source_model': str(source), 'label': args.label,
    'geometry_modified': False,
}, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'http://127.0.0.1:8765/ui/review-preview/{args.name}/model.html')
