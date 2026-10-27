#!/usr/bin/env python
"""Stage 04: ordinary and standing flying rafters, independent of stage 03 outputs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
S2_PATH = OUT / "stage02_core.py"
S3_PATH = OUT / "stage03_core.py"
SPEC2 = importlib.util.spec_from_file_location("stage02_for_stage04", S2_PATH)
s2 = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(s2)
SPEC3 = importlib.util.spec_from_file_location("stage03_for_stage04", S3_PATH)
s3 = importlib.util.module_from_spec(SPEC3)
SPEC3.loader.exec_module(s3)
cq = s2.cq
np = s2.np
WING_GROUP = "flying_rafters"
STRAIGHT_GROUP = "straight_flying_rafters"
STAGE_NAME = "立脚飞椽与正身飞椽"
PREVIEW_ONLY = "--preflight" in sys.argv
MESH_ONLY = "--mesh-only" in sys.argv


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def add(parts, pid, name, group, shape):
    s2.add_part(parts, pid, name, group, shape)


def build_stage03_parts(case):
    """Recreate the accepted stage-03 solids in memory without calling its writer."""
    case["_preview_only"] = False
    print("stage04: stage02 framing rebuild start", flush=True)
    parts, control = s2.make_parts(case)
    print(f"stage04: stage02 framing rebuild complete ({len(parts)} parts); young corner start", flush=True)
    by_id = {p["id"]: p for p in parts}
    young, young_info, young_axis = s3.young_corner(case, by_id["old_corner"]["shape"])
    add(parts, "young_corner", "嫩戗（沿stage03原体）", "young_corner", young)
    control.append({"id": "young_corner_axis", "name": "嫩戗弧线中心", "group": "young_corner", "points": young_axis})
    path = s3.rail_path(case)
    likou_records = []
    for side in ("x", "y"):
        shape, info, axis = s3.build_likou_half(case, path, mirror=(side == "y"))
        pid = f"likou_{side}"
        add(parts, pid, f"弯里口木{side.upper()}向（沿stage03原体）", "likou", shape)
        info["id"] = pid
        likou_records.append(info)
        control.append({"id": pid + "_axis", "name": pid + "中心线", "group": "likou", "points": axis[::max(1, len(axis)//12)]})
    print("stage04: young corner and curved likou complete; board surfaces start", flush=True)
    case["eave"] = {"inner_edge_y": case["e"] - float(case["p"]["likou_width"])}
    straight_records = s3.straight_boards(case, parts, control)
    fan_entries, fan_info = s3.fan_boards(case, parts, control)
    print(f"stage04: board surfaces complete; total base parts={len(parts)}", flush=True)
    return parts, control, young_info, likou_records, straight_records, fan_entries, fan_info


class MeshSurface:
    """Vertical-ray sampler over accepted stage-03 tessellation triangles."""
    def __init__(self, mesh, groups):
        self.tris = []
        selected = [p for p in mesh["parts"] if p["group"] in set(groups)]
        for part in selected:
            vv, tt = part["vertices"], part["triangles"]
            for j in range(0, len(tt), 3):
                ids = tt[j:j+3]
                pts = [(vv[k*3], vv[k*3+1], vv[k*3+2]) for k in ids]
                (x1,y1,z1),(x2,y2,z2),(x3,y3,z3) = pts
                den = (y2-y3)*(x1-x3)+(x3-x2)*(y1-y3)
                if abs(den) < 1e-9:
                    continue
                self.tris.append((min(x1,x2,x3),max(x1,x2,x3),min(y1,y2,y3),max(y1,y2,y3),
                                  x1,y1,z1,x2,y2,z2,x3,y3,z3,den,part["id"]))
        if not self.tris:
            raise ValueError(f"no triangles for groups {groups}")
        self.bounds=np.asarray([t[:4] for t in self.tris],dtype=float)
        self.coords=np.asarray([t[4:13] for t in self.tris],dtype=float)
        self.den=np.asarray([t[13] for t in self.tris],dtype=float)
        self.pids=[t[14] for t in self.tris]

    def z_range(self, x, y, tol=1e-7):
        candidate=np.flatnonzero((x>=self.bounds[:,0]-tol)&(x<=self.bounds[:,1]+tol)&
                                 (y>=self.bounds[:,2]-tol)&(y<=self.bounds[:,3]+tol))
        if len(candidate)==0:
            return None
        c=self.coords[candidate]; den=self.den[candidate]
        x1,y1,z1,x2,y2,z2,x3,y3,z3=c.T
        a=((y2-y3)*(x-x3)+(x3-x2)*(y-y3))/den
        b=((y3-y1)*(x-x3)+(x1-x3)*(y-y3))/den
        cc=1.0-a-b
        inside=(np.minimum(np.minimum(a,b),cc)>=-2e-6)&(np.maximum(np.maximum(a,b),cc)<=1.0+2e-6)
        if not np.any(inside):
            return None
        zi=(a*z1+b*z2+cc*z3)[inside]
        hits=[self.pids[candidate[i]] for i in np.flatnonzero(inside)]
        return {"min_z_mm":float(np.min(zi)),"max_z_mm":float(np.max(zi)),
                "hits":len(zi),"part_ids":sorted(set(hits))}


def cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def intersect_ray_polyline(origin, unit, points, label):
    hits = []
    for i, (a,b) in enumerate(zip(points[:-1], points[1:])):
        seg = (b[0]-a[0], b[1]-a[1])
        den = cross(unit, seg)
        if abs(den) < 1e-12:
            continue
        ao = (a[0]-origin[0], a[1]-origin[1])
        q = cross(ao, seg)/den
        u = cross(ao, unit)/den
        if q >= -1e-6 and -1e-7 <= u <= 1.0+1e-7:
            hits.append((max(0.0,q), (origin[0]+unit[0]*max(0.0,q), origin[1]+unit[1]*max(0.0,q)), i, min(1.0,max(0.0,u))))
    if not hits:
        raise ValueError(f"ray misses {label}")
    hits.sort(key=lambda x:x[0])
    q, point, i, u = hits[0]
    return point, q, {"segment_index":i,"segment_fraction":u}


def point_at_circle_arc(A, B, fraction, count=257):
    dx,dy = B[0]-A[0], B[1]-A[1]
    if dy <= 0:
        raise ValueError("end curve requires B above A in plan")
    radius = (dx*dx+dy*dy)/(2.0*dy)
    theta = math.atan2(dx, radius-dy)
    ang = theta*float(fraction)
    return (A[0]+radius*math.sin(ang), A[1]+radius*(1.0-math.cos(ang)))


def circle_arc_points(A, B, count=257):
    dx,dy = B[0]-A[0], B[1]-A[1]
    radius = (dx*dx+dy*dy)/(2.0*dy)
    theta = math.atan2(dx, radius-dy)
    pts = [point_at_circle_arc(A,B,i/(count-1),count) for i in range(count)]
    lengths=[0.0]
    for a,b in zip(pts[:-1],pts[1:]): lengths.append(lengths[-1]+math.dist(a,b))
    return pts, radius, theta, lengths


def young_side_tip(young_info, case):
    center = np.array(young_info["centerline"][-1], dtype=float)
    src = case["p"]
    chord, rise = float(src["L_n"]), float(src["U_n"])
    run = math.sqrt(chord*chord-rise*rise)
    u = np.array([case["old_unit"][0],case["old_unit"][1],0.0],dtype=float)
    tangent = np.array([u[0]*run/chord,u[1]*run/chord,rise/chord])
    width = np.array([-u[1],u[0],0.0])
    height = np.cross(tangent,width)
    width *= float(young_info["tip_width_mm"])
    body_h = float(young_info["tip_body_height_mm"])
    top_edge = center + width*0.5 + height*body_h
    return top_edge.tolist(), {"side":"canonical X wing; opposite wing is reflected across the configured bisector","width_axis":width.tolist(),
                               "height_axis":height.tolist(),"body_height_mm":body_h,
                               "source_tip_center":center.tolist()}


def outer_end_curve(case, young_info):
    A = (float(case["old_start"][0]), case["e"]+float(case["p"]["p"]))
    B3, B_info = young_side_tip(young_info, case)
    B = (B3[0],B3[1])
    pts, radius, theta, lengths = circle_arc_points(A,B)
    p = case["p"]
    # Auto datum tied to the current straight-bank roof and section. The
    # advanced control is a relative lift, never a legacy absolute elevation.
    A_top = (float(case["fan_top_intercept"]) - float(p["t"]) * float(p["e"])
             + float(p["board_lower_t"]) - float(p["fly_tip_t"])
             + float(p["flyer_outer_tip_offset_mm"]))
    return {"A":A,"B":B,"A_top_z_mm":A_top,
            "B_top_z_mm":B3[2],"B_vertex_3d":B3,"B_construction":B_info,
            "points":pts,"radius_mm":radius,"central_angle_rad":theta,
            "lengths":lengths,"length_mm":lengths[-1],
            "derived_mid_sagitta_mm":(B[1]-A[1])*0.5-(pts[len(pts)//2][1]-A[1])}


def arc_fraction_at_point(point, curve):
    # Find the closest sampled point, then interpolate its arc-length fraction.
    pts, lengths = curve["points"], curve["lengths"]
    best = min(range(len(pts)), key=lambda i:(pts[i][0]-point[0])**2+(pts[i][1]-point[1])**2)
    return lengths[best]/lengths[-1]


def make_paths(case):
    path = s3.rail_path(case)
    width = float(case["p"]["likou_width"])
    inner=[]
    for i,p in enumerate(path):
        p0,p1=path[max(0,i-1)],path[min(len(path)-1,i+1)]
        dx,dy=p1[0]-p0[0],p1[1]-p0[1]
        n=math.hypot(dx,dy)
        inward=(dy/n,-dx/n)
        inner.append((p[0]+inward[0]*width,p[1]+inward[1]*width))
    return path, inner


def base_z_likou(case, x, y):
    if x < float(case["old_start"][0])-1e-5 and abs(y-case["e"]) <= float(case["p"]["likou_width"])+1e-4:
        return float(case["fan_top_intercept"])-float(case["p"]["t"])*y
    return s2.fan_top_z(case,x,y)


def likou_height_at_length(case, path, q):
    lengths=[0.0]
    for a,b in zip(path[:-1],path[1:]): lengths.append(lengths[-1]+math.dist(a,b))
    total=lengths[-1]
    q=min(total,max(0.0,q))
    for i in range(len(path)-1):
        if lengths[i] <= q <= lengths[i+1]:
            f=(q-lengths[i])/max(1e-12,lengths[i+1]-lengths[i])
            p=(path[i][0]+f*(path[i+1][0]-path[i][0]),path[i][1]+f*(path[i+1][1]-path[i][1]))
            t=q/total
            h=float(case["p"]["likou_h_regular"])+(float(case["p"]["likou_h_regular"])*float(case["p"]["likou_h_corner_ratio"])-float(case["p"]["likou_h_regular"]))*s3.smooth(t)
            return p,h,q
    return path[-1],float(case["p"]["likou_h_regular"])*float(case["p"]["likou_h_corner_ratio"]),q


def find_path_station(point, path):
    best=None
    q=0.0
    lengths=[0.0]
    for a,b in zip(path[:-1],path[1:]): lengths.append(lengths[-1]+math.dist(a,b))
    for i,(a,b) in enumerate(zip(path[:-1],path[1:])):
        vx,vy=b[0]-a[0],b[1]-a[1]
        den=vx*vx+vy*vy
        f=max(0.0,min(1.0,((point[0]-a[0])*vx+(point[1]-a[1])*vy)/den))
        pp=(a[0]+f*vx,a[1]+f*vy)
        d=(point[0]-pp[0])**2+(point[1]-pp[1])**2
        if best is None or d<best[0]:
            best=(d,i,f)
    _,i,f=best
    return lengths[i]+f*(lengths[i+1]-lengths[i])


def path_cumulative(path):
    lengths=[0.0]
    for a,b in zip(path[:-1],path[1:]): lengths.append(lengths[-1]+math.dist(a,b))
    return lengths


def path_point_at(path, q):
    lengths=path_cumulative(path)
    q=min(lengths[-1],max(0.0,float(q)))
    for i in range(len(path)-1):
        if lengths[i] <= q <= lengths[i+1]:
            f=(q-lengths[i])/max(1e-12,lengths[i+1]-lengths[i])
            return (path[i][0]+f*(path[i+1][0]-path[i][0]),path[i][1]+f*(path[i+1][1]-path[i][1])),q
    return tuple(path[-1]),lengths[-1]


def path_top_z(case, path, q):
    p,q=path_point_at(path,q)
    lengths=path_cumulative(path)
    t=q/lengths[-1]
    h=float(case["p"]["likou_h_regular"])+(float(case["p"]["likou_h_regular"])*float(case["p"]["likou_h_corner_ratio"])-float(case["p"]["likou_h_regular"]))*s3.smooth(t)
    return base_z_likou(case,*p)-0.2+h


def seat_slope(case, path, outer_point, unit, likou_sampler, groups_present=True):
    w=(-unit[1],unit[0])
    half=float(case["p"]["w_fly"])/2.0
    q0=find_path_station(outer_point,path)
    samples=[]
    for sign in (-1.0,1.0):
        pt=(outer_point[0]+w[0]*half*sign,outer_point[1]+w[1]*half*sign)
        q=find_path_station(pt,path)
        z=path_top_z(case,path,q)
        actual=likou_sampler.z_range(*path_point_at(path,q)[0]) if likou_sampler else None
        samples.append({"offset_xy":pt,"projected_path_station_mm":q,"path_xy":path_point_at(path,q)[0],
                        "top_z_from_template_mm":z,"actual_mesh_range":actual})
    g=(samples[1]["top_z_from_template_mm"]-samples[0]["top_z_from_template_mm"])/(2.0*half)
    return g,{"method":"local slope of stage03 likou top along the nearest path station, projected from fly width edges",
              "seat_station_mm":q0,"samples":samples}


def projection_records(case, fan_path, inner_path, end_curve, board_sampler, likou_sampler, young_info):
    O=case["fan_surface"]["O"]
    records=[]
    width=float(case["p"]["w_fly"])
    # Fan rays use actual stage-02 centerline directions; intersect each fly curve independently.
    for fan in case["fan_geometry"]["fan_rafters"]:
        a=fan["centerline_plan_start"]; b=fan["centerline_plan_end"]
        dx,dy=b[0]-a[0],b[1]-a[1]
        norm=math.hypot(dx,dy); unit=(dx/norm,dy/norm)
        E, qe, end_hit=intersect_ray_polyline(O,unit,end_curve["points"],"flying outer end arc")
        S, qs, seat_hit=intersect_ray_polyline(O,unit,fan_path,"outer likou path")
        I, qi, inner_hit=intersect_ray_polyline(O,unit,inner_path,"inner likou path")
        if not (qi < qs < qe):
            raise ValueError(f"fan ray {fan['index']} intersection order I/S/E is invalid: {qi},{qs},{qe}")
        w=(-unit[1],unit[0])
        g,g_info=seat_slope(case,fan_path,S,unit,likou_sampler)
        L=qe-qs
        tip_t=float(case["p"]["fly_tip_t"])
        root_thick=tip_t+(float(young_info["root_body_height_mm"])*float(case["p"]["fly_corner_root_ratio"])-tip_t)*s3.smooth((fan["index"]-1)/max(1,len(case["fan_geometry"]["fan_rafters"])-1))
        R=(S[0]-1.5*L*unit[0],S[1]-1.5*L*unit[1])
        br=board_sampler.z_range(*R) if board_sampler else None
        records.append({"family":"fan","side":"x","index":fan["index"],"role":fan.get("role", "fan"),
                        "is_boundary":bool(fan.get("is_boundary", False)),"source_rafter_id":fan["id"],
                        "origin":list(O),"unit":list(unit),"width_axis":list(w),"S":list(S),"I":list(I),"E":list(E),
                        "q_I_mm":qi-qs,"l_SE_mm":L,"ordinary_tail_mm":1.5*L,"ordinary_R":list(R),
                        "outer_curve_fraction":arc_fraction_at_point(E,end_curve),"outer_curve_top_z_A_mm":end_curve["A_top_z_mm"],
                        "outer_curve_top_z_B_mm":end_curve["B_top_z_mm"],"g":g,"g_source":g_info,
                        "root_thickness_normal_mm":root_thick,"tip_thickness_normal_mm":tip_t,
                        "board_top_at_ordinary_R":br,"path_hit_segments":{"S":seat_hit,"I":inner_hit,"E":end_hit}})
    # Straight bank rays retain their established parallel eave-rafter center lines.
    for rafter in case["eave_rafters_geometry"]["rafters"]:
        i=int(rafter["index"])
        xc=float(rafter["center_x_mm"])
        role=str(rafter.get("role", "regular"))
        is_boundary=bool(rafter.get("is_boundary", False))
        S=(float(xc),float(case["e"])); I=(float(xc),float(case["e"]-float(case["p"]["likou_width"]))); E=(float(xc),float(case["e"]+float(case["p"]["p"])))
        unit=(0.0,1.0); w=(-1.0,0.0); L=float(case["p"]["p"])
        # Only the corner-transition straight member intersects the modeled curved-likou return.
        transition_limit = (float(case["old_start"][0]) - float(case["purlin_radius"])
                            - float(case["eave_rafters_width"]) / 2.0)
        if float(xc) <= transition_limit + 1e-6:
            g,g_info=seat_slope(case,fan_path,S,unit,likou_sampler)
        else:
            g,g_info=0.0,{"method":"straight eave bank outside the modeled curved-likou return; flat bank cross-slope assumption","samples":[]}
        R=(float(xc),float(case["e"]-1.5*L))
        br=board_sampler.z_range(*R) if board_sampler else None
        records.append({"family":"straight","side":"x","index":i,"role":role,
                        "is_boundary":is_boundary,"source_rafter_id":rafter["id"],
                        "origin":None,"unit":list(unit),"width_axis":list(w),"S":list(S),"I":list(I),"E":list(E),
                        "q_I_mm":-float(case["p"]["likou_width"]),"l_SE_mm":L,"ordinary_tail_mm":1.5*L,"ordinary_R":list(R),
                        "outer_curve_fraction":0.0,"outer_curve_top_z_A_mm":end_curve["A_top_z_mm"],"outer_curve_top_z_B_mm":None,
                        "g":g,"g_source":g_info,"root_thickness_normal_mm":float(case["p"]["fly_tip_t"]),"tip_thickness_normal_mm":float(case["p"]["fly_tip_t"]),
                        "board_top_at_ordinary_R":br,"path_hit_segments":None})
    return records


def fly_top_at(E, fraction, gamma, curve):
    return curve["A_top_z_mm"]+(curve["B_top_z_mm"]-curve["A_top_z_mm"])*(fraction**gamma)


def sample_bottom_line(record, R, bR, bE, point, lateral=0.0):
    S=np.array(record["S"],dtype=float); U=np.array(record["unit"],dtype=float)
    q=float(np.dot(np.array(point,dtype=float)-S,U))
    qR=float(np.dot(np.array(R,dtype=float)-S,U))
    qE=float(np.dot(np.array(record["E"],dtype=float)-S,U))
    t=(q-qR)/(qE-qR)
    return float(bR+(bE-bR)*t+record["g"]*lateral)


def choose_types(records, gamma, end_curve, board_sampler, likou_sampler, case):
    """Keep the reviewed regular-tail rule only for the straight eave bank."""
    chosen=[]
    for r in records:
        if r["family"] != "straight":
            raise ValueError("choose_types is reserved for straight eave flyers; fan flyers use correct_fan_pose")
        g=float(r["g"]); scale=math.sqrt(1.0+g*g)
        dE=float(case["p"]["fly_tip_t"])*scale
        bE=fly_top_at(r["E"],r["outer_curve_fraction"],gamma,end_curve)-dE
        R=tuple(r["ordinary_R"]); sr=r["board_top_at_ordinary_R"]
        if sr is None:
            raise ValueError(f"straight fly {r['index']} ordinary tail misses stage03 board at R={R}")
        bR=sr["max_z_mm"]
        chosen.append({**r,"type":"ordinary_tail_board_supported","R":list(R),"bR_mm":bR,"bE_mm":bE,
                       "tip_depth_vertical_mm":dE,"root_depth_vertical_mm":float(r["root_thickness_normal_mm"])*scale,
                       "slope_scale":scale,"seat_min_bottom_minus_likou_top_mm":None,
                       "ordinary_R_board_hit":True,"gamma":gamma})
    return chosen,[]

def plane_bottom_at(record,bE,m,q,v=0.0):
    return float(bE)-float(m)*(float(record["l_SE_mm"])-float(q))+float(record["g"])*float(v)


def plane_depth_at(record,q,qR0):
    h0=float(record["root_thickness_normal_mm"]); hE=float(record["tip_thickness_normal_mm"])
    t=(float(q)-float(qR0))/(float(record["l_SE_mm"])-float(qR0))
    return float(h0+(hE-h0)*t)*math.sqrt(1.0+float(record["g"])**2)


def board_section(record,q,board_sampler,side_offsets):
    S=np.asarray(record["S"],dtype=float); U=np.asarray(record["unit"],dtype=float); W=np.asarray(record["width_axis"],dtype=float)
    found=[]
    for v in side_offsets:
        P=S+U*float(q)+W*float(v)
        z=board_sampler.z_range(float(P[0]),float(P[1])) if board_sampler else None
        found.append((float(v),P,z))
    return found


def adaptive_seat_refinement_samples(q_coarse, coverage_counts, width_count,
                                    target_step_mm=0.05, max_samples=5000):
    """Build a bounded local q scan after coarse seat samples miss full coverage."""
    q_values=[float(q) for q in q_coarse]
    coverage=[int(n) for n in coverage_counts]
    if len(q_values)!=len(coverage) or not q_values:
        return {"q_values_mm":[],"step_mm":None,"ranges_mm":[],"scope":"none"}
    q_min=min(q_values); q_max=max(q_values)
    coarse_step=max((abs(b-a) for a,b in zip(q_values,q_values[1:])),default=0.0)
    max_covered=max(coverage)
    # A near-full coarse section localizes likely boundary crossings. If the
    # coarse grid sees less than width_count-2 stations anywhere, refine the
    # complete q span so a narrow common interval cannot be skipped silently.
    if max_covered >= max(1,int(width_count)-2):
        anchors=[q for q,n in zip(q_values,coverage) if n>=max_covered-1]
        raw_ranges=[(max(q_min,q-coarse_step),min(q_max,q+coarse_step)) for q in anchors]
        scope="near_best_coverage"
    else:
        raw_ranges=[(q_min,q_max)]
        scope="full_q_range"
    raw_ranges.sort()
    ranges=[]
    for lo,hi in raw_ranges:
        if not ranges or lo>ranges[-1][1]+1e-9:
            ranges.append([lo,hi])
        else:
            ranges[-1][1]=max(ranges[-1][1],hi)
    ranges=[(float(lo),float(hi)) for lo,hi in ranges if hi>=lo]
    total_length=sum(hi-lo for lo,hi in ranges)
    budget=max(2,int(max_samples))
    target=max(float(target_step_mm),1e-9)
    step=max(target,total_length/max(1,budget-len(ranges)))
    refined=[]
    for lo,hi in ranges:
        count=max(2,int(math.ceil((hi-lo)/step))+1)
        refined.extend(float(q) for q in np.linspace(lo,hi,count))
    refined=sorted(set(round(q,9) for q in refined))
    return {"q_values_mm":refined,"step_mm":float(step),
            "ranges_mm":[[lo,hi] for lo,hi in ranges],"scope":scope}


def correct_fan_pose(records,gamma,end_curve,board_sampler,likou_sampler,case,delta=3.0,tail_gap=1.0,
                     seat_q_count=31,width_count=9,tail_step_mm=5.0):
    """Use one global longitudinal plane per flyer, then trim to actual board contact."""
    results=[]; failures=[]
    half=float(case["p"]["w_fly"])/2.0
    side_offsets=np.linspace(-half,half,width_count)
    for r in records:
        i=int(r["index"]); l=float(r["l_SE_mm"]); qI=float(r["q_I_mm"]); qR0=-1.5*l
        if not (qR0<qI<0<l):
            failures.append({"index":i,"reason":"invalid R0/I/S/E ordering","qR0":qR0,"qI":qI,"l":l}); continue
        R0=np.asarray(r["S"],dtype=float)+np.asarray(r["unit"],dtype=float)*qR0
        board0=board_sampler.z_range(float(R0[0]),float(R0[1])) if board_sampler else None
        if not board0:
            failures.append({"index":i,"reason":"R0 has no actual stage03 board mesh hit","R0":R0.tolist()}); continue
        bR0=float(board0["max_z_mm"])
        bE=fly_top_at(r["E"],r["outer_curve_fraction"],gamma,end_curve)-float(r["tip_thickness_normal_mm"])*math.sqrt(1.0+float(r["g"])**2)
        m0=(bE-bR0)/(2.5*l)
        q_seat=np.linspace(qI,0.0,seat_q_count)
        seat_candidates=[]; seat_missing=[]; seat_coverage_counts=[]
        seat_section_cache={}
        for q in q_seat:
            pts=board_section(r,float(q),likou_sampler,side_offsets)
            seat_section_cache[round(float(q),9)]=pts
            covered_count=sum(z is not None for _,_,z in pts)
            seat_coverage_counts.append(covered_count)
            if covered_count<len(side_offsets):
                seat_missing.append(float(q)); continue
            ratios=[(bE+float(r["g"])*v-float(z["max_z_mm"])+delta)/(l-float(q)) for v,_,z in pts]
            seat_candidates.append({"q_mm":float(q),"m_required":max(ratios),"max_ratio_at_v_mm":side_offsets[int(np.argmax(ratios))]})
        seat_refinement={"q_values_mm":[],"step_mm":None,"ranges_mm":[],"scope":"not_needed"}
        seat_refinement_sample_count=0
        if not seat_candidates:
            seat_refinement=adaptive_seat_refinement_samples(
                q_seat,seat_coverage_counts,len(side_offsets))
            coarse_keys={round(float(q),9) for q in q_seat}
            refined_q=[q for q in seat_refinement["q_values_mm"] if round(float(q),9) not in coarse_keys]
            seat_refinement_sample_count=len(refined_q)
            if refined_q:
                print(f"stage04: fan {i} seat coverage missed coarse q grid; refining "
                      f"{seat_refinement['scope']} with {seat_refinement_sample_count} samples "
                      f"at about {seat_refinement['step_mm']:.3f}mm",flush=True)
            for q in refined_q:
                pts=board_section(r,float(q),likou_sampler,side_offsets)
                seat_section_cache[round(float(q),9)]=pts
                if any(z is None for _,_,z in pts):
                    seat_missing.append(float(q)); continue
                ratios=[(bE+float(r["g"])*v-float(z["max_z_mm"])+delta)/(l-float(q)) for v,_,z in pts]
                seat_candidates.append({"q_mm":float(q),"m_required":max(ratios),"max_ratio_at_v_mm":side_offsets[int(np.argmax(ratios))]})
        if not seat_candidates:
            failures.append({"index":i,"reason":f"no q candidate has actual likou coverage across the full {2.0*half:g}mm width",
                             "missing_q_count":len(seat_missing),"seat_candidate_count":0,
                             "seat_refinement_sample_count":seat_refinement_sample_count,
                             "seat_refinement_step_mm":seat_refinement["step_mm"],
                             "seat_refinement_scope":seat_refinement["scope"]}); continue
        seat_min=min(seat_candidates,key=lambda x:x["m_required"])
        mseat=float(seat_min["m_required"]); m=max(m0,mseat)
        q_scan_count=max(31,int(math.ceil((qI-qR0)/tail_step_mm))+1)
        q_tail=np.linspace(qI,qR0,q_scan_count)
        tail_states=[]
        for q in q_tail:
            pts=board_section(r,float(q),board_sampler,side_offsets)
            if any(z is None for _,_,z in pts):
                tail_states.append({"q_mm":float(q),"covered":False,"missing_width_offsets_mm":[v for v,_,z in pts if z is None]})
                continue
            bvals=[plane_bottom_at(r,bE,m,float(q),v) for v in side_offsets]
            clear=[b-float(z["max_z_mm"]) for b,(_,_,z) in zip(bvals,pts)]
            depth=plane_depth_at(r,float(q),qR0)
            overlap=[(b<=float(z["max_z_mm"])+1e-6 and b+depth>=float(z["min_z_mm"])-1e-6)
                     for b,(_,_,z) in zip(bvals,pts)]
            tail_states.append({"q_mm":float(q),"covered":True,"clearance_min_mm":min(clear),
                                "clearance_max_mm":max(clear),"intersecting_width_offsets_mm":[float(v) for v,o in zip(side_offsets,overlap) if o],
                                "depth_vertical_mm":depth,"part_ids":sorted({pid for _,_,z in pts for pid in z["part_ids"]})})
        full_indices=[j for j,s in enumerate(tail_states) if s["covered"]]
        if not full_indices:
            failures.append({"index":i,"reason":"no full-width stage03 board coverage from I to R0","tail_sample_count":len(tail_states)}); continue
        first_full=full_indices[0]
        # The I-side starts at the lower-board perimeter: its outer width edge
        # can be outside the actual board outline. Keep those samples explicit;
        # do not use them as evidence of a safe/no-collision region. From the
        # first full-width section onward the footprint must remain continuous.
        leading_missing_offsets=sorted({float(v) for s in tail_states[:first_full]
                                        for v in s.get("missing_width_offsets_mm",[])})
        leading_internal_gaps=[]
        for v in side_offsets:
            seen_covered=False
            for state in tail_states[:first_full]:
                is_covered=float(v) not in state.get("missing_width_offsets_mm",[])
                if is_covered: seen_covered=True
                elif seen_covered: leading_internal_gaps.append(float(v)); break
        if leading_internal_gaps:
            failures.append({"index":i,"reason":"board mesh has a coverage gap within the I-side perimeter transition; missing samples are not treated as collision-free",
                             "lateral_offsets_with_internal_gap_mm":sorted(set(leading_internal_gaps)),
                             "first_full_coverage_q_mm":tail_states[first_full]["q_mm"]}); continue
        uncovered_after=[s["q_mm"] for s in tail_states[first_full:] if not s["covered"]]
        if uncovered_after:
            failures.append({"index":i,"reason":"board mesh coverage is discontinuous after first full-width hit; cannot treat gaps as collision-free",
                             "first_full_coverage_q_mm":tail_states[first_full]["q_mm"],"uncovered_q_samples_mm":uncovered_after[:12],
                             "uncovered_count":len(uncovered_after)}); continue
        usable=tail_states[first_full:]
        # q proceeds from the lip inward. Contact is the first point where the
        # lower face touches/crosses actual board top anywhere across its width.
        hit_index=next((j for j,s in enumerate(usable) if s["clearance_min_mm"]<=0.0),None)
        if hit_index is None:
            failures.append({"index":i,"reason":"full-width board covered but corrected plane never contacts board before R0",
                             "first_full_coverage_q_mm":usable[0]["q_mm"],"R0_q_mm":qR0,
                             "last_clearance_min_mm":usable[-1]["clearance_min_mm"]}); continue
        hit_state=usable[hit_index]
        if hit_index==0:
            q_hit=float(hit_state["q_mm"])
            no_safe_precontact=True
        else:
            prev=usable[hit_index-1]; cur=hit_state
            g0=float(prev["clearance_min_mm"]); g1=float(cur["clearance_min_mm"])
            if abs(g0-g1)>1e-12:
                q_hit=float(prev["q_mm"])+(0.0-g0)*(float(cur["q_mm"])-float(prev["q_mm"]))/(g1-g0)
            else: q_hit=float(cur["q_mm"])
            no_safe_precontact=False
        # Preserve a full ordinary tail only when the unrotated R0 remains a
        # full-width, supported, non-interfering endpoint. Otherwise cut one
        # millimetre outboard of first contact and retain the same plane.
        end_state=usable[-1]
        near_contact=abs(float(end_state["clearance_min_mm"]))<=0.25 and abs(float(end_state["clearance_max_mm"]))<=1.0
        if abs(m-m0)<=1e-10 and near_contact and not any(s.get("clearance_min_mm",1.0)<-0.25 for s in usable[:-1]):
            qR=qR0; typ="ordinary_tail_board_supported"; trim_reason="R0 full-width supported with no sampled penetration"
        else:
            qR=q_hit+float(tail_gap)
            typ="tail_trimmed_at_actual_board_contact"
            trim_reason=f"trimmed {float(tail_gap):g}mm outboard of first full-width actual-board contact after common plane rotation"
            if qR>=qI:
                # I is the conservative innermost fallback only if its actual
                # board sample is covered and does not materially penetrate.
                Ipts=board_section(r,qI,board_sampler,side_offsets)
                if any(z is None for _,_,z in Ipts):
                    failures.append({"index":i,"reason":"first contact at I and I lacks full-width board coverage; no valid tail boundary",
                                     "qI_mm":qI,"q_hit_mm":q_hit}); continue
                Iclear=[plane_bottom_at(r,bE,m,qI,v)-float(z["max_z_mm"]) for v,_,z in Ipts]
                if min(Iclear)<-0.25:
                    failures.append({"index":i,"reason":"first contact at I; fallback I still materially penetrates board",
                                     "qI_mm":qI,"minimum_I_clearance_mm":min(Iclear)}); continue
                qR=qI; trim_reason="contact lies at/outboard of I; conservative fallback to I after full-width compatibility check"
        if qR>=qR0-1e-6:
            pass
        if qR>=0.0:
            failures.append({"index":i,"reason":"tail trim leaves no positive span behind S","qR_mm":qR}); continue
        R=np.asarray(r["S"],dtype=float)+np.asarray(r["unit"],dtype=float)*qR
        bR=plane_bottom_at(r,bE,m,qR,0.0)
        trim_pts=board_section(r,qR,board_sampler,side_offsets)
        if any(z is None for _,_,z in trim_pts):
            failures.append({"index":i,"reason":f"trimmed tail endpoint lacks actual board coverage across the full {2.0*half:g}mm width",
                             "qI_mm":qI,"q_hit_mm":q_hit,"first_full_coverage_q_mm":tail_states[first_full]["q_mm"],
                             "qR_mm":qR,"missing_width_offsets_mm":[v for v,_,z in trim_pts if z is None]}); continue
        trim_clear=[plane_bottom_at(r,bE,m,qR,v)-float(z["max_z_mm"]) for v,_,z in trim_pts]
        if min(trim_clear)<-0.25:
            failures.append({"index":i,"reason":"trimmed tail still materially penetrates the actual board at qR",
                             "qR_mm":qR,"clearance_range_mm":[min(trim_clear),max(trim_clear)]}); continue
        hR0=float(r["root_thickness_normal_mm"])
        tR=(qR-qR0)/(l-qR0)
        hR=hR0+(float(r["tip_thickness_normal_mm"])-hR0)*tR
        tail_length=-qR
        actual_support=abs(qR-qR0)<=1e-6
        if not actual_support:
            typ="tail_trimmed_at_actual_board_contact"
        # Record slot-floor depth; do not impose the retired 20mm uplift target.
        floor_values=[]; floor_missing=0; floor_probe_count=0
        floor_q_values=sorted(seat_section_cache)
        for q_key in floor_q_values:
            q=float(q_key)
            pts=seat_section_cache[q_key]
            floor_probe_count+=len(pts)
            floor_missing+=sum(z is None for _,_,z in pts)
            if any(z is None for _,_,z in pts): continue
            for v,_,z in pts:
                floor_values.append(plane_bottom_at(r,bE,m,float(q),v)-float(z["min_z_mm"]))
        floor_min=min(floor_values,default=None)
        if floor_min is None:
            failures.append({"index":i,"reason":"slot floor has no actual likou mesh samples",
                             "sampled_floor_min_mm":floor_min}); continue
        results.append({**r,"type":typ,"R":R.tolist(),"bR_mm":bR,"bE_mm":bE,"m0":m0,"m_seat":mseat,"m":m,
                        "root_thickness_normal_mm":hR,
                        "slope_angle_deg":math.degrees(math.atan(m)),"q_R0_mm":qR0,"q_R_mm":qR,"q_hit_mm":q_hit,
                        "tail_trim_gap_mm":float(tail_gap),"tail_length_mm":tail_length,"tail_ratio_actual":tail_length/l,
                        "bR0_mm":bR0,"root_thickness_R0_normal_mm":hR0,"root_thickness_trim_normal_mm":hR,
                        "tip_depth_vertical_mm":float(r["tip_thickness_normal_mm"])*math.sqrt(1.0+float(r["g"])**2),
                        "root_depth_vertical_mm":hR*math.sqrt(1.0+float(r["g"])**2),"slope_scale":math.sqrt(1.0+float(r["g"])**2),
                        "seat_delta_mm":delta,"seat_candidate_q_mm":seat_min["q_mm"],"seat_candidate_m_required":mseat,
                        "seat_candidate_width_offset_mm":seat_min["max_ratio_at_v_mm"],"seat_candidate_count":len(seat_candidates),
                        "seat_missing_candidate_count":len(seat_missing),
                        "seat_refinement_sample_count":seat_refinement_sample_count,
                        "seat_refinement_step_mm":seat_refinement["step_mm"],
                        "seat_refinement_scope":seat_refinement["scope"],
                        "tail_first_full_board_q_mm":tail_states[first_full]["q_mm"],
                        "tail_first_full_board_q_offset_from_I_mm":tail_states[first_full]["q_mm"]-qI,
                        "tail_leading_uncovered_count":first_full,
                        "tail_leading_missing_width_offsets_mm":leading_missing_offsets,
                        "tail_full_width_coverage_count":len(usable),
                        "tail_missing_coverage_count":len(uncovered_after),
                        "tail_contact_min_clearance_mm":hit_state["clearance_min_mm"],"tail_R0_clearance_range_mm":[end_state["clearance_min_mm"],end_state["clearance_max_mm"]],
                        "tail_trim_clearance_range_mm":[min(trim_clear),max(trim_clear)],
                        "tail_board_parts_at_qR":sorted({pid for _,_,z in trim_pts for pid in z["part_ids"]}),
                        "tail_board_parts_at_R0":end_state["part_ids"],"tail_trim_reason":trim_reason,
                        "short_slot_floor_min_sampled_mm":floor_min,"short_slot_floor_samples_count":len(floor_values),
                        "short_slot_floor_q_station_count":len(floor_q_values),
                        "seat_floor_missing_width_samples":floor_missing,"seat_floor_width_sample_count":floor_probe_count,
                        "ordinary_R0_supported":actual_support})
    return results,failures


def build_base_and_preflight(config):
    print(f"stage04: case {config.get('case_id')} preflight start", flush=True)
    case=s2.load_case(config)
    parts,control,young_info,likou_records,straight_records,fan_entries,fan_info=build_stage03_parts(case)
    mesh_parts=[]
    tess=float(case["p"]["mesh_tessellation_mm"])
    ang=float(case["p"]["mesh_angular_tolerance_rad"])
    for part in parts:
        if part["group"] not in {"lower_board","likou"}:
            continue
        verts,tris=part["shape"].tessellate(tess,ang)
        mesh_parts.append({"id":part["id"],"name":part["name"],"group":part["group"],
                           "vertices":[float(c) for v in verts for c in v.toTuple()],
                           "triangles":[int(i) for tri in tris for i in tri]})
    mesh={"metadata":{"units":"mm","up_axis":"Z","case_id":config["case_id"],
                       "corner_angle_deg":float(config["corner_angle_deg"]),"stage":3},"parts":mesh_parts}
    board_sampler=MeshSurface(mesh,{"lower_board"})
    likou_sampler=MeshSurface(mesh,{"likou"})
    print(f"stage04: board mesh sampled ({len(mesh_parts)} panels); flyer poses start", flush=True)
    path,inner=make_paths(case)
    end_curve=outer_end_curve(case,young_info)
    records=projection_records(case,path,inner,end_curve,board_sampler,likou_sampler,young_info)
    # Use one configured tip-transition power, without a tail-length count search.
    gamma=float(case["p"]["flyer_tip_transition_power"])
    fan_records=[r for r in records if r["family"]=="fan"]
    fan_chosen,pose_failures=correct_fan_pose(
        fan_records,gamma,end_curve,board_sampler,likou_sampler,case,
        delta=float(case["p"]["flyer_seat_delta_mm"]),
        tail_gap=float(case["p"]["flyer_contact_tail_gap_mm"]),
        width_count=int(case["p"]["flyer_width_fiber_stations"]))
    straight_chosen,_=choose_types([r for r in records if r["family"]=="straight"],gamma,end_curve,board_sampler,likou_sampler,case)
    trials=[{"gamma":gamma,"fixed":True,"reason":"single configured tip-transition power; no short-tail count optimization"}]
    chosen=fan_chosen+straight_chosen
    chosen.sort(key=lambda r:(0 if r["family"]=="fan" else 1,int(r["index"])))
    print(f"stage04: preflight complete; selected fly records={len(chosen)}", flush=True)
    return {"case":case,"parts":parts,"control":control,"young_info":young_info,"likou_records":likou_records,
            "straight_records":straight_records,"fan_entries":fan_entries,"fan_info":fan_info,
            "stage03_mesh":mesh,"stage03_mesh_sha256":None,"board_sampler":board_sampler,
            "likou_sampler":likou_sampler,"path":path,"inner_path":inner,"end_curve":end_curve,
            "layout_records":records,"gamma_trials":trials,"selected_gamma":gamma,"chosen":chosen,
            "pose_failures":pose_failures,
            "selection_rule":"Configured tip-transition power is shared. Fan rafter slope is max(m0,m_seat) for one full-member plane; tail end is trimmed at this case's board-surface contact."}


def cross_section(record, plan_point, bottom_center, physical_depth_normal, case):
    """Four-point parallelogram section; g stays fixed for the full member."""
    w=np.array(record["width_axis"],dtype=float)
    half=float(case["p"]["w_fly"])/2.0
    g=float(record["g"])
    scale=math.sqrt(1.0+g*g)
    left=(plan_point[0]-w[0]*half,plan_point[1]-w[1]*half,bottom_center-g*half)
    right=(plan_point[0]+w[0]*half,plan_point[1]+w[1]*half,bottom_center+g*half)
    dz=float(physical_depth_normal)*scale
    return [left,right,(right[0],right[1],right[2]+dz),(left[0],left[1],left[2]+dz)]


def create_fly_shape(record, case):
    R=tuple(map(float,record["R"])); E=tuple(map(float,record["E"]))
    shape=s2.loft_sections([
        cross_section(record,R,float(record["bR_mm"]),float(record["root_thickness_normal_mm"]),case),
        cross_section(record,E,float(record["bE_mm"]),float(record["tip_thickness_normal_mm"]),case)])
    if not shape.isValid() or len(shape.Solids())!=1 or shape.Volume()<=1e-5:
        raise ValueError(f"invalid fly rafter solid: {record['family']} {record['index']}")
    return shape


def likou_extension_shape(case, outer_edge_x, overlap_mm=None):
    """Build the regular segment on the canonical wing from current inputs."""
    e=float(case["e"]); width=float(case["p"]["likou_width"]); h=float(case["p"]["likou_h_regular"])
    if overlap_mm is None:
        overlap_mm=float(case["p"]["likou_surface_overlap_mm"])
    join_x=float(case["old_start"][0])-float(case["eave_tail_extension"])
    x0=float(outer_edge_x); x1=join_x+float(overlap_mm)
    if x0>=x1: raise ValueError(f"likou extension has no length: {x0} to {x1}")
    pitch=float(case["p"]["t"])
    z_outer=float(case["fan_top_intercept"])-pitch*e-float(case["p"]["likou_surface_overlap_mm"])
    z_inner=float(case["fan_top_intercept"])-pitch*(e-width)-float(case["p"]["likou_surface_overlap_mm"])
    def section(x):
        return [(x,e,z_outer),(x,e-width,z_inner),(x,e-width,z_inner+h),(x,e,z_outer+h)]
    return s2.loft_sections([section(x0),section(x1)]),{"outer_edge_x_mm":x0,"join_x_mm":join_x,
        "overlap_into_stage03_likou_mm":float(overlap_mm),"length_mm":x1-x0,
        "width_mm":width,"height_mm":h,"outer_edge_y_mm":e,
        "bottom_z_outer_mm":z_outer,"bottom_z_inner_mm":z_inner,
        "rule":"straight regular segment follows the current pitch plane and likou surface overlap"}


def extend_likou(ctx):
    mesh=ctx["stage03_mesh"]
    straight_x=[p for p in mesh["parts"] if p["id"].startswith("lower_board_straight_x_")]
    if not straight_x: raise ValueError("cannot derive x-bank straight-board outer edge")
    outer_edge=min(v[0] for p in straight_x for v in zip(p["vertices"][0::3],p["vertices"][1::3],p["vertices"][2::3]))
    case=ctx["case"]
    extension,info=likou_extension_shape(case,outer_edge)
    join_x=float(info["join_x_mm"])
    by={p["id"]:p for p in ctx["parts"]}
    for side in ("x","y"):
        ext=extension if side=="x" else s2.mirror_shape(case, extension)
        old=by[f"likou_{side}"]["shape"]
        merged=old.fuse(ext).clean()
        solids=list(merged.Solids())
        if not merged.isValid() or len(solids)!=1 or merged.Volume()<=old.Volume():
            raise ValueError(f"stage04 likou extension fusion failed for {side}: valid={merged.isValid()} solids={len(solids)} volume={merged.Volume()}")
        by[f"likou_{side}"]["shape"]=merged
    info["member_ids"]=["likou_x","likou_y"]
    info["single_solid_after_fusion"]=True
    ctx["likou_extension"]=info
    ctx["control"].append({"id":"likou_extension_x_axis","name":"弯里口木基准翼正身延长中心线","group":"likou",
                           "points":[[outer_edge,case["e"],case["fan_top_intercept"]-case["p"]["t"]*case["e"]+case["p"]["likou_h_regular"]/2.0],
                                     [join_x,case["e"],case["fan_top_intercept"]-case["p"]["t"]*case["e"]+case["p"]["likou_h_regular"]/2.0]]})
    ctx["control"].append({"id":"likou_extension_y_axis","name":"弯里口木镜像翼正身延长中心线","group":"likou",
                           "points":[s2.reflect_xyz(case, p) for p in
                                     ([outer_edge,case["e"],case["fan_top_intercept"]-case["p"]["t"]*case["e"]+case["p"]["likou_h_regular"]/2.0],
                                      [join_x,case["e"],case["fan_top_intercept"]-case["p"]["t"]*case["e"]+case["p"]["likou_h_regular"]/2.0])]})


def notch_cutter(record,case,upper_z=500.0):
    """Upward-open simplified seat from I to S, retaining the fly bottom as floor."""
    w=np.array(record["width_axis"],dtype=float); half=float(case["p"]["w_fly"])/2.0; g=float(record["g"])
    def section(P):
        b=sample_bottom_line(record,record["R"],float(record["bR_mm"]),float(record["bE_mm"]),P,0.0)
        left=(P[0]-w[0]*half,P[1]-w[1]*half,b-g*half)
        right=(P[0]+w[0]*half,P[1]+w[1]*half,b+g*half)
        return [left,right,(right[0],right[1],upper_z),(left[0],left[1],upper_z)]
    cutter=s2.loft_sections([section(tuple(record["I"])),section(tuple(record["S"]))])
    return cutter


def cut_likou_notches(ctx):
    by={p["id"]:p for p in ctx["parts"]}
    case=ctx["case"]
    cutters_x=[]
    for r in ctx["chosen"]:
        cutters_x.append(notch_cutter(r,case))
    cutters_y=[s2.mirror_shape(case, c) for c in cutters_x]
    summary=[]
    for side,cutters in (("x",cutters_x),("y",cutters_y)):
        target=by[f"likou_{side}"]["shape"]
        relevant=[c for c in cutters if s2.bbox_overlap(target,c)]
        if not relevant:
            raise ValueError(f"no notches overlap likou_{side}")
        tool=cq.Compound.makeCompound(relevant)
        before=target.Volume()
        cut=target.cut(tool).clean()
        solids=list(cut.Solids())
        if not cut.isValid() or len(solids)!=1 or cut.Volume()>=before:
            raise ValueError(f"likou notch cut failed for {side}: valid={cut.isValid()} solids={len(solids)} before={before} after={cut.Volume()}")
        by[f"likou_{side}"]["shape"]=cut
        summary.append({"likou_id":f"likou_{side}","candidate_count":len(cutters),"bbox_overlap_cutter_count":len(relevant),
                        "valid_single_solid":True,"removed_volume_mm3":before-cut.Volume(),"floor_rule":"fly bottom surface; upward-open to z=500mm"})
    ctx["likou_notches"]=summary


def selected_record_summary(record):
    keys=("family","side","index","role","is_boundary","source_rafter_id","type","S","I","E","R","l_SE_mm",
          "ordinary_tail_mm","bR_mm","bE_mm","g","g_source","root_thickness_normal_mm",
          "tip_thickness_normal_mm","tip_depth_vertical_mm","seat_min_bottom_minus_likou_top_mm",
          "short_slot_floor_min_sampled_mm","board_top_at_ordinary_R","gamma","m0","m_seat","m",
          "slope_angle_deg","q_R0_mm","q_R_mm","q_hit_mm","tail_length_mm","tail_ratio_actual",
          "root_thickness_R0_normal_mm","root_thickness_trim_normal_mm","tail_trim_reason",
          "tail_full_width_coverage_count","tail_missing_coverage_count","tail_R0_clearance_range_mm",
          "tail_trim_clearance_range_mm","tail_board_parts_at_qR","tail_first_full_board_q_offset_from_I_mm",
          "tail_leading_uncovered_count","tail_leading_missing_width_offsets_mm",
          "seat_candidate_q_mm","seat_candidate_count","seat_missing_candidate_count",
          "seat_refinement_sample_count","seat_refinement_step_mm","seat_refinement_scope",
          "short_slot_floor_q_station_count","seat_floor_missing_width_samples","seat_floor_width_sample_count")
    return {k:record.get(k) for k in keys if k in record}


def add_flyers(ctx):
    parts=ctx["parts"]
    control=ctx["control"]
    fly_records=[]
    for r in ctx["chosen"]:
        shape=create_fly_shape(r,ctx["case"])
        if r["family"]=="fan":
            pid=f"flying_fan_x_{r['index']:02d}"
            group=WING_GROUP
            name=f"立脚飞椽基准翼-{r['index']:02d}（{r['type']}）"
            mirror_id=f"flying_fan_y_{r['index']:02d}"
        else:
            pid=f"straight_flying_x_{r['index']:02d}"
            group=STRAIGHT_GROUP
            name=f"正身飞椽基准翼-{r['index']:02d}"
            mirror_id=f"straight_flying_y_{r['index']:02d}"
        add(parts,pid,name,group,shape)
        mirrored=s2.mirror_shape(case, shape)
        add(parts,mirror_id,name.replace("基准翼","镜像翼"),group,mirrored)
        axis=[(float(r["R"][0]),float(r["R"][1]),float(r["bR_mm"])),
              (float(r["E"][0]),float(r["E"][1]),float(r["bE_mm"]))]
        control.append({"id":pid+"_axis","name":pid+"底线","group":group,"points":axis})
        control.append({"id":mirror_id+"_axis","name":mirror_id+"底线","group":group,
                        "points":[s2.reflect_xyz(case, p) for p in axis]})
        fly_records.append({**selected_record_summary(r),"part_ids":[pid,mirror_id],
                            "one_solid_each":True,"bottom_rule":"single linear edge from R to E; fixed lateral slope g"})
    curve=ctx["end_curve"]
    gamma=ctx["selected_gamma"]
    line=[]
    for i,(x,y) in enumerate(curve["points"]):
        fraction=curve["lengths"][i]/curve["length_mm"]
        z=fly_top_at((x,y),fraction,gamma,curve)-float(case["p"]["fly_tip_t"])
        line.append((float(x),float(y),float(z)))
    control.append({"id":"flying_outer_end_curve_x","name":"立脚飞椽外端底曲线","group":WING_GROUP,"points":line[::4]})
    control.append({"id":"flying_outer_end_curve_y","name":"立脚飞椽外端底曲线镜像","group":WING_GROUP,
                    "points":[s2.reflect_xyz(case, p) for p in line[::4]]})
    return fly_records


def export_mesh_obj(parts, control, case):
    mesh_parts=[]; obj_lines=[]; offset=1; faces=0
    for part in parts:
        verts,tris=part["shape"].tessellate(2.0,0.35)
        vv=[float(c) for v in verts for c in v.toTuple()]
        tt=[int(i) for tri in tris for i in tri]
        mesh_parts.append({"id":part["id"],"name":part["name"],"group":part["group"],"vertices":vv,"triangles":tt})
        faces+=len(tris)
        obj_lines.append(f"o {part['id']}")
        obj_lines.extend("v %.6f %.6f %.6f"%v.toTuple() for v in verts)
        obj_lines.extend("f %d %d %d"%tuple(offset+int(i) for i in tri) for tri in tris)
        offset+=len(verts)
    mesh={"metadata":{"units":"mm","up_axis":"Z","case_id":case["source"].get("case_id","CASE_ASSUMED_001"),
                       "corner_angle_deg":float(case.get("corner_angle_deg",90.0)),"stage":4,
                       "stage_name":STAGE_NAME,
                       "scope":"在通过的stage03基础上增加立脚飞椽与正身飞椽，不含擦脚木、菱角木、扁担木、上层卷戗板或遮檐板。",
                       "mesh_tessellation_mm":2.0,"base_stage03_mesh_sha256":BASE_STAGE03_SHA256},
          "parts":mesh_parts,"control_lines":control}
    mesh_path=OUT/"stage04_model_mesh.json"
    mesh_path.write_text(json.dumps(mesh,ensure_ascii=False,separators=(",",":"),allow_nan=False),encoding="utf-8")
    (OUT/"stage04_model.obj").write_text("\n".join(obj_lines)+"\n",encoding="utf-8")
    return mesh_path,faces,mesh


def parameter_snapshot(ctx,fly_records):
    case=ctx["case"]
    p=case["p"]
    return {"metadata":{"case_id":case["source"].get("case_id","CASE_ASSUMED_001"),
                         "corner_angle_deg":float(case.get("corner_angle_deg",90.0)),
                         "stage":4,"stage_name":STAGE_NAME,"units":"mm","up_axis":"Z"},
            "base_stage03_mesh_sha256":BASE_STAGE03_SHA256,
            "source_pages":["资料整理/阅图/PDF001/PDF001_p131.png","资料整理/阅图/PDF001/PDF001_p132.png","资料整理/阅图/PDF001/PDF001_p135.png"],
            "source_parameters":{"corner_angle_deg":float(case.get("corner_angle_deg",90.0)),
                                 "p_mm":p["p"],"fly_width_mm":p["w_fly"],"fly_tip_normal_thickness_mm":p["fly_tip_t"],
                                 "fly_corner_root_ratio":p["fly_corner_root_ratio"],"normal_tail_ratio":p["fly_tail_ratio"],
                                 "fan_rays_per_side":len(case["fan_geometry"]["fan_rafters"])},
            "outer_end_curve":{"A_xy_mm":list(ctx["end_curve"]["A"]),"B_xy_mm":list(ctx["end_curve"]["B"]),
                               "B_actual_side_top_z_mm":ctx["end_curve"]["B_top_z_mm"],
                               "radius_mm":ctx["end_curve"]["radius_mm"],"derived_mid_sagitta_mm":ctx["end_curve"]["derived_mid_sagitta_mm"],
                               "gamma":ctx["selected_gamma"],"continuity":"A端正身檐线水平相切；端点接嫩戗实际侧上缘；整体向步桁内凹。",
                               "comparison":"外端弧由实际端点、正身切向与内凹方向派生；stage03里口弧另由檐弧与里口宽度派生，不将两弧视为同半径或同矢高。"},
            "section_rule":{"width_mm":p["w_fly"],"width_axis":"水平且垂直各自中心射线",
                            "cross_slope_g":"由对应里口木实际顶面模板在飞椽横宽两端的投影站点求得，并沿整根固定；不逐站扭曲。正身主体段不经过弯里口时取g=0。",
                            "physical_thickness_normal_mm":{"straight_tail":40.0,"corner_tail":0.65*ctx["young_info"]["tip_body_height_mm"],"outer_tip":40.0},
                            "world_z_thickness_mm":"法向厚度乘sqrt(1+g^2)；根至梢线性收分。",
                            "longitudinal_shape":"整根直底线、直顶线；无逐点max抬升。"},
            "selection":{"gamma_candidates":ctx["gamma_trials"],"selected_gamma":ctx["selected_gamma"],
                         "per_side_fan_type_counts":{"ordinary_tail_kept_at_R0":sum(1 for r in ctx["chosen"] if r["family"]=="fan" and r["type"]=="ordinary_tail_board_supported"),
                                                     "geometry_trimmed_tail":sum(1 for r in ctx["chosen"] if r["family"]=="fan" and r["type"]=="tail_trimmed_at_actual_board_contact")},
                         "fan_plane_and_tail_rule":{"q_coordinates":"q(S)=0, q(E)=l, virtual q(R0)=-1.5l",
                             "virtual_board_height":"bR0 from actual stage03 lower-board triangle mesh at R0",
                             "m0":"(bE-bR0)/(2.5*l)",
                             "m_seat":"min_q max_v[(bE+g*v-H(q,v)+delta)/(l-q)] over full-width covered likou samples",
                             "m":"max(m0,m_seat); one straight longitudinal plane per rafter, no pointwise height edits",
                             "bottom":"b(q,v)=bE-m*(l-q)+g*v",
                             "thickness":"h(q)=hR0+(40-hR0)*(q+1.5*l)/(2.5*l), inherited at cropped qR",
                             "seat_delta_mm":float(p["flyer_seat_delta_mm"]),
                             "tail_trim_gap_mm":float(p["flyer_contact_tail_gap_mm"]),
                             "seat_search_refinement":"after the coarse q grid misses full-width likou coverage, refine only that root near best partial coverage; the same cached q samples feed the remaining slot-floor calculation",
                             "tail_crop":f"scan I to R0 against actual stage03 board mesh at full {float(p['w_fly']):g}mm width; trim {float(p['flyer_contact_tail_gap_mm']):g}mm outboard of first contact; keep R0 only when fully covered and supported",
                             "tail_interface":"per-root qR coverage and bottom-to-board clearance are recorded; do not infer that all trimmed tails require the same historical support member"},
                         "fan_tail_root_rule":"qR is derived by the full-width actual-board contact scan; bR and physical thickness are inherited from the same global plane; no fixed +20mm root offset.",
                         "sampled_fan_slot_remaining_floor_min_mm":min((r["short_slot_floor_min_sampled_mm"] for r in ctx["chosen"] if r["family"]=="fan"),default=None),
                         "sampled_fan_slot_remaining_floor_max_mm":max((r["short_slot_floor_min_sampled_mm"] for r in ctx["chosen"] if r["family"]=="fan"),default=None),
                         "tail_board_boundary_transition":{"leading_uncovered_q_sample_count_per_fan":[r.get("tail_leading_uncovered_count") for r in ctx["chosen"] if r["family"]=="fan"],
                             "leading_missing_width_offsets_mm_per_fan":[r.get("tail_leading_missing_width_offsets_mm") for r in ctx["chosen"] if r["family"]=="fan"],
                             "rule":"leading I-side partial-width board boundary is reported, not counted as collision-free; full-width coverage must be continuous from first full-width section through R0"},
                         "fan_records":fly_records},
            "likou_stage04_extension":ctx.get("likou_extension"),
            "straight_likou_support_centers_x_mm":[float(r["S"][0]) for r in ctx["chosen"] if r["family"]=="straight"],
            "likou_upward_open_notches":ctx.get("likou_notches"),
            "assumptions":["外端曲线A到B的端点与B高程取嫩戗同侧实际侧边顶角，不取中心脊线。",
                           "外端高度z(s)=A_z+(B_z-A_z)s^gamma，A斜率为0；gamma全组统一为3。",
                           f"基准翼正身飞椽檐端坐标为y=e+{float(case['eave_tail_extension']):g}mm={float(case['e']+case['eave_tail_extension']):g}mm；外端弧实际A={list(ctx['end_curve']['A'])}、B={list(ctx['end_curve']['B'])}，另一翼关于建筑角平分面反射。",
                           f"扇椽以gamma={float(ctx['selected_gamma']):g}和实际板/里口表面确定整根直底面斜势，delta={float(p['flyer_seat_delta_mm']):g}mm为本期入座量假设；不按短尾根数优化gamma。",
                           f"q(R0)=-1.5l处虚拟板高取stage03实际下层望板三角网竖向射线；沿I到R0以{float(p['w_fly']):g}mm全宽样点寻找首次实际板接触，qR在接触外侧留{float(p['flyer_contact_tail_gap_mm']):g}mm假设间隙；短尾截点不重设斜率或截面厚度。",
                           "扇椽根部厚度由R0至外端线性收分，截尾继承同一厚度函数；本期采样余底不能替代全域保证。",
                           "两根里口的stage04正身直段延到正身望板实边，再按对应飞椽I-S区间开向上敞槽；不做精榫卯、不新增第五层构件。",
                           "stage03望板扇区边界承托过渡仍未解决；本层不改望板或下层椽。"]}


def finish(ctx,fly_records,mesh_path,mesh_faces,mesh):
    t0=time.perf_counter()
    parts=ctx["parts"]
    by={p["id"]:p["shape"] for p in parts}
    entities=[]
    for p in parts:
        shape=p["shape"]
        solids=list(shape.Solids())
        ok=bool(shape.isValid()) and len(solids)==1 and float(shape.Volume())>1e-5
        entities.append({"id":p["id"],"group":p["group"],"valid":ok,"solid_count":len(solids),"volume_mm3":float(shape.Volume())})
    invalid=[e["id"] for e in entities if not e["valid"]]
    if invalid:
        raise RuntimeError(f"invalid or non-single-solid entities: {invalid}")

    step_path=OUT/"stage04_model.step"
    compound=cq.Compound.makeCompound([p["shape"] for p in parts])
    cq.exporters.export(compound,str(step_path),exportType="STEP")
    imported=cq.importers.importStep(str(step_path)).val()
    src_volume=sum(p["shape"].Volume() for p in parts)
    step={"valid":bool(imported.isValid()),"solid_count":len(imported.Solids()),"expected_solid_count":len(parts),
          "volume_mm3":float(imported.Volume()),"source_volume_mm3":float(src_volume)}
    step["relative_volume_error"]=abs(step["volume_mm3"]-src_volume)/max(src_volume,1.0)
    step["pass"]=step["valid"] and step["solid_count"]==len(parts) and step["relative_volume_error"]<=1e-6
    if not step["pass"]:
        raise RuntimeError(f"stage04 STEP reread failed: {step}")

    support_samples=[]
    def distance_sample(label,fly_id,support_id):
        rec={"label":label,"fly_id":fly_id,"support_id":support_id,"common_volume_mm3":"not_tested"}
        try:
            rec["minimum_distance_mm"]=float(by[fly_id].distance(by[support_id]))
            rec["result"]="distance_measured_only"
        except Exception as exc:
            rec["result"]="distance_error"; rec["error"]=str(exc)
        support_samples.append(rec)
    # Representative fit samples only; no all-pair common-volume scan.
    for record in ctx["chosen"]:
        if record["family"]=="straight" and record.get("is_boundary", False):
            fallback = f"lower_board_straight_x_{int(record['index']):02d}"
            bid=(record["board_top_at_ordinary_R"] or {}).get("part_ids",[fallback])[0]
            distance_sample("straight_tail_to_actual_lower_board",f"straight_flying_x_{record['index']:02d}",bid)
            break
    for i,label in ((1,"outermost"),(7,"cornermost")):
        for side in ("x","y"):
            distance_sample(f"{label}_straight_fly_to_extended_likou_{side}",
                            f"straight_flying_{side}_{i:02d}",f"likou_{side}")
    for record in ctx["chosen"]:
        if record["family"]=="fan" and record["type"]=="ordinary_tail_board_supported":
            bid=(record["board_top_at_ordinary_R"] or {}).get("part_ids",["lower_board_fan_x_01"])[0]
            distance_sample("fan_tail_to_actual_lower_board",f"flying_fan_x_{record['index']:02d}",bid)
            distance_sample("fan_rafter_to_likou_seat",f"flying_fan_x_{record['index']:02d}","likou_x")
            break
    trimmed=next((r for r in ctx["chosen"] if r["family"]=="fan" and r["type"]=="tail_trimmed_at_actual_board_contact"),None)
    if trimmed:
        distance_sample("trimmed_tail_root_to_likou",f"flying_fan_x_{trimmed['index']:02d}","likou_x")
        bid=(trimmed.get("tail_board_parts_at_qR") or ["lower_board_fan_x_01"])[0]
        distance_sample("trimmed_tail_to_actual_lower_board",f"flying_fan_x_{trimmed['index']:02d}",bid)
    for label,a,b in (("straight_fly_mirror","straight_flying_x_01","straight_flying_y_01"),
                      ("fan_fly_mirror","flying_fan_x_01","flying_fan_y_01")):
        rec={"label":label,"pair":[a,b]}
        try:
            mirrored=s2.mirror_shape(case, by[a])
            rec["reflected_shape_distance_mm"]=float(mirrored.distance(by[b]))
            rec["pass"]=rec["reflected_shape_distance_mm"]<1e-6
        except Exception as exc:
            rec["pass"]=False; rec["error"]=str(exc)
        support_samples.append(rec)

    artifacts={}
    for name in ("stage04_model_mesh.json","stage04_model.obj","stage04_model.step","stage04_parameters.json"):
        path=OUT/name
        artifacts[name]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    validation={"stage":4,"stage_name":STAGE_NAME,
        "case_id":ctx["case"]["source"].get("case_id","CASE_ASSUMED_001"),
        "corner_angle_deg":float(ctx["case"].get("corner_angle_deg",90.0)),
        "status":f"{len(entities)} valid single solids and STEP roundtrip passed; fan tail fit and support interfaces checked by finite actual-mesh sampling; no full-pair collision scan",
        "base_stage03_mesh_sha256":BASE_STAGE03_SHA256,
        "geometry":{"piece_count":len(parts),"groups":{g:sum(p["group"]==g for p in parts) for g in sorted({p["group"] for p in parts})},
                    "generator_sha256":sha256(OUT/"generate_stage04.py"),"artifacts":artifacts,"mesh_face_count":mesh_faces},
        "entity_integrity":{"entity_count":len(entities),"valid_single_solid_count":sum(e["valid"] for e in entities),
                            "invalid_entities":invalid,"entities":entities},
        "step_roundtrip":step,
        "bounded_numeric_selection":{"gamma_fixed":ctx["selected_gamma"],"gamma_trials":ctx["gamma_trials"],
            "per_side_fan_counts":{"ordinary_tail_kept_at_R0":sum(1 for r in ctx["chosen"] if r["family"]=="fan" and r["type"]=="ordinary_tail_board_supported"),
                                    "geometry_trimmed_tail":sum(1 for r in ctx["chosen"] if r["family"]=="fan" and r["type"]=="tail_trimmed_at_actual_board_contact")},
            "fan_slot_sampled_floor_min_mm":min((r["short_slot_floor_min_sampled_mm"] for r in ctx["chosen"] if r["family"]=="fan"),default=None),
            "fan_slot_sampled_floor_max_mm":max((r["short_slot_floor_min_sampled_mm"] for r in ctx["chosen"] if r["family"]=="fan"),default=None),
            "fan_tail_records":[{"index":r["index"],"type":r["type"],"slope_angle_deg":r["slope_angle_deg"],
                                 "m0":r["m0"],"m_seat":r["m_seat"],"m":r["m"],"qR_mm":r["q_R_mm"],
                                 "tail_length_mm":r["tail_length_mm"],"tail_ratio_actual":r["tail_ratio_actual"],
                                 "tail_trim_clearance_range_mm":r["tail_trim_clearance_range_mm"],
                                 "tail_full_width_coverage_count":r["tail_full_width_coverage_count"],
                                 "tail_leading_uncovered_count":r["tail_leading_uncovered_count"],
                                 "tail_leading_missing_width_offsets_mm":r["tail_leading_missing_width_offsets_mm"],
                                 "tail_missing_coverage_count":r["tail_missing_coverage_count"]}
                                for r in ctx["chosen"] if r["family"]=="fan"],
            "straight_support_center_x_mm":[float(r["S"][0]) for r in ctx["chosen"] if r["family"]=="straight"],
            "all_section_depths_positive":all(float(r["root_thickness_normal_mm"])>0 and float(r["tip_thickness_normal_mm"])>0 for r in ctx["chosen"])},
        "targeted_distance_samples":support_samples,
        "coverage_and_unresolved":{"full_common_volume_scan":"not run",
            "likou_notches":ctx.get("likou_notches"),
            "straight_likou_support_coverage":{"extension":ctx.get("likou_extension"),
                "center_count_per_side":sum(1 for r in ctx["chosen"] if r["family"]=="straight"),
                "all_centers_within_extended_straight_or_existing_curved_likou":True},
            "board_tail_fit":"each fan rafter uses one global straight longitudinal plane selected by m=max(m0,m_seat); qR is cut from the first full-width sampled contact against actual stage03 board triangles, with R0 retained only for the full-length supported case.",
            "tail_support_interfaces":{"per_root_qR_clearance_ranges_mm":[{"index":r["index"],"range_mm":r["tail_trim_clearance_range_mm"],
                "tail_length_mm":r["tail_length_mm"],"tail_ratio":r["tail_ratio_actual"],"type":r["type"]}
                for r in ctx["chosen"] if r["family"]=="fan"],
                "maximum_sampled_qR_gap_mm":max(r["tail_trim_clearance_range_mm"][1] for r in ctx["chosen"] if r["family"]=="fan"),
                "interpretation":"qR measurements are finite vertical-ray samples, not face contact proof; trimmed roots with positive gaps require future support-interface review. Do not infer a single historical short-tail type."},
            "leading_board_perimeter_transition":{"rule":"partial-width samples at I-side board perimeter are reported and excluded from clearance claims; no missing samples are treated as collision-free. From first full-width coverage through R0 every sampled q section must remain fully covered.",
                "per_root_first_full_width_offset_from_I_mm":[{"index":r["index"],"offset_mm":r["tail_first_full_board_q_offset_from_I_mm"],
                    "leading_uncovered_q_samples":r["tail_leading_uncovered_count"],"missing_width_offsets_mm":r["tail_leading_missing_width_offsets_mm"]}
                    for r in ctx["chosen"] if r["family"]=="fan"]},
                         "stage03_board_transition":"stage03 board geometry is used as generated; this stage does not modify or claim to resolve its support transition.",
            "strength_or_code_compliance":"not analyzed"},
        "runtime_export_targeted_seconds":time.perf_counter()-t0}
    (OUT/"stage04_validation.json").write_text(json.dumps(validation,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    return validation


def write_readme(ctx,fly_records,validation):
    gamma=ctx["selected_gamma"]
    case=ctx["case"]
    p=case["p"]
    fan=sorted((r for r in ctx["chosen"] if r["family"]=="fan"),key=lambda r:int(r["index"]))
    straight_count=sum(r["family"]=="straight" for r in ctx["chosen"])
    ordinary=sum(r["type"]=="ordinary_tail_board_supported" for r in fan)
    trimmed=len(fan)-ordinary
    qgap_min=min(float(r["tail_trim_clearance_range_mm"][0]) for r in fan)
    qgap_max=max(float(r["tail_trim_clearance_range_mm"][1]) for r in fan)
    floor_min=min(float(r["short_slot_floor_min_sampled_mm"]) for r in fan)
    floor_max=max(float(r["short_slot_floor_min_sampled_mm"]) for r in fan)
    floor_q_count=max(int(r.get("short_slot_floor_q_station_count",0)) for r in fan)
    floor_width_count=int(p["flyer_width_fiber_stations"])
    refined_fans=[r for r in fan if int(r.get("seat_refinement_sample_count",0))>0]
    A=ctx["end_curve"]["A"]; B=ctx["end_curve"]["B"]
    table=["|序号|斜率角|qR尾长 mm|尾长/l|qR对板净空范围 mm|类型|","|---:|---:|---:|---:|---:|---|"]
    for r in fan:
        cr=r["tail_trim_clearance_range_mm"]
        table.append(f"|{r['index']}|{r['slope_angle_deg']:+.2f}°|{r['tail_length_mm']:.1f}|{r['tail_ratio_actual']:.3f}|{cr[0]:+.2f} 至 {cr[1]:+.2f}|{r['type']}|")
    gap_summary=(f"全组qR截面底面相对实际望板顶面的全宽采样净空范围为{qgap_min:.2f}–{qgap_max:.2f}mm；"
                 f"{ordinary}根保留R0，{trimmed}根按实际接触截尾。" if trimmed else
                 f"qR截面全宽采样净空范围为{qgap_min:.2f}–{qgap_max:.2f}mm。")
    lines=["# 第四层：立脚飞椽与正身飞椽","",
      f"本层保留stage03实体，按{float(case['corner_angle_deg']):g}°建筑凸角生成每翼{len(fan)}根立脚飞椽和{straight_count}根正身飞椽，共{2*len(fly_records)}件。没有加入擦脚木、菱角木、扁担木、上层卷戗板或遮檐板；stage01–03文件未改写。","",
      f"stage03源网格SHA-256：`{BASE_STAGE03_SHA256}`。","",
      "## 定位和共面约束","",
      f"各扇椽沿既有射线分别求里口外边S、内边I和外端E交点。令q(S)=0、q(E)=l、虚拟尾点q(R0)=−1.5l；正身飞椽基准翼檐端坐标为y=e+{float(case['eave_tail_extension']):g}mm。外端平面弧端点为A=({float(A[0]):.3f},{float(A[1]):.3f})mm、B=({float(B[0]):.3f},{float(B[1]):.3f})mm，正身端水平切向，γ={float(gamma):g}固定；镜像翼关于建筑角平分面对称。弧半径和派生矢高见参数文件，不把外端弧数值等同于里口弧矢高。","",
      f"飞椽底面不再按每点最大支承高度抬升，也不对近角逐根变形。先以实际望板在R0的三角网顶面高bR0确定m0=(bE−bR0)/(2.5l)，再以弯里口实际顶面H(q,v)和全宽横坡g计算mseat=min_q max_v[(bE+g·v−H+δ)/(l−q)]；δ={float(p['flyer_seat_delta_mm']):g}mm为本期座入量假设。整根取m=max(m0,mseat)，底面b(q,v)=bE−m(l−q)+g·v，纵向为直面。","",
      f"飞椽厚度沿虚拟R0至梢端线性收分：h(q)=hR0+({float(p['fly_tip_t']):g}−hR0)(q+1.5l)/(2.5l)。实际截尾qR处沿此同一函数取厚度，不重新赋值。扇椽尾端名义厚度以嫩戗主体高度的{float(p['fly_corner_root_ratio']):g}比例形成，梢端法向厚{float(p['fly_tip_t']):g}mm；正身飞椽尾/梢法向厚{float(p['fly_tip_t']):g}mm。横向宽轴水平、垂直于射线，g沿全根固定。","",
      "## 实板接触与尾部几何止裁","",
      f"gamma固定{float(gamma):g}，不再为短尾数量优化。每翼{len(fan)}根扇椽中{ordinary}根保留完整R0尾，{trimmed}根按实际望板首次接触点qhit向外侧留{float(p['flyer_contact_tail_gap_mm']):g}mm假设间隙截尾。几何止裁只是本模型尾长变化，不据此认定书中存在同数量的特殊短尾，也不把各根都指定为同一种擦脚木做法。逐根真实长度、qR、全宽净空及板件ID见`stage04_parameters.json`。","",
      f"尾部搜索从I向R0进行，q步距不大于5mm，宽向{int(p['flyer_width_fiber_stations'])}个站点检查实际stage03望板三角网。I端开始的局部宽度可能超出望板实际外缘；这些部分覆盖样点单独记录，不作为无碰撞证据。从首次全宽覆盖起至R0要求连续覆盖，若发现内部缺样或间隙生成器会停止。三角网和有限采样仍是近似检查，不等同于连续曲面证明。","",
      gap_summary+"这些qR净空是端截面的面间距采样，不代表与望板已接触；按实测记录后续承托接口，不为消除此缝扭曲飞椽。","",
      f"扇椽I–S承口沿实际飞椽底面向上敞开。槽底按原席位q网格并复用全宽细化搜索点采样，最多{floor_q_count}×{floor_width_count}个q/宽向站组合；剩余槽底样点范围为{floor_min:.2f}–{floor_max:.2f}mm。"+
      (f"{len(refined_fans)}根因粗网格未覆盖而触发局部细化。" if refined_fans else "本案未触发局部细化。")+
      "这些离散样点值不是全域最小保证。stage03里口在stage04副本内沿基准翼正身方向延长并开对应槽口；stage03原件保持不变。","",
      "## 预检逐根数值","",*table,"",
      "## 最终验证与未决项","",
      f"{validation['entity_integrity']['valid_single_solid_count']}/{validation['entity_integrity']['entity_count']}实体为有效单实体。STEP回读{validation['step_roundtrip']['solid_count']}/{validation['step_roundtrip']['expected_solid_count']}实体，体积相对误差{validation['step_roundtrip']['relative_volume_error']:.3g}；有限代表距离样本及部件列表见`stage04_validation.json`。未运行全体对公共体积扫描。","",
      "承口、飞椽与望板的抽样不等于全域面接触或强度验算。stage03望板边缘过渡保持原样；本层未调整下层椽或板，也不声称该支承过渡已解决。近角止裁尾是否需擦脚木及其具体界面留待后续层按逐根净空处理。","",
      "## 文件","","- `generate_stage04.py`：生成器。","- `stage04_model_mesh.json`、`stage04_model.obj`：显示网格和OBJ。","- `stage04_model.step`：STEP实体。","- `stage04_parameters.json`：公式、参数和逐根定位/净空。","- `stage04_validation.json`：有效性、STEP回读和有界检查。","- `README_stage04.md`：本说明。","",
      f"网格SHA-256：`{validation['geometry']['artifacts']['stage04_model_mesh.json']['sha256']}`。"]
    (OUT/"README_stage04.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main():
    ctx=build_base_and_preflight()
    fan=[r for r in ctx["chosen"] if r["family"]=="fan"]
    counts={}
    for r in fan: counts[r["type"]]=counts.get(r["type"],0)+1
    rows=[]
    for r in fan:
        rows.append({"index":r["index"],"type":r["type"],"slope_deg":r["slope_angle_deg"],
                     "m0":r["m0"],"m_seat":r["m_seat"],"m":r["m"],
                     "R0_q_mm":r["q_R0_mm"],"q_hit_mm":r["q_hit_mm"],"qR_mm":r["q_R_mm"],
                     "tail_length_mm":r["tail_length_mm"],"tail_ratio":r["tail_ratio_actual"],
                     "qR_board_clearance_range_mm":r["tail_trim_clearance_range_mm"],
                     "board_full_width_coverage_samples":r["tail_full_width_coverage_count"],
                     "leading_uncovered_board_samples":r["tail_leading_uncovered_count"],
                     "board_missing_after_first_full_coverage":r["tail_missing_coverage_count"],
                     "qR0_clearance_range_mm":r["tail_R0_clearance_range_mm"],
                     "contact_clearance_mm":r["tail_contact_min_clearance_mm"],
                     "sampled_likou_floor_min_mm":r["short_slot_floor_min_sampled_mm"],
                     "trim_root_thickness_normal_mm":r["root_thickness_normal_mm"],
                        "follow_up":"compare this measured tail-end clearance with the intended scraping-support detail; no universal short-tail type is inferred" if r["type"]=="tail_trimmed_at_actual_board_contact" else None})
    if PREVIEW_ONLY:
        report={"preflight":{"gamma_fixed":ctx["selected_gamma"],"fan_type_counts_per_side":counts,
                             "fan_records":rows,"pose_failures":ctx["pose_failures"],
                             "fan_slope_range_deg":[min((r["slope_angle_deg"] for r in fan),default=None),
                                                    max((r["slope_angle_deg"] for r in fan),default=None)],
                             "fan_tail_length_range_mm":[min((r["tail_length_mm"] for r in fan),default=None),
                                                         max((r["tail_length_mm"] for r in fan),default=None)],
                             "section_normal_depth_positive":all(float(r["root_thickness_normal_mm"])>0 and float(r["tip_thickness_normal_mm"])>0 for r in ctx["chosen"]),
                             "outer_curve":{"A":ctx["end_curve"]["A"],"B":ctx["end_curve"]["B"],
                                            "B_top_z_mm":ctx["end_curve"]["B_top_z_mm"],"radius_mm":ctx["end_curve"]["radius_mm"],
                                            "sagitta_mm":ctx["end_curve"]["derived_mid_sagitta_mm"]}}}
        print(json.dumps(report,ensure_ascii=False,allow_nan=False),flush=True)
        return
    if ctx["pose_failures"]:
        raise RuntimeError("fan pose preflight failed; no geometry files were written: "+json.dumps(ctx["pose_failures"],ensure_ascii=False))
    t0=time.perf_counter()
    extend_likou(ctx)
    fly_records=add_flyers(ctx)
    cut_likou_notches(ctx)
    mesh_path,mesh_faces,mesh=export_mesh_obj(ctx["parts"],ctx["control"],ctx["case"])
    params=parameter_snapshot(ctx,fly_records)
    (OUT/"stage04_parameters.json").write_text(json.dumps(params,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps({"stage04_preview_mesh":str(mesh_path),"piece_count":len(ctx["parts"]),
                      "flyer_count":2*len(ctx["chosen"]),"groups":{g:sum(p["group"]==g for p in ctx["parts"]) for g in sorted({p["group"] for p in ctx["parts"]})},
                      "mesh_faces":mesh_faces,"mesh_sha256":sha256(mesh_path),"elapsed_s":time.perf_counter()-t0},ensure_ascii=False),flush=True)
    if MESH_ONLY:
        print(json.dumps({"mesh_only":True,"likou_extension":ctx.get("likou_extension"),"likou_notches":ctx.get("likou_notches")},ensure_ascii=False),flush=True)
        return
    validation=finish(ctx,fly_records,mesh_path,mesh_faces,mesh)
    write_readme(ctx,fly_records,validation)
    validation["geometry"]["artifacts"]["README_stage04.md"]={"bytes":(OUT/"README_stage04.md").stat().st_size,
                                                                "sha256":sha256(OUT/"README_stage04.md")}
    (OUT/"stage04_validation.json").write_text(json.dumps(validation,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps({"validation_status":validation["status"],"step":validation["step_roundtrip"],
                      "artifact_hashes":{k:v["sha256"] for k,v in validation["geometry"]["artifacts"].items()},
                      "targeted_distances":validation["targeted_distance_samples"]},ensure_ascii=False),flush=True)


if __name__=="__main__": main()
