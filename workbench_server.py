#!/usr/bin/env python
"""Serve the local symmetric corner workbench on loopback only."""
from __future__ import annotations

import argparse
import sys

from workbench_backend import serve


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="本地木构造参数工作台服务")
    parser.add_argument("--port", type=int, default=8765, help="仅绑定127.0.0.1的HTTP端口")
    args = parser.parse_args(argv)
    try:
        serve(args.port)
    except OSError as exc:
        parser.exit(2, f"无法绑定本地工作台端口 {args.port}：{exc}\n")
    except (RuntimeError, ValueError) as exc:
        parser.exit(2, f"工作台启动失败：{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
