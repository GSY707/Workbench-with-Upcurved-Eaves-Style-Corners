"""Check the real workbench API and delivered assets, using existing results only."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--case-id", action="append", default=[],
                        help="核对指定的已登记结果，可重复；只允许命中当前源码缓存，不启动CAD")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    records = []

    def request(path, payload=None):
        start = time.perf_counter()
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", "Origin": base} if data else {}
        req = urllib.request.Request(base + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read()
            mime = response.headers.get_content_type()
            records.append({"path": path, "status": response.status, "mime": mime,
                            "bytes": len(body), "seconds": round(time.perf_counter() - start, 4)})
        return json.loads(body) if mime == "application/json" else body

    boot = request("/api/bootstrap")
    assert boot["app_id"] == "mu-gouzao-corner-workbench", "Unexpected local service"
    assert boot["version"] == "0.4.0", "Preview checks require workbench 0.4.0"
    assert not boot["active_job"], "A real generation is active; leave it undisturbed"
    root = request("/")
    assert b"<!doctype html" in root.lower(), "Workbench HTML missing"
    cases = request("/api/cases")["cases"]
    assert cases, "No completed case is available"
    cached = []
    if args.case_id:
        selections = [next(c for c in cases if c["id"] == case_id) for case_id in args.case_id]
    else:
        selections = [next(e for e in boot["examples"] if e["id"] == example_id)
                      for example_id in ("default", "size_variant_valid64")]
    for selection in selections:
        example_id = selection["id"]
        mode = selection.get("mode", "exact")
        assert mode in ("preview", "exact"), f"Unknown case mode: {mode}"
        checked = request("/api/validate", {"config": selection["config"], "mode": mode})
        other_mode = "exact" if mode == "preview" else "preview"
        other_checked = request("/api/validate", {"config": selection["config"], "mode": other_mode})
        assert checked["calculation_key"] != other_checked["calculation_key"], "Modes share a calculation key"
        match = next((c for c in cases if c["calculation_key"] == checked["calculation_key"]
                      and c.get("mode", "exact") == mode), None)
        assert match and match["validated"], f"No current validated cache for {example_id}; refuse to start CAD"
        result = request("/api/jobs", {"config": selection["config"], "mode": mode})
        job = result["job"]
        if not result.get("reused"):
            request(f"/api/jobs/{job['id']}/cancel", {})
            raise AssertionError("Expected cached reuse; unexpected generation cancelled")
        assert job["status"] == "succeeded" and job["result"]["validated"]
        displayed = job["result"]
        assert job.get("mode") == mode and displayed.get("mode") == mode, "Returned mode mismatch"
        if mode == "preview":
            assert not any(displayed["urls"].get(name) for name in ("layout", "points", "svg")), \
                "Preview exposes fabrication downloads"
            asset_names = ("model", "index", "obj", "config", "validation")
        else:
            asset_names = ("model", "layout", "obj", "points", "svg", "config", "validation")
        for name in asset_names:
            asset = request(displayed["urls"][name])
            assert asset, f"Empty {name} asset for {example_id}"
            if name in ("model", "layout", "index"):
                assert b"<!doctype html" in asset.lower()
            if name == "model" and mode == "preview":
                assert "预览".encode("utf-8") in asset, "Offline model lacks preview label"
            if name == "validation":
                assert asset.get("mode", "exact") == mode, "Validation mode mismatch"
        cached.append({"example": example_id, "result_id": displayed["id"],
                       "mode": mode, "metrics": displayed["metrics"]})
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "passed",
              "scope": "Real HTTP bootstrap, mode-separated identities, cached reuse and mode-specific result downloads; no CAD rerun",
              "url": base, "cases": cached, "requests": records}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "http_requests": len(records),
                      "cached_examples": [c["example"] for c in cached],
                      "request_seconds": round(sum(r["seconds"] for r in records), 3)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
