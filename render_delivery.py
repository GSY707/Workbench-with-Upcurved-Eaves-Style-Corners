"""Create offline viewers, static figures and a marking table from one generated case."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "资料整理/tmp/section_plot_deps"))
sys.path.insert(0, str(ROOT / "资料整理/tmp/model_deps"))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Polygon, Rectangle
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

FONT = FontProperties(fname="C:/Windows/Fonts/msyh.ttc")
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["svg.fonttype"] = "none"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def embedded(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluate(spec, x):
    xs = np.asarray(spec["knots_x_mm"])
    q = np.asarray(x, dtype=float)
    ix = np.clip(np.searchsorted(xs, q, side="right")-1, 0, len(xs)-2)
    cs = np.asarray([s["coeffs_descending_in_dx_mm"] for s in spec["segments"]])
    t = q-xs[ix]
    return ((cs[ix, 0]*t+cs[ix, 1])*t+cs[ix, 2])*t+cs[ix, 3]


def layout_figure(data, out, diagnostic=False):
    board = data["layout"]
    W, H, T = [board[k] for k in ("board_width_x_mm", "board_height_y_mm", "board_thickness_mm")]
    pieces = board["pieces"]
    fig, (ax, cx) = plt.subplots(1, 2, figsize=(14, 9), gridspec_kw={"width_ratios": [1, 1.1]})
    fig.subplots_adjust(top=.88, bottom=.12, wspace=.23)
    ax.add_patch(Rectangle((0, 0), W, H, facecolor="#fff7df", edgecolor="#aa6047", lw=1.7))
    for x in board["x_verticals_mm"]:
        ax.plot([x, x], [0, H], color="#84947f", lw=.55, alpha=.5)
    area = 0
    for piece in pieces:
        p = np.asarray([piece[k] for k in "ABCD"])
        area += abs(np.dot(p[:, 0], np.roll(p[:, 1], -1))-np.dot(p[:, 1], np.roll(p[:, 0], -1)))/2
        ax.add_patch(Polygon(p, facecolor="#94b79a", edgecolor="#476d50", alpha=.48, lw=.8))
        ax.text(*np.mean(p, axis=0), piece["id"], fontsize=7, ha="center", color="#31513c")
    colors = {"b": "#b04950", "c": "#277d9b", "d": "#c28c2f"}
    for key in "bcd":
        fit = data["fits"][key]["natural_cubic"]
        q = np.linspace(fit["knots_x_mm"][0], fit["knots_x_mm"][-1], 501)
        vals = evaluate(fit, q)
        ax.plot(q, vals, color=colors[key], lw=1.8)
        cx.plot(q, vals, color=colors[key], lw=1.8, label=key+" 自然三次样条")
        nodes = data["curve_nodes"][key]
        cx.scatter([p["x_mm"] for p in nodes], [p["y_mm"] for p in nodes], color=colors[key], s=18)
    tr = data.get("translation") or {}
    if isinstance(tr.get("dx_mm"), (int, float)):
        q = np.linspace(data["fits"]["c"]["natural_cubic"]["knots_x_mm"][0],
                        data["fits"]["c"]["natural_cubic"]["knots_x_mm"][-1], 501)
        method = tr.get("method", "natural_cubic")
        vals = evaluate(data["fits"]["d"][method], q-tr["dx_mm"])+tr["dy_mm"]
        cx.plot(q, vals, "--", color="#73558c", lw=1.7, label="d 平移得到的 c（对照）")
        note = f"c 平移对照：右移 {tr['dx_mm']:.3f}、上移 {tr['dy_mm']:.3f} mm；最大角点误差 {tr['max_abs_error_mm']:.3f} mm"
    else:
        note = "b、c、d 分别经过原角点；切割轮廓以原角点为准。"
    ax.axhline(0, color="#355e48", lw=2)
    ax.set_xlim(-W*.04, W*1.04)
    ax.set_ylim(-H*.025, H*1.04)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(f"每套 {len(pieces)} 根模板 × 2 套\n{W:.3f} × {H:.3f} × {T:.3f} mm；利用率 {100*area/(W*H):.2f}%", fontproperties=FONT, fontsize=11)
    cx.set_title("定位曲线与原角点", fontproperties=FONT, fontsize=12)
    cx.legend(prop=FONT, fontsize=9, loc="upper left")
    cx.grid(alpha=.2)
    for sub in (ax, cx):
        sub.set_xlabel("x / mm")
        sub.set_ylabel("y / mm")
    fig.suptitle("参数生成 · 立脚飞椽尖角触底排料", fontproperties=FONT, fontsize=17)
    fig.text(.5, .035, note, ha="center", fontproperties=FONT, fontsize=10)
    if diagnostic:
        fig.text(.5, .97, "诊断结果：未通过完整性检查，不能作为完整排料方案", ha="center", color="#a02020", fontproperties=FONT)
    fig.savefig(out / "layout.png", dpi=160)
    fig.savefig(out / "layout.svg")
    plt.close(fig)


def model_figure(model, out, diagnostic=False):
    palette = {
        "eave_purlins": "#99774f", "step_purlins": "#806243",
        "old_corner": "#795039", "young_corner": "#ad6d35",
        "flying_rafters": "#c99856", "straight_flying_rafters": "#c99856",
        "likou": "#858650", "lower_board": "#b1b79b", "upper_board": "#c2b593",
        "lingjiao": "#b27945", "biandan": "#916039",
    }
    fig = plt.figure(figsize=(12, 9))
    ax = fig.add_subplot(111, projection="3d")
    all_vertices = []
    for part in model["parts"]:
        v = np.asarray(part["vertices"], dtype=float).reshape(-1, 3)
        t = np.asarray(part["triangles"], dtype=int).reshape(-1, 3)
        all_vertices.append(v)
        board = "board" in part["group"]
        collection = Poly3DCollection(v[t], facecolors=palette.get(part["group"], "#bda784"),
                                      edgecolors="none", alpha=.16 if board else 1, linewidth=0)
        ax.add_collection3d(collection)
    vv = np.concatenate(all_vertices)
    lo, hi = vv.min(axis=0), vv.max(axis=0)
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(np.maximum(hi-lo, 1))
    ax.view_init(elev=29, azim=float(model.get("metadata", {}).get("corner_angle_deg", 90.0)) / 2.0)
    ax.set_xlabel("X / mm"); ax.set_ylabel("Y / mm"); ax.set_zlabel("Z / mm")
    ax.set_title("参数生成三维模型 · "+str(model.get("metadata", {}).get("case_id", out.name)),
                 fontproperties=FONT, fontsize=16)
    fig.text(.5, .04, f"{len(model['parts'])} 个构件；望板以半透明显示。完整交互视图见 model.html。",
             ha="center", fontproperties=FONT, fontsize=10)
    if diagnostic:
        fig.text(.5, .96, "诊断模型：未通过完整性检查", ha="center", color="#a02020", fontproperties=FONT)
    fig.savefig(out / "model_preview.png", dpi=140)
    plt.close(fig)


def render_case(case_dir: Path):
    case_dir = Path(case_dir).resolve()
    model_path, layout_path = case_dir / "model_mesh.json", case_dir / "layout.json"
    model, layout = read_json(model_path), read_json(layout_path)
    resolved = read_json(case_dir / "parameters_resolved.json")
    angle = float(resolved["corner_angle_deg"])
    if not math.isfinite(angle) or not 0.0 < angle < 180.0:
        raise ValueError("生成结果的建筑平面内角无效")
    stored_angle = model.get("metadata", {}).get("corner_angle_deg")
    if stored_angle is not None and float(stored_angle) != angle:
        raise ValueError("模型网格与本次配置的建筑平面内角不一致")
    model.setdefault("metadata", {})["corner_angle_deg"] = angle
    validation_path = case_dir / "validation.json"
    validation = read_json(validation_path) if validation_path.exists() else {}
    diagnostic = validation.get("passed") is False
    warning = '<aside style="padding:12px;background:#ffe4df;color:#8c2118;border:1px solid #dca99e">诊断结果：本案例未通过几何或完整性检查，不能作为完整模型和排料方案。详见 <a href="validation.json">检查记录</a>。</aside>' if diagnostic else ''
    model.setdefault("metadata", {})["viewer_source_sha256"] = hash_file(model_path)
    if not model["metadata"].get("case_id"):
        model["metadata"]["case_id"] = case_dir.name
    if not model["parts"]:
        raise ValueError("三维构件为空，无法生成查看器")
    (case_dir / "vendor").mkdir(exist_ok=True)
    for name in ("viewer.css", "layout.js"):
        shutil.copyfile(HERE / name, case_dir / name)
    for name in ("viewer-runtime.js", "LICENSE-three.txt"):
        shutil.copyfile(HERE / "vendor" / name, case_dir / "vendor" / name)
    for template, dest, marker, data in [
        ("viewer.template.html", "model.html", "__MODEL_DATA__", model),
        ("layout.template.html", "layout.html", "__LAYOUT_DATA__", layout),
    ]:
        text = (HERE / template).read_text(encoding="utf-8")
        if text.count(marker) != 1:
            raise ValueError(f"{template} 数据占位符不唯一")
        text = text.replace(marker, embedded(data))
        if warning:
            text = text.replace('<body>', '<body>'+warning, 1)
        (case_dir / dest).write_text(text, encoding="utf-8")
    with (case_dir / "marking_points.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["template_id", "model_ids", "left_x_mm", "right_x_mm", "A_y_mm", "B_y_mm", "C_y_mm", "D_y_mm", "sets"])
        for p in layout["layout"]["pieces"]:
            writer.writerow([p["id"], ";".join(p.get("model_ids", [])), p["A"][0], p["B"][0], p["A"][1], p["B"][1], p["C"][1], p["D"][1], 2])
    layout_figure(layout, case_dir, diagnostic=diagnostic)
    model_figure(model, case_dir, diagnostic=diagnostic)
    board = layout["layout"]
    pieces = board["pieces"]
    case_id = html.escape(str(model["metadata"]["case_id"]))
    fmt = lambda key: f"{board[key]:.3f}"
    links = [
        ("model.html", "三维模型：旋转、隐藏、单件查看"), ("layout.html", "立脚飞椽排料与三步放样"),
        ("model.obj", "OBJ 三维模型"), ("model.step", "STEP 实体模型"),
        ("marking_points.csv", "累计尺寸 CSV"), ("layout.svg", "排料 SVG 图"),
        ("parameters_resolved.json", "本次参数与派生值"), ("validation.json", "本次生成记录"),
        ("layout.json", "排料、样条与误差完整数据"), ("profiles.json", "立脚飞椽中心截面"),
    ]
    link_html = "".join('<li><a href="'+path+'">'+label+'</a></li>' for path, label in links if (case_dir/path).exists())
    index = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>'+case_id+' · 生成结果</title><style>body{max-width:1100px;margin:35px auto;padding:0 22px;background:#f4f3ed;'
             'color:#2e483b;font:15px/1.75 "Microsoft YaHei",sans-serif}h1{font-weight:500}a{color:#356753}'
             '.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}img{width:100%;background:white;border:1px solid #dce0d5}'
             'li{margin:7px 0}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style>'
             '<h1>'+case_id+' · 戗角生成结果</h1>'+warning+
             '<p>建筑平面内角 '+f'{angle:g}'+'°，左右对称；'+str(len(model["parts"]))+' 个三维构件；'+str(len(pieces))+' 种立脚飞椽模板 × 2 套。'
             '每套木板 '+fmt("board_width_x_mm")+' × '+fmt("board_height_y_mm")+' × '+fmt("board_thickness_mm")+' mm。</p>'
             '<div class="grid"><a href="model.html"><img src="model_preview.png" alt="三维模型"></a>'
             '<a href="layout.html"><img src="layout.png" alt="立脚飞椽排料"></a></div><ul>'+link_html+'</ul>'
             '<p>模型由本次参数计算；排料采用本次立脚飞椽中心截面的梯形近似。曲线拟合误差只作报告。</p>'
             '<p><a href="../../README.md">生成器使用说明</a> · <a href="../../参数说明.md">有效参数及几何约束</a></p></html>')
    (case_dir/"index.html").write_text(index, encoding="utf-8")
    artifacts = ["index.html", "model.html", "layout.html", "layout.png", "layout.svg", "model_preview.png", "marking_points.csv"]
    report = {
        "scope": "生成结果展示；不重新进行实体接触检查",
        "generation_completed": validation.get("passed"),
        "visual_review": validation.get("visual_review", {"status": "not_recorded"}),
        "model_mesh_sha256": hash_file(model_path),
        "corner_angle_deg": angle,
        "layout_json_sha256": hash_file(layout_path),
        "parts": len(model["parts"]), "templates": len(pieces), "sets": 2,
        "artifacts": {p: hash_file(case_dir/p) for p in artifacts},
    }
    (case_dir/"delivery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(render_case(args.case_dir), ensure_ascii=False))
