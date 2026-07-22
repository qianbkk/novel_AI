"""Setup minimax provider + route all 15 engine roles to it.

通过 backend REST API 加 Provider + RoleAssignment 记录，让 engine 路由
所有 9 个 agent 走 MiniMax-M3。这是 Phase 2 测试的前置步骤。
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

API = os.environ.get("NOVEL_API", "http://127.0.0.1:8132")
KEY = "sk-cp-4nFWmy0to7cDWslbXUu6fq99Kdxl8ZjQKSbT7BeR4O0CfA9o2tFq8-tf3wic4UOlq7Y9HnVUxH1_ypvp8mOcMbBsoVn3wi5Bu-gmYALNbiGSvA0GHPMPxak"


def main():
    # 1. 列出现有 provider
    r = httpx.get(f"{API}/providers", timeout=10)
    print("existing providers:", r.status_code, len(r.json()))

    # 2. 创建 MiniMax provider
    payload = {
        "name": "MiniMax-M3 (real)",
        "provider_type": "minimax",
        "api_base": "https://api.minimaxi.com/v1",
        "api_key": KEY,
        "default_model": "MiniMax-M3",
        "extra_json": {},
        "needs_proxy": False,
    }
    r = httpx.post(f"{API}/providers", json=payload, timeout=10)
    print("create provider:", r.status_code, r.json())
    if r.status_code not in (200, 201):
        return
    provider_id = r.json()["id"]

    # 3. 列出 engine 15 个角色
    r = httpx.get(f"{API}/role-assignments", timeout=10)
    print("role-assignments:", r.status_code, len(r.json()))
    roles = r.json()

    # 4. 把所有 15 个角色绑到 minimax provider
    updated = 0
    for r_row in roles:
        rk = r_row["role_key"]
        r = httpx.put(
            f"{API}/role-assignments/{rk}",
            json={"provider_id": provider_id, "model_override": "MiniMax-M3"},
            timeout=10,
        )
        if r.status_code == 200:
            updated += 1
        else:
            print(f"  ✗ {rk}: {r.status_code} {r.text[:200]}")
    print(f"updated {updated}/{len(roles)} role assignments")

    # 5. 验证
    r = httpx.get(f"{API}/role-assignments", timeout=10)
    bound = sum(1 for r_row in r.json() if r_row.get("provider_id") == provider_id)
    print(f"final bound: {bound}/{len(r.json())}")


if __name__ == "__main__":
    main()
