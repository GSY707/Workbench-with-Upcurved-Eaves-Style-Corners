from pathlib import Path
import sys,json,inspect,hashlib
HERE=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(HERE.parents[1]/'资料整理/tmp/model_deps'),str(HERE/'engine')]
import stage05_core as m
import numpy as np
from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools
from OCP.TopoDS import TopoDS_Shape
from OCP.BRepAdaptor import BRepAdaptor_Curve
p=Path(sys.argv[1]); d=json.loads(p.read_text(encoding='utf-8'))
r=d['record']; controls=d['controls']; roots=d['roots']
s=TopoDS_Shape(); bp=HERE/'diagnostics/v02/fan01_shared_motherboard_B400.brep'
assert BRepTools.Read_s(s,str(bp),BRep_Builder()); shape=m.cq.Shape.cast(s)
S=np.array(r['S']); U=np.array(r['unit']); W=np.array(r['width_axis']); basis=np.column_stack((U,W))
out={'source_module':m.__file__,'function_source':inspect.getsource(m._measure_side_brep_generators),'brep_sha256':hashlib.sha256(bp.read_bytes()).hexdigest(),'input_path':str(p),'basis':basis.tolist(),'gram':(basis.T@basis).tolist(),'v_extremes':[controls[0]['v_mm'],controls[-1]['v_mm']],'sides':[]}
for label,idx in [('A',0),('B',len(controls)-1)]:
 e=controls[idx]; rt=roots[idx]; v0=float(e['v_mm']); qi=float(e['q_front_mm']); qe=float(e['q_E_mm']); qr=float(rt['q_root_mm'])
 rails={'tail_lower':(qr,qi,float(rt['H_R_board_mm']),float(e['H_front_mm'])),'tail_upper':(qr,qi,float(e['R_top_z_mm']),float(e['I_upper_z_mm'])),'outer_lower':(qi,qe,float(e['H_front_mm']),float(e['E_bottom_z_mm'])),'outer_upper':(qi,qe,float(e['I_upper_z_mm']),float(e['E_top_z_mm']))}
 groups={k:[] for k in rails}; rejected=[]
 for j,ed in enumerate(shape.Edges()):
  vv=ed.Vertices()
  if len(vv)!=2:continue
  pp=[np.array(x.Center().toTuple()) for x in vv]; coords=[np.linalg.solve(basis,x[:2]-S) for x in pp]
  if max(abs(x[1]-v0) for x in coords)>1e-5 or abs(coords[0][0]-coords[1][0])<1e-6:continue
  pts=[np.array(ed.positionAt(float(f)).toTuple()) for f in np.linspace(0,1,21)]; loc=[np.linalg.solve(basis,x[:2]-S) for x in pts]
  cand={}
  for name,(qa,qb,za,zb) in rails.items():
   if min(x[0] for x in coords)<min(qa,qb)-1e-3 or max(x[0] for x in coords)>max(qa,qb)+1e-3:continue
   cand[name]=max(max(abs(float(x[2])-(za+(c[0]-qa)/(qb-qa)*(zb-za))),abs(c[1]-v0)) for x,c in zip(pts,loc))
  name=min(cand,key=cand.get) if cand else None
  direction=pp[1]-pp[0]; norm=np.linalg.norm(direction)
  chorderr=max(float(np.linalg.norm(np.cross(x-pp[0],direction))/norm) for x in pts)
  pole_error=None
  if ed.geomType()=='BSPLINE':
   curve=BRepAdaptor_Curve(ed.wrapped).BSpline()
   pole_error=max(float(np.linalg.norm(np.cross(np.array(curve.Pole(k).Coord())-pp[0],direction))/norm) for k in range(1,curve.NbPoles()+1))
  item={'max_control_pole_chord_error':pole_error,'edge_index':j,'q_range':sorted([float(c[0]) for c in coords]),'type':ed.geomType(),'sample_max_chord_error':chorderr,'max_rail_residual':cand.get(name),'endpoints':[x.tolist() for x in pp]}
  (groups[name] if name else rejected).append(item)
 summary={}
 for name,edges in groups.items():
  merged=[]
  for lo,hi in sorted(x['q_range'] for x in edges):
   if not merged or lo>merged[-1][1]+1e-3:merged.append([lo,hi])
   else:merged[-1][1]=max(merged[-1][1],hi)
  qa,qb,*_=rails[name]
  summary[name]={'count':len(edges),'max_residual':max((x['max_rail_residual'] for x in edges),default=None),'max_chord_error':max((x['sample_max_chord_error'] for x in edges),default=None),'merged':merged,'expected':[qa,qb],'endpoint_error':max(abs(merged[0][0]-min(qa,qb)),abs(merged[-1][1]-max(qa,qb))) if merged else None,'types':sorted(set(x['type'] for x in edges))}
 out['sides'].append({'side':label,'v':v0,'summary':summary,'groups':groups,'rejected':rejected})
# Exact current helper and isolated halfwidth correction: same BRep/input, no model generation.
for key,fix in [('current_disk',False),('historical_halfwidth_bug',True)]:
 src=inspect.getsource(m._measure_side_brep_generators)
 if fix:src=src.replace('expected_v = -half_width if side == "A" else half_width','expected_v = 0.5 * float(edge["v_mm"])')
 ns=dict(m.__dict__);exec(src,ns)
 try:out[key]=ns['_measure_side_brep_generators'](shape,r,controls,roots,d['E_bottom_center_mm'],d.get('E_depth_world_Z_mm',d.get('E_depth_center_mm')))
 except Exception as exc:out[key]={'error':repr(exc)}
op=HERE/'diagnostics/v02/edge_advice_fan01_results.json';op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in out.items() if k not in ('function_source','sides')},ensure_ascii=False,indent=2)); print(json.dumps([{'side':x['side'],'summary':x['summary']} for x in out['sides']],indent=2))


