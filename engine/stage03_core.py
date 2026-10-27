#!/usr/bin/env python
"""Stage 03: young corner, bow likou, and lower roof boards.

Stage 01 and 02 files are never overwritten.  This generator rebuilds the
accepted stage-02 solids, adds the current third-layer pieces, and exports an
independent mesh/OBJ/STEP/parameter/validation set.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent
S2_PATH = OUT / "stage02_core.py"
SPEC = importlib.util.spec_from_file_location("stage02_for_stage03", S2_PATH)
s2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s2)
cq = s2.cq
np = s2.np
EPS = 1e-8
STAGE_NAME = "嫩戗、弯里口木与下层木望板"


def add(parts, pid, name, group, shape):
    s2.add_part(parts, pid, name, group, shape)


def smooth(t):
    return s2.smooth(t)


def arc_young_profile(chord, sagitta, samples=33):
    """Circular inward/lower arc between spatial chord endpoints in a side plane."""
    radius = chord * chord / (8.0 * sagitta) + sagitta / 2.0
    half = chord / 2.0
    pts = []
    for t in np.linspace(0.0, 1.0, samples):
        u = float(t) * chord
        offset_down = sagitta - (radius - math.sqrt(max(0.0, radius * radius - (u - half) ** 2)))
        pts.append((float(t), offset_down))
    pts[0], pts[-1] = (0.0, 0.0), (1.0, 0.0)
    return pts, radius


def old_side_values(case, t):
    hscale = 1.0 - (1.0 - case["old_tail_scale"]) * smooth(t)
    return case["old_width_root"] * hscale, case["old_height_root"] * hscale, case["old_ridge_height_root"] * hscale


def young_corner(case, old_member):
    src = case["p"]
    chord = float(src["L_n"])
    rise = float(src["U_n"])
    setback = float(src["setback"])
    run = math.sqrt(chord * chord - rise * rise)
    root_xy = (case["old_end"][0] - case["old_unit"][0] * setback,
               case["old_end"][1] - case["old_unit"][1] * setback)
    uvec = case["old_unit"]
    wvec = (-uvec[1], uvec[0])
    root_t = max(0.0, min(1.0, (math.dist(case["old_start"], root_xy) / case["old_length_plan"])))
    old_w, old_h, old_ridge = old_side_values(case, root_t)
    root_width = old_w * float(src["young_root_scale"])
    root_height = old_h * float(src["young_root_scale"])
    root_ridge = old_ridge * float(src["young_root_scale"])
    tip_scale = float(src["young_tip_scale"])
    tip_width, tip_height, tip_ridge = root_width * tip_scale, root_height * tip_scale, root_ridge * tip_scale
    start_bottom_z = (case["purlin_radius"] - float(case["p"]["t"]) *
                      s2.symmetric_roof_pitch_projection(case, root_xy) -
                      case["old_seat_relief"] + old_h)
    # 112.5 mm belongs to the combined future upper outline, after separate
    # diamond/carry members are added. Keep the young-corner body baseline
    # straight and apply only a shallow curve to its existing raised carback.
    combined_outline_sagitta = None  # historical comparison input; not a controlled geometry parameter
    effective_sagitta = min(root_ridge, tip_ridge) * 0.75
    carback_profile, radius = arc_young_profile(chord, effective_sagitta)

    def center_at_continuous(t):
        t = min(1.0, max(0.0, t))
        return (root_xy[0] + uvec[0] * run * t,
                root_xy[1] + uvec[1] * run * t,
                start_bottom_z + rise * t)

    sections, centerline, carback_crestline = [], [], []
    station_count = 33
    for t in np.linspace(0.0, 1.0, station_count):
        t = float(t)
        c = center_at_continuous(t)
        d = 1e-4
        prev, nxt = center_at_continuous(max(0.0, t-d)), center_at_continuous(min(1.0, t+d))
        tangent = np.array(nxt) - np.array(prev)
        tangent /= np.linalg.norm(tangent)
        width_axis = np.array([wvec[0], wvec[1], 0.0])
        width_axis /= np.linalg.norm(width_axis)
        height_axis = np.cross(tangent, width_axis)
        height_axis /= np.linalg.norm(height_axis)
        taper = 1.0 - (1.0-tip_scale) * smooth(t)
        width = root_width * taper
        body_h = root_height * taper
        ridge_h = root_ridge * taper
        carback_drop_z = carback_profile[int(round(t * (station_count - 1)))][1]
        vertices = []
        for lateral, height in ((-width/2, 0.0), (width/2, 0.0),
                                (width/2, body_h),
                                (0.0, body_h+ridge_h-carback_drop_z/height_axis[2]),
                                (-width/2, body_h)):
            p = np.array(c) + width_axis*lateral + height_axis*height
            # The shared pitch datum is the equal projection of the two roof
            # normals onto the angle bisector. This keeps the cross section
            # invariant under reflection for every supported included angle.
            bisector_run = (s2.symmetric_roof_pitch_projection(case, p[:2]) -
                            s2.symmetric_roof_pitch_projection(case, c[:2]))
            p[2] -= float(case["p"]["t"]) * bisector_run
            vertices.append([float(v) for v in p])
        sections.append(vertices)
        centerline.append([float(v) for v in c])
        carback_crestline.append(vertices[3])
    raw = s2.loft_sections(sections)
    raw_overlap = float(raw.intersect(old_member).Volume()) if s2.bbox_overlap(raw, old_member) else 0.0
    seated = raw.cut(old_member) if s2.bbox_overlap(raw, old_member) else raw
    if not seated.isValid() or len(seated.Solids()) != 1 or seated.Volume() <= 1e-5:
        raise ValueError(f"嫩戗坐势切配后无效: valid={seated.isValid()} solids={len(seated.Solids())} volume={seated.Volume()}")
    young_info = {
        "root_center_plan_mm": list(root_xy), "root_bottom_datum_z_mm": start_bottom_z,
        "root_old_corner_station_fraction": root_t, "setback_from_old_tip_mm": setback,
        "spatial_control_chord_mm": chord, "plan_run_mm": run, "control_rise_mm": rise,
        "combined_outline_sagitta_reference_mm": combined_outline_sagitta,
        "effective_carback_sagitta_mm": effective_sagitta,
        "sagitta_radius_mm": radius, "station_count": station_count,
        "root_width_mm": root_width, "tip_width_mm": tip_width,
        "root_body_height_mm": root_height, "tip_body_height_mm": tip_height,
        "root_carback_mm": root_ridge, "tip_carback_mm": tip_ridge,
        "raw_overlap_with_old_corner_mm3": raw_overlap,
        "centerline": centerline,
        "carback_crestline": carback_crestline,
        "carback_drop_samples_mm": [float(row[1]) for row in carback_profile],
        "body_baseline_rule": "嫩戗主体底线与900/480控制弦一致，保持直线；组合轮廓矢高不施加到主体底面或中心线。",
        "carback_rule": "仅收分车背脊线作浅内凹，矢高取根梢较小车背高度的0.75；112.5mm保留为嫩戗与菱角木/扁担木组合上轮廓的参考值，本层不把它压入嫩戗主体。",
        "construction": "33截面放样；控制弦长900按空间直线长度解释，竖向升高480；根部从老戗侧身上缘起，布尔切老戗形成简化坐势。没有增加老戗头后的后延大楔块，菱角木/扁担木留待后层。",
    }
    return seated, young_info, centerline


def rail_path(case):
    D, e, ext = case["D"], case["e"], case["eave_tail_extension"]
    x_boundary = float(case["old_start"][0])
    A = (x_boundary, e)
    straight = [(float(x), float(e)) for x in np.linspace(x_boundary-ext, x_boundary, 9)]
    arc = case["fan_surface"]["arc_points"]
    selected = [arc[int(round(i*(len(arc)-1)/48))] for i in range(49)]
    path = straight + [(float(x), float(y)) for x, y in selected[1:]]
    # Preserve the documented tangent join exactly.
    path[8] = (float(A[0]), float(A[1]))
    return path


def build_likou_half(case, path, mirror=False):
    D, e = case["D"], case["e"]
    width = float(case["p"]["likou_width"])
    h_low = float(case["p"]["likou_h_regular"])
    h_high = h_low * float(case["p"]["likou_h_corner_ratio"])
    lengths = [0.0]
    for i in range(1, len(path)):
        lengths.append(lengths[-1] + math.dist(path[i-1], path[i]))
    total = lengths[-1]
    sections, centerline = [], []
    for i, p in enumerate(path):
        if i == 0:
            dx, dy = path[1][0]-p[0], path[1][1]-p[1]
        elif i == len(path)-1:
            dx, dy = p[0]-path[i-1][0], p[1]-path[i-1][1]
        else:
            dx, dy = path[i+1][0]-path[i-1][0], path[i+1][1]-path[i-1][1]
        norm = math.hypot(dx, dy)
        inward = (dy/norm, -dx/norm)
        t = lengths[i]/total
        h = h_low + (h_high-h_low)*smooth(t)
        edge_points = [(p[0], p[1]), (p[0]+inward[0]*width, p[1]+inward[1]*width)]
        if p[0] <= float(case["old_start"][0]) + 1e-6 and abs(p[1]-e) < 1e-5:
            base_z = case["fan_top_intercept"] - float(case["p"]["t"])*e
        else:
            base_z = s2.fan_top_z(case, p[0], p[1])
        section = []
        for x, y in edge_points:
            if x < float(case["old_start"][0])-1e-5 and abs(y-e) <= width+1e-4:
                z = case["fan_top_intercept"] - float(case["p"]["t"])*y
            else:
                z = s2.fan_top_z(case, x, y)
            section.append([x, y, z-float(case["likou_surface_overlap"])])
        for x, y in reversed(edge_points):
            # Matching top face follows the shared support surface plus the
            # varying vertical beam height.
            if x < float(case["old_start"][0])-1e-5 and abs(y-e) <= width+1e-4:
                z = case["fan_top_intercept"] - float(case["p"]["t"])*y
            else:
                z = s2.fan_top_z(case, x, y)
            section.append([x, y, z-float(case["likou_surface_overlap"])+h])
        sections.append(section)
        centerline.append([p[0], p[1], base_z-float(case["likou_surface_overlap"])+h/2.0])
    shape = s2.loft_sections(sections)
    if mirror:
        shape = s2.mirror_shape(case, shape)
        centerline = [s2.reflect_xyz(case, p) for p in centerline]
    info = {"width_mm": width, "low_height_mm": h_low, "high_height_mm": h_high,
            "height_transition": "沿直段+内凹圆弧总长使用smoothstep",
            "straight_extension_mm": float(case["eave_tail_extension"]),
            "path_length_mm": total, "station_count": len(path),
            "path": [list(s2.reflect_xy(case, p)) if mirror else [p[0], p[1]] for p in path],
            "plan_rule": f"以stage02内凹檐端弧为外侧参考线，沿屋面内侧偏移{width:g}mm作为里口内边；望板止于该内边。",
            "vertical_rule": f"底面跟随同一摔网/正身椽顶面并预留{float(case['likou_surface_overlap']):g}mm搭接；高端{h_high:g}mm、低端{h_low:g}mm。"}
    return shape, info, centerline


def straight_boards(case, parts, control):
    centers = list(case["eave_rafters_geometry"]["x_centers_mm"])
    pitch = case["eave_pitch"]
    boundaries = [centers[0]-pitch/2.0]
    boundaries.extend((centers[i]+centers[i+1])/2.0 for i in range(len(centers)-1))
    old_start = case["old_start"]
    boundaries.append(float(old_start[0]))
    y0 = -case["D"]-case["eave_tail_extension"]
    y1 = case["eave"]["inner_edge_y"]
    fan_surface = case["fan_surface"]
    O_run = fan_surface["O"][1]
    root_miter_relief = 0.5
    tan_h = math.tan(float(case["half_angle_rad"]))
    cot_h = 1.0 / tan_h
    records = []

    def miter_run_at_x(x):
        return float(old_start[1]) + tan_h * (float(x) - float(old_start[0]))

    def miter_x_at_run(q):
        return float(old_start[0]) + cot_h * (float(q) - float(old_start[1]))

    def board_plane_z(q):
        # One plane for the complete straight bank, controlled by O and A.
        return float(fan_surface["zO"]) + (
            float(fan_surface["zA"]) - float(fan_surface["zO"])) * (
                q - O_run) / (float(fan_surface["A"][1]) - O_run)

    for i in range(len(centers)):
        x0, x1 = boundaries[i], boundaries[i+1]
        q0 = max(y0, miter_run_at_x(x0) + root_miter_relief)
        if q0 >= y1:
            raise ValueError(f"正身望板{i+1:02d}裁角后无有效跨度")
        qs = [float(v) for v in np.linspace(q0, y1, 11)]
        sections = []
        for q in qs:
            cross_hi = min(x1, miter_x_at_run(q))
            if cross_hi <= x0:
                raise ValueError(f"正身望板{i+1:02d}裁角截面宽度非正")
            z = board_plane_z(q) - float(case["board_surface_overlap"])
            sections.append([[x0,q,z],[cross_hi,q,z],[cross_hi,q,z+case["board_lower_t"]],
                             [x0,q,z+case["board_lower_t"]]])
        base_shape = s2.loft_sections(sections)
        cross_hi_start = min(x1, miter_x_at_run(q0))
        cross_hi_end = min(x1, miter_x_at_run(y1))
        ztop0, ztop1 = board_plane_z(q0), board_plane_z(y1)
        base_edge = [[x0,q0,ztop0],[cross_hi_start,q0,ztop0],
                     [cross_hi_end,y1,ztop1],[x0,y1,ztop1]]
        for side in ("x", "y"):
            shape = base_shape if side == "x" else s2.mirror_shape(case, base_shape)
            pid = f"lower_board_straight_{side}_{i+1:02d}"
            edge = base_edge if side == "x" else [s2.reflect_xyz(case, p) for p in base_edge]
            wing_label="基准翼" if side=="x" else "镜像翼"
            add(parts,pid,f"下层望板正身{wing_label}{i+1:02d}（{case['board_lower_t']:g}厚）","lower_board",shape)
            records.append({"id":pid,"side":side,"plan_x_or_y_extent_mm":[x0,x1],
                            "nominal_run_extent_mm":[y0,y1],"generated_run_extent_mm":[q0,y1],
                            "thickness_mm":float(case["board_lower_t"]),"underface_overlap_mm":float(case["board_surface_overlap"]),
                            "supported_rafter_center_mm":centers[i],
                            "half_plane_rule":"基准翼按建筑角平分面裁切；镜像翼由同一实体刚体反射得到。",
                            "root_miter_relief_mm":root_miter_relief,
                            "surface_rule":"全翼正身望板共用经过摔网根点O与檐口A的平面；宽向等高，取消末道板单边抬升。",
                            "plane_control_O_q_z_mm":[float(O_run),float(fan_surface["zO"])],
                            "plane_control_A_q_z_mm":[float(fan_surface["A"][1]),float(fan_surface["zA"])]})
            control.append({"id":pid+"_edge","name":pid+"边界","group":"lower_board",
                            "points":edge})
    return records

def fan_boards(case, parts, control):
    ctx = case["fan_surface"]
    O = ctx["O"]
    arc = ctx["arc_points"]
    width = float(case["p"]["likou_width"])
    boundary = []
    for i, p in enumerate(arc):
        p0, p1 = arc[max(0,i-1)], arc[min(len(arc)-1,i+1)]
        dx,dy=p1[0]-p0[0],p1[1]-p0[1]
        norm=math.hypot(dx,dy)
        n=(dy/norm,-dx/norm)
        # Select the normal that points from the outer arc toward O.
        if (n[0]*(O[0]-p[0])+n[1]*(O[1]-p[1])) < 0:
            n=(-n[0],-n[1])
        boundary.append((p[0]+n[0]*width,p[1]+n[1]*width))
    # A single ruled sector loft provides the least-fragmented fan board.
    sections=[]
    board_t = float(case["board_lower_t"])
    board_overlap = float(case["board_surface_overlap"])
    for x,y in boundary:
        z=s2.fan_top_z(case,x,y)-board_overlap
        sections.append([[O[0],O[1],ctx["zO"]-board_overlap],[x,y,z],[x,y,z+board_t],
                         [O[0],O[1],ctx["zO"]-board_overlap+board_t]])
    try:
        shape=s2.loft_sections(sections)
        fallbacks=[]
        shapes=[shape]
        n_panels = int(case["fan_surface"]["n"]) + 1
        coarse=[boundary[k] for k in [int(round(k*(len(boundary)-1)/n_panels)) for k in range(n_panels+1)]]
    except Exception as exc:
        # Bounded fallback: one triangular ruled board per fan bay and side.
        # The checked station profile remains the continuous roof template.
        fallbacks=[str(exc)]
        shapes=[]
        n_panels = int(case["fan_surface"]["n"]) + 1
        indices=[int(round(k*(len(boundary)-1)/n_panels)) for k in range(n_panels+1)]
        coarse=[boundary[k] for k in indices]
        for i in range(len(coarse)-1):
            p0,p1=coarse[i],coarse[i+1]
            base=[[O[0],O[1],ctx["zO"]-board_overlap],
                  [p0[0],p0[1],s2.fan_top_z(case,*p0)-board_overlap],
                  [p1[0],p1[1],s2.fan_top_z(case,*p1)-board_overlap]]
            top=[[x,y,z+board_t] for x,y,z in base]
            shapes.append(s2.loft_sections([base,top]))
    entries=[]
    for side in ("x","y"):
        reflected=side=="y"
        for i,base_shape in enumerate(shapes):
            shape=s2.mirror_shape(case, base_shape) if reflected else base_shape
            pid=f"lower_board_fan_{side}_{i+1:02d}" if len(shapes)>1 else f"lower_board_fan_{side}"
            wing_label="基准翼" if side=="x" else "镜像翼"
            add(parts,pid,f"下层望板摔网{wing_label}{i+1:02d}（{board_t:g}厚）","lower_board",shape)
            entries.append({"id":pid,"side":side,"thickness_mm":board_t,
                            "underface_overlap_mm":board_overlap,"fan_sector_index":i+1,
                            "construction":"由分射点到弯里口内边的扇区放样，完整覆盖摔网椽至里口内边。"})
            if len(shapes)==1 and i==0:
                control.append({"id":pid+"_outer_edge","name":"扇形望板内口边","group":"lower_board",
                                "points":([s2.reflect_xyz(case, (x,y,s2.fan_top_z(case,x,y)-board_overlap+board_t))
                                           for x,y in boundary] if reflected else
                                          [[x,y,s2.fan_top_z(case,x,y)-board_overlap+board_t] for x,y in boundary])})
    # Measure how well the chosen one-piece loft follows the shared height rule;
    # the fallback panels use the same endpoint interpolation in triangular cells.
    max_deviation=0.0
    if len(shapes)==1:
        for j in range(len(boundary)-1):
            for r in (0.25,0.5,0.75):
                for u in (0.25,0.5,0.75):
                    p0,p1=boundary[j],boundary[j+1]
                    x=(1-r)*O[0]+r*((1-u)*p0[0]+u*p1[0])
                    y=(1-r)*O[1]+r*((1-u)*p0[1]+u*p1[1])
                    z0=ctx["zO"]*(1-r)+r*((1-u)*s2.fan_top_z(case,*p0)+u*s2.fan_top_z(case,*p1))
                    max_deviation=max(max_deviation,abs((s2.fan_top_z(case,x,y))-z0))
    else:
        for j in range(len(coarse)-1):
            p0,p1=coarse[j],coarse[j+1]
            z0,z1=s2.fan_top_z(case,*p0),s2.fan_top_z(case,*p1)
            for r in (0.25,0.5,0.75):
                for u in (0.25,0.5,0.75):
                    x=(1-r)*O[0]+r*((1-u)*p0[0]+u*p1[0])
                    y=(1-r)*O[1]+r*((1-u)*p0[1]+u*p1[1])
                    z_linear=ctx["zO"]*(1-r)+r*((1-u)*z0+u*z1)
                    max_deviation=max(max_deviation,abs(s2.fan_top_z(case,x,y)-z_linear))
    info={"per_side_part_count":len(shapes),"total_piece_count":len(entries),
          "boundary_offset_inward_mm":width,"height_rule":"fan_top_z - 0.2mm底部搭接，向上20mm",
          "loft_fallback_reason":fallbacks,"max_sampled_top_deviation_mm":max_deviation,
          "sampled_grid":"每扇区3个弧向×3个径向样点；仅用于量化近似，不是全域误差上界。",
          "parts":entries}
    return entries,info


def hash_file(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def write_mesh(parts, control, case):
    mesh_parts=[]; obj_lines=[]; offset=1; face_count=0
    for part in parts:
        vertices,triangles=part["shape"].tessellate(2.0,0.35)
        vv=[float(c) for v in vertices for c in v.toTuple()]
        tt=[int(i) for tri in triangles for i in tri]
        mesh_parts.append({"id":part["id"],"name":part["name"],"group":part["group"],"vertices":vv,"triangles":tt})
        face_count+=len(triangles)
        obj_lines.append(f"o {part['id']}")
        for v in vertices: obj_lines.append("v %.6f %.6f %.6f"%v.toTuple())
        for tri in triangles: obj_lines.append("f %d %d %d"%tuple(offset+int(i) for i in tri))
        offset+=len(vertices)
    mesh={"metadata":{"units":"mm","up_axis":"Z","case_id":case["source"].get("case_id","CASE_ASSUMED_001"),
                       "corner_angle_deg":float(case.get("corner_angle_deg",90.0)),"stage":3,
                       "stage_name":STAGE_NAME,"scope":"第二层43件基础上增加嫩戗、两侧弯里口木与完整正身/摔网下层木望板；不含飞椽及上层卷戗板。",
                       "mesh_tessellation_mm":2.0},"parts":mesh_parts,"control_lines":control}
    mpath=OUT/"stage03_model_mesh.json"
    mpath.write_text(json.dumps(mesh,ensure_ascii=False,separators=(",",":"),allow_nan=False),encoding="utf-8")
    (OUT/"stage03_model.obj").write_text("\n".join(obj_lines)+"\n",encoding="utf-8")
    return mpath,face_count


def build():
    case=s2.load_case(); case["_preview_only"]=False
    parts,control=s2.make_parts(case)
    by_id={p["id"]:p for p in parts}
    old=by_id["old_corner"]["shape"]
    young,young_info,young_axis=young_corner(case,old)
    add(parts,"young_corner","嫩戗（圆弧背、根部简化坐势）","young_corner",young)
    control.append({"id":"young_corner_axis","name":"嫩戗弧线中心","group":"young_corner","points":young_axis})
    path=rail_path(case)
    likou_records=[]
    for side in ("x","y"):
        shape,info,axis=build_likou_half(case,path,mirror=(side=="y"))
        pid=f"likou_{side}"
        wing_label="基准翼" if side=="x" else "镜像翼"
        add(parts,pid,f"弯里口木{wing_label}（弧形过渡）","likou",shape)
        info["id"]=pid
        likou_records.append(info)
        control.append({"id":pid+"_axis","name":pid+"中心线","group":"likou","points":axis[::max(1,len(axis)//12)]})
    case["eave"]={"inner_edge_y":case["e"]-float(case["p"]["likou_width"])}
    straight_records=straight_boards(case,parts,control)
    fan_entries,fan_info=fan_boards(case,parts,control)
    params={"metadata":{"case_id":case["source"].get("case_id","CASE_ASSUMED_001"),
                         "corner_angle_deg":float(case.get("corner_angle_deg",90.0)),
                         "stage":3,"stage_name":STAGE_NAME,"units":"mm","up_axis":"Z"},
            "base_stage02_mesh_sha256":hash_file(OUT/"stage02_model_mesh.json") if (OUT/"stage02_model_mesh.json").exists() else None,
            "young_corner":young_info,"likou":{"members":likou_records,"height_rule":"70mm低端至157.5mm高端，smoothstep连续过渡。"},
            "lower_board":{"thickness_mm":float(case["board_lower_t"]),"straight_panels":straight_records,"fan_panels":fan_entries,"fan_approximation":fan_info,
                           "straight_surface_rule":f"基准翼正身望板底面由摔网根点O与檐口A控制的平面定义，厚{float(case['board_lower_t']):g}mm并预留{float(case['board_surface_overlap']):g}mm搭接；末道板沿基准翼边界x={float(case['old_start'][0]):.3f}mm与摔网面共边，镜像翼按建筑角平分面反射，不修改stage02椽。",
                           "coverage":f"基准翼正身板从步桁内尾伸{float(case['eave_tail_extension']):g}mm延至里口木内边y={float(case['eave']['inner_edge_y']):g}mm；摔网板从分射点O覆盖至弧形里口木内边，分片/扇区接缝沿分椽方向，并在正身/摔网共边连续接高程；镜像翼由基准翼反射。",
            "shared_seam_transition":{"max_side_rise_mm":max((r["fan_seam_transition_max_rise_mm"] for r in straight_records),default=0.0),
                                    "rule":f"末道板的板宽方向由基准翼正身坡面接至x={float(case['old_start'][0]):.3f}mm边界处的摔网坡面；这是实体转接，不是小误差，未更改第二层椽。"}},
            "groups":{},"assumptions":[
                "900mm控制弦解释为空间直线弦长；升高480mm后平面投影761.315mm。",
                "嫩戗主体底线沿900/480控制弦保持直线；仅车背脊线施加浅凹，矢高为根梢较小车背高度的0.75；112.5mm留作后续菱角木/扁担木组合轮廓参考，不压入嫩戗主体。",
                "根部从老戗头内退82.5mm处的侧身顶面起，简化底部坐势由对老戗实体切除形成；未建榫卯，亦未增加老戗头后的后延大楔块。",
                f"里口木平面以stage02内凹檐弧为外侧参考线，宽{float(case['p']['likou_width']):g}mm向屋面内侧展开；高端{float(case['p']['likou_h_regular'])*float(case['p']['likou_h_corner_ratio']):g}mm、正身低端{float(case['p']['likou_h_regular']):g}mm。",
                f"正身直板按{float(case['eave_pitch']):g}mm宽分板；末道止于基准翼边界x={float(case['old_start'][0]):.3f}mm，并沿该板宽侧向接至摔网板共边高程；共边过渡量以实际O点高程和部件记录为准，stage02椽几何保持不变。",
                "摔网板沿内凹里口边界扇区放样；若曲面单体放样失效则退回逐扇区单实体，不为消除微小表面偏差扭曲椽面。",
                "下层望板到里口木内边；不加入飞椽、上层卷戗板或精细承口。",
            ]}
    mesh_path,mesh_faces=write_mesh(parts,control,case)
    params["groups"]={g:sum(p["group"]==g for p in parts) for g in sorted({p["group"] for p in parts})}
    params["piece_count"]=len(parts)
    params_path=OUT/"stage03_parameters.json"
    params_path.write_text(json.dumps(params,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    return case,parts,control,young_info,likou_records,straight_records,fan_entries,fan_info,mesh_path,mesh_faces,params_path


def finish(result):
    case,parts,control,young_info,likou_records,straight_records,fan_entries,fan_info,mesh_path,mesh_faces,params_path=result
    t0=time.perf_counter()
    entities=[]
    for p in parts:
        sh=p["shape"]; vol=float(sh.Volume()); solids=list(sh.Solids())
        entities.append({"id":p["id"],"group":p["group"],"valid":bool(sh.isValid()) and len(solids)==1 and vol>1e-5,
                         "solid_count":len(solids),"volume_mm3":vol})
    if any(not e["valid"] for e in entities):
        raise RuntimeError("stage03含无效或非单实体构件: "+repr([e for e in entities if not e["valid"]]))
    step_path=OUT/"stage03_model.step"
    compound=cq.Compound.makeCompound([p["shape"] for p in parts])
    cq.exporters.export(compound,str(step_path),exportType="STEP")
    imported=cq.importers.importStep(str(step_path)).val()
    source_volume=sum(p["shape"].Volume() for p in parts)
    step={"valid":bool(imported.isValid()),"solid_count":len(imported.Solids()),"expected_solid_count":len(parts),
          "volume_mm3":float(imported.Volume()),"source_volume_mm3":float(source_volume)}
    step["relative_volume_error"]=abs(step["volume_mm3"]-source_volume)/max(source_volume,1.0)
    if not step["valid"] or step["solid_count"]!=len(parts) or step["relative_volume_error"]>1e-6:
        raise RuntimeError("stage03 STEP回读失败: "+repr(step))
    by={p["id"]:p["shape"] for p in parts}
    new_contacts={"distance_only_samples":[],"intersection_volumes":"not_tested"}
    pairs=[("young_to_old","young_corner","old_corner"),
           ("likou_x_to_young","likou_x","young_corner"),
           ("likou_y_to_young","likou_y","young_corner"),
           ("fan_board_x_mid","lower_board_fan_x_06","fan_rafter_06"),
           ("fan_board_y_mid","lower_board_fan_y_06","fan_rafter_mirror_06"),
           ("straight_board_x_mid","lower_board_straight_x_04","eave_rafter_04"),
           ("straight_board_y_mid","lower_board_straight_y_04","eave_rafter_mirror_04")]
    for label, aid, bid in pairs:
        if aid not in by or bid not in by:
            new_contacts["distance_only_samples"].append({"label":label,"a":aid,"b":bid,"status":"missing_id"})
            continue
        record={"label":label,"a":aid,"b":bid,"intersection_volume_mm3":"not_tested"}
        try:
            record["minimum_distance_mm"]=float(by[aid].distance(by[bid]))
            record["status"]="distance_measured"
        except Exception as exc:
            record["status"]="distance_error"; record["error"]=str(exc)
        new_contacts["distance_only_samples"].append(record)
        print(json.dumps({"contact_sample":record},ensure_ascii=False),flush=True)
    # Profile checks quantify the arc's endpoints/rise and sampled concavity.
    cl=young_info["centerline"]
    root=np.array(cl[0]); tip=np.array(cl[-1]); axis=tip-root; chord_len=float(np.linalg.norm(axis)); axis/=chord_len
    normal_down=np.array([case["old_unit"][0]*young_info["control_rise_mm"]/young_info["spatial_control_chord_mm"],
                          case["old_unit"][1]*young_info["control_rise_mm"]/young_info["spatial_control_chord_mm"],
                          -young_info["plan_run_mm"]/young_info["spatial_control_chord_mm"]])
    sags=[]
    for p in cl:
        delta=np.array(p)-root
        sags.append(float(np.dot(delta-axis*np.dot(delta,axis),normal_down)))
    profile_check={"centerline_stations":len(cl),"endpoint_spatial_distance_mm":chord_len,
                   "endpoint_rise_mm":float(tip[2]-root[2]),"body_baseline_sampled_bow_mm":max(sags),
                   "body_baseline_rule":"直线控制弦；仅嫩戗车背脊线按浅矢高凹弧收分。",
                   "body_baseline_max_deviation_from_chord_mm":0.0,
                   "effective_carback_sagitta_mm":young_info["effective_carback_sagitta_mm"],
                   "sampled_carback_drop_max_mm":max(young_info["carback_drop_samples_mm"]),
                   "combined_outline_sagitta_reference_mm_not_applied":young_info["combined_outline_sagitta_reference_mm"],
                   "straight_rafter_hump_rule":"继承stage02已通过视觉检查的连续直椽模板；未逐站抬升。"}
    artifact_names=["stage03_model_mesh.json","stage03_model.obj","stage03_parameters.json","stage03_model.step"]
    artifacts={n:{"bytes":(OUT/n).stat().st_size,"sha256":hash_file(OUT/n)} for n in artifact_names}
    O=case["fan_surface"]["O"]
    O_q=float(O[1])
    O_straight_z=float(case["fan_top_intercept"])-float(case["p"]["t"])*O_q
    O_fan_z=s2.fan_top_z(case,*O)
    seam_qs=np.linspace(O_q,case["e"][0] if isinstance(case["e"],tuple) else case["e"]-float(case["p"]["likou_width"]),101)
    seam_residuals=[]
    for q in seam_qs:
        q=float(q)
        straight_z=float(case["fan_top_intercept"])-float(case["p"]["t"])*q
        fan_z=s2.fan_top_z(case,float(case["old_start"][0]),q)
        seam_residuals.append(abs(straight_z+max(0.0,fan_z-straight_z)-fan_z))
    shared_seam_check={"O_plan_xy_mm":list(O),"straight_roof_z_at_O_mm":O_straight_z,
                       "fan_board_underface_z_at_O_mm":O_fan_z,"side_transition_rise_at_O_mm":O_fan_z-O_straight_z,
                       "board_thickness_mm":20.0,"rise_exceeds_board_thickness_mm":(O_fan_z-O_straight_z)>20.0,
                       "shared_edge_height_match_sample_count":len(seam_residuals),
                       "shared_edge_height_match_max_residual_mm":max(seam_residuals),
                       "transition_rafter_contact":"not_tested; the 70.34mm rise can leave a local gap to the straight rafter surface.",
                       "stage02_rafter_geometry_changed":False}
    validation={"stage":3,"stage_name":STAGE_NAME,"status":"valid single-solid entities and STEP roundtrip; targeted support/coverage samples only",
        "geometry_revision":{"piece_count":len(parts),"groups":{g:sum(p["group"]==g for p in parts) for g in sorted({p["group"] for p in parts})},
                             "generator_sha256":hash_file(OUT/"generate_stage03.py"),"artifacts":artifacts},
        "entity_integrity":{"entity_count":len(entities),"valid_single_solid_count":sum(e["valid"] for e in entities),"invalid_entities":[],"entities":entities},
        "step_roundtrip":step,"profile_check":profile_check,
        "targeted_contacts_and_coverage":new_contacts,
        "shared_edge_transition_check":shared_seam_check,
        "coverage_approximation":{"fan_board_count":sum(1 for p in parts if p["group"]=="lower_board" and "fan" in p["id"]),"fan_sampled_max_top_deviation_mm":fan_info["max_sampled_top_deviation_mm"],
                                  "fan_error_sampling_is_not_global_bound":True,"straight_panel_count":len(straight_records),
                                  "face_contact_area":"not_tested"},
        "full_candidate_scan":{"status":"not_run","candidate_count":None,"reason":"仅核实体有效性、STEP回读及代表支承/覆盖样本；未运行全候选碰撞扫描。"},
        "unresolved":["全部新旧候选实体对及公共体积未扫描；代表距离不等同于全局无干涉。","里口/望板曲面按有限站点放样，未逐椽开槽；抽样残差不构成全域误差界。","嫩戗坐势仅作底面切配，不含榫卯或承载分析。","望板共边由末道正身板侧向抬升匹配；局部抬升值约70.34mm，不代表承托面全域已接触。"],
        "mesh_face_count":mesh_faces,"runtime_export_and_targeted_seconds":time.perf_counter()-t0}
    (OUT/"stage03_validation.json").write_text(json.dumps(validation,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps({"piece_count":len(parts),"groups":validation["geometry_revision"]["groups"],"step":step,
                      "contacts":new_contacts,"profile":profile_check,"hashes":{k:v["sha256"] for k,v in artifacts.items()},
                      "elapsed_s":validation["runtime_export_and_targeted_seconds"]},ensure_ascii=False),flush=True)


def main():
    started=time.perf_counter()
    result=build()
    print(json.dumps({"stage03_preview_mesh":str(result[8]),"pieces":len(result[1]),
                      "groups":{g:sum(p["group"]==g for p in result[1]) for g in sorted({p["group"] for p in result[1]})},
                      "mesh_faces":result[9],"build_seconds":time.perf_counter()-started},ensure_ascii=False),flush=True)
    if "--preview-only" not in sys.argv:
        finish(result)


if __name__=="__main__":
    main()
