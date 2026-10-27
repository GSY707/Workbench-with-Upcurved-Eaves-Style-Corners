"""Check generated page/data linkage without rebuilding CAD geometry."""
import argparse
import csv
import hashlib
import json
import sys
from html.parser import HTMLParser
from pathlib import Path


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.refs, self.payloads = set(), [], {}
        self.active = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        for key in ("src", "href"):
            if attrs.get(key):
                self.refs.append(attrs[key])
        if tag == "script" and attrs.get("type") == "application/json":
            self.active = attrs.get("id")
            self.payloads[self.active] = ""

    def handle_data(self, data):
        if self.active:
            self.payloads[self.active] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = None


def check_case(folder):
    folder = Path(folder).resolve()
    mesh = json.loads((folder / "model_mesh.json").read_text(encoding="utf-8-sig"))
    layout = json.loads((folder / "layout.json").read_text(encoding="utf-8-sig"))
    pages = {name: Page((folder / name).read_text(encoding="utf-8")) for name in ("index.html", "model.html", "layout.html")}
    refs = []
    for filename, page in pages.items():
        for ref in page.refs:
            if ref.startswith("#"):
                continue
            assert not ref.startswith(("http:", "https:", "//")), (filename, "external reference", ref)
            target = folder / ref.split("#")[0].split("?")[0]
            assert target.is_file(), (filename, "missing local reference", ref)
            refs.append((filename, ref))
    shown_mesh = json.loads(pages["model.html"].payloads["model-data"])
    resolved = json.loads((folder / "parameters_resolved.json").read_text(encoding="utf-8-sig"))
    assert shown_mesh["metadata"].get("corner_angle_deg", 90) == resolved["corner_angle_deg"], "viewer angle differs from configuration"
    assert shown_mesh["parts"] == mesh["parts"], "3D viewer embeds a different model"
    assert shown_mesh["metadata"]["viewer_source_sha256"] == hashlib.sha256((folder / "model_mesh.json").read_bytes()).hexdigest()
    shown_layout = json.loads(pages["layout.html"].payloads["layout-data"])
    assert shown_layout == layout, "layout viewer embeds different calculation data"
    required_model_ids = {
        "model-title", "stage-label", "scope-note", "model-summary", "group-list", "viewport",
        "load-error", "show-skeleton", "show-edges", "board-controls", "board-opacity",
        "board-opacity-value", "explode", "explode-value", "selection", "isolate", "restore",
        "visible-count", "fit", "flying-view",
    }
    required_layout_ids = {"summary", "step", "piece", "method", "translated", "save-svg",
                           "layout", "curves", "layout-stat", "curve-stat", "translation", "point-rows", "error"}
    assert required_model_ids <= pages["model.html"].ids
    assert required_layout_ids <= pages["layout.html"].ids
    with (folder / "marking_points.csv").open(encoding="utf-8-sig", newline="") as handle:
        table = list(csv.DictReader(handle))
    assert [row["template_id"] for row in table] == [piece["id"] for piece in layout["layout"]["pieces"]]
    part_ids = {part["id"] for part in mesh["parts"]}
    for row, piece in zip(table, layout["layout"]["pieces"]):
        ids = piece.get("model_ids", [])
        assert len(ids) == 2 and len(set(ids)) == 2 and set(ids) <= part_ids, (piece["id"], "invalid model mapping", ids)
        assert row["model_ids"] == ";".join(ids), (piece["id"], "CSV model mapping mismatch")
    validation = json.loads((folder / "validation.json").read_text(encoding="utf-8-sig"))
    report = {"status": "passed" if validation.get("passed") is True else "diagnostic_only", "geometry_validation_passed": validation.get("passed"), "scope": "离线页面、数据版本、尺寸表及本地链接整合检查；不代替浏览器交互验证",
              "case_dir": str(folder), "parts": len(mesh["parts"]), "templates": len(table),
              "local_references_checked": len(refs), "checks": ["embedded_mesh_matches", "embedded_layout_matches",
              "mesh_hash_matches", "viewer_angle_matches", "required_page_elements_exist", "marking_table_ids_match", "layout_model_ids_match", "local_references_exist"]}
    (folder / "delivery_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps([check_case(folder) for folder in args.case_dir], ensure_ascii=False))
