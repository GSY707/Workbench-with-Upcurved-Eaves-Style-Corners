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
    assert not boot["active_job"], "A real generation is active; leave it undisturbed"
    root = request("/")
    assert b"<!doctype html" in root.lower(), "Workbench HTML missing"
    cases = request("/api/cases")["cases"]
    assert len(cases) >= 2, "Both verified examples must be available"
    cached = []
    for example_id in ("default", "size_variant_valid64"):
        example = next(e for e in boot["examples"] if e["id"] == example_id)
        checked = request("/api/validate", {"config": example["config"]})
        match = next((c for c in cases if c["calculation_key"] == checked["calculation_key"]), None)
        assert match and match["validated"], f"No current validated cache for {example_id}; refuse to start CAD"
        result = request("/api/jobs", {"config": example["config"]})
        job = result["job"]
        if not result.get("reused"):
            request(f"/api/jobs/{job['id']}/cancel", {})
            raise AssertionError("Expected cached reuse; unexpected generation cancelled")
        assert job["status"] == "succeeded" and job["result"]["validated"]
        displayed = job["result"]
        for name in ("model", "layout", "obj", "points", "svg", "config", "validation"):
            asset = request(displayed["urls"][name])
            assert asset, f"Empty {name} asset for {example_id}"
            if name in ("model", "layout"):
                assert b"<!doctype html" in asset.lower()
        cached.append({"example": example_id, "result_id": displayed["id"],
                       "metrics": displayed["metrics"]})
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "passed",
              "scope": "Real HTTP bootstrap, configuration validation, cached reuse and result downloads; no CAD rerun",
              "url": base, "cases": cached, "requests": records}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "http_requests": len(records),
                      "cached_examples": [c["example"] for c in cached],
                      "request_seconds": round(sum(r["seconds"] for r in records), 3)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
