"""export_openapi.py — 导出后端 OpenAPI 3 spec 到 frontend/openapi.json

用法:
    cd backend
    python -m scripts.export_openapi

    # 或指定后端 URL（默认 http://localhost:8132）
    python -m scripts.export_openapi --url http://localhost:8132

    # 或指定输出路径（默认 ../frontend/openapi.json）
    python -m scripts.export_openapi --out ../frontend/openapi.json

历史背景：
  frontend/openapi.json 之前是手工导出 + commit 进来的，commit 之后就漂了：
  后端加了 rules / foreshadowings / ai-assist-level / reimport-chapters /
  strip-junk-headers 等 10+ 端点，openapi.json 都没记录。

  本脚本从运行中的后端直接拉 /openapi.json 写到 frontend/，CI 可以独立
  校验漂移。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="导出后端 OpenAPI spec 到前端")
    parser.add_argument(
        "--url",
        default="http://localhost:8132",
        help="后端 base URL（默认 http://localhost:8132）",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"),
        help="输出文件路径（默认 frontend/openapi.json）",
    )
    args = parser.parse_args()

    try:
        import httpx
    except ImportError:
        print("✗ httpx 未装，请先: pip install httpx", file=sys.stderr)
        return 1

    # 1. 拉 openapi.json
    try:
        resp = httpx.get(f"{args.url}/openapi.json", timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"✗ 拉 {args.url}/openapi.json 失败：{e}", file=sys.stderr)
        print(f"  请确认后端在 {args.url} 运行中", file=sys.stderr)
        return 1

    spec = resp.json()
    paths_count = len(spec.get("paths", {}))

    # 2. 写到目标
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 迭代 #56: 改用 atomic_write_json（跟 iter #43/#49/#55 同型 —
    # openapi.json 是 CI 校验漂移的基准，半写损坏会掩盖真实漂移）
    from engine.utils import atomic_write_json
    atomic_write_json(str(out_path), spec)
    print(f"✓ 导出 {paths_count} 个 paths 到 {out_path}")
    print(f"  文件大小: {out_path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())