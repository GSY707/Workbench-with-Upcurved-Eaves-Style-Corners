from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


FAKE_CHILD = r'''import hashlib,json,sys,time
from pathlib import Path
config_path=Path(sys.argv[1]); out=Path(sys.argv[2]); generator=Path(sys.argv[3]); mode=sys.argv[4]
sys.path.insert(0,str(generator/'engine'))
from config import resolve_config
raw=json.loads(config_path.read_text(encoding='utf-8'))
if mode=='fail':
    print('generator: validated case=fake; starting geometry',flush=True)
    print('Traceback (most recent call last): RuntimeError: forced fake failure',flush=True)
    raise SystemExit(7)
if mode=='sleep':
    print('generator: validated case=fake; starting geometry',flush=True)
    print('stage04: board mesh sampled (1 panels); flyer poses start',flush=True)
    time.sleep(15)
    raise SystemExit(0)
resolved=resolve_config(raw)
resolved['derived']['fan_count']=11
resolved['derived']['fan_spacing_mm']=180.6851173174174
out.mkdir(parents=True,exist_ok=True)
part_ids=[]
for i in range(1,12): part_ids.extend([f'flying_fan_{i:02d}_x',f'flying_fan_{i:02d}_y'])
mesh={'metadata':{'case_id':resolved['case_id']},'parts':[{'id':p,'name':p,'group':'flying_fan','vertices':[0,0,0,1,0,0,0,1,0],'triangles':[0,1,2]} for p in part_ids],'control_lines':[]}
mesh_path=out/'model_mesh.json'; mesh_path.write_text(json.dumps(mesh,ensure_ascii=False),encoding='utf-8')
mesh_hash=hashlib.sha256(mesh_path.read_bytes()).hexdigest()
profiles=[{'id':f'F{i:02d}','index':i,'width_mm':resolved['parameters']['w_fly'],'area_mm2':100.0,'polygon_qz_mm':[[0,0],[1,0],[1,1],[0,1]]} for i in range(1,12)]
(out/'profiles.json').write_text(json.dumps({'case_id':resolved['case_id'],'source_mesh_sha256':mesh_hash,'profiles':profiles},ensure_ascii=False),encoding='utf-8')
pieces=[{'id':f'F{i:02d}','A':[0,0],'B':[0,1],'C':[1,1],'D':[1,0],'model_ids':[f'flying_fan_{i:02d}_x',f'flying_fan_{i:02d}_y'],'area_mm2':100.0} for i in range(1,12)]
layout={'input':{'case_id':resolved['case_id']},'layout':{'pieces':pieces,'board_width_x_mm':100.0,'board_height_y_mm':200.0,'board_thickness_mm':resolved['parameters']['w_fly'],'retain_ratio_percent':55.0},'translation':{'max_abs_error_mm':1.0,'rms_error_mm':0.5}}
(out/'layout.json').write_text(json.dumps(layout,ensure_ascii=False),encoding='utf-8')
(out/'parameters_resolved.json').write_text(json.dumps(resolved,ensure_ascii=False),encoding='utf-8')
validation={'passed':True,'input_geometry':{'case_id':resolved['case_id'],'fan_count_expected_per_side':11,'fan_count_actual_per_side':11},'entities':{'count':len(part_ids),'valid_single_solids':len(part_ids),'all_valid_single_solids':True}}
(out/'validation.json').write_text(json.dumps(validation,ensure_ascii=False),encoding='utf-8')
for name,body in {'index.html':'<html>index</html>','model.html':'<html>model</html>','layout.html':'<html>layout</html>','model.obj':'o fake','marking_points.csv':'id,x,y\nF01,0,0\n','layout.svg':'<svg xmlns="http://www.w3.org/2000/svg"></svg>'}.items():
    (out/name).write_text(body,encoding='utf-8')
files=[]
for path in sorted(out.rglob('*')):
    if path.is_file() and path.name!='manifest.json':
        files.append({'path':path.relative_to(out).as_posix(),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
(out/'manifest.json').write_text(json.dumps({'case_id':resolved['case_id'],'file_count':len(files),'files':files},ensure_ascii=False),encoding='utf-8')
print('generator: validated case=fake; starting geometry',flush=True)
print('stage05: dynamic stage04 base ready (2 parts)',flush=True)
print('generator: geometry complete (22 parts); exporting mesh',flush=True)
print('generator: offline viewer rendering complete',flush=True)
'''


def fake_command_builder(generator_dir: Path, mode_for_d, *, sleep_seconds: int = 15):
    def build(config_path: Path, output_path: Path) -> list[str]:
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
        d_value = raw.get("parameters", {}).get("D", 1200.0)
        mode = mode_for_d(d_value)
        return [sys.executable, "-X", "utf8", "-u", "-c", FAKE_CHILD,
                str(config_path), str(output_path), str(generator_dir), str(mode)]
    return build


def default_config(generator_dir: Path) -> dict:
    return json.loads((Path(generator_dir) / "configs" / "default.json").read_text(encoding="utf-8"))

