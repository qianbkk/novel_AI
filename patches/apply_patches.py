"""apply_patches.py — 一键把 patches/ 里的 4 个 fix apply 到 novel_AI/

Why: novel_AI/ is .gitignored in this repo (it is a reference submodule, not
versioned source). The 4 fixes documented in 2026-06-28-novel_ai-mvp-fixes.md
were applied by hand on a previous machine and live only in the markdown.
Anyone who clones this repo on a new machine needs to re-apply them; doing
it by hand from prose is error-prone.

What this does: takes the 4 fix descriptions from the .md file and applies
them in-place to the 2 affected files (novel_AI/api_client.py and
novel_AI/orchestrator.py). Idempotent — running twice is safe.

Usage (from project root):
    python patches/apply_patches.py
    # verify:
    python -m tools.system_test     # inside novel_AI/
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NOVEL_AI_DIR = PROJECT_ROOT / "novel_AI"
PATCHES_MD = Path(__file__).resolve().parent / "2026-06-28-novel_ai-mvp-fixes.md"


# ---------- Fix 1: api_client.py — 5 处 with _get_client(X) as c: → 裸调用 ----------
# 原:   with _get_client(120) as c:
#           r = c.post(URL, headers=headers, json=payload)
#           r.raise_for_status()
#           data = r.json()
# 改:   c = _get_client(120)
#       r = c.post(URL, headers=headers, json=payload)
#       r.raise_for_status()
#       data = r.json()
# 涉及 line 174/200/224/268/329（5 处）
FIX1_PATTERN = re.compile(
    r"^(\s*)with _get_client\((\d+)\) as c:\s*\n"
    r"(\s*)r = c\.post\(",
    re.MULTILINE,
)


def fix1_api_client_pool(path: Path) -> bool:
    """Replace 'with _get_client(N) as c:' blocks with bare assignment so the
    httpx connection pool survives across calls."""
    text = path.read_text(encoding="utf-8")
    if "with _get_client(120) as c:" not in text:
        return False
    new_text, n = FIX1_PATTERN.subn(
        r"\1c = _get_client(\2)\n\3r = c.post(",
        text,
    )
    path.write_text(new_text, encoding="utf-8")
    print(f"  [fix1] api_client.py: replaced {n} 'with _get_client' blocks")
    return n > 0


# ---------- Fix 4: api_client.py — tenacity 网络重试 ----------
# Helper: _post_with_retry wraps client.post + status code handling.
# 5 个 caller (_deepseek/_gemini/_kimi/_minimax/_custom) 改用它。
HELPER_CODE = '''

class _HTTPClientError(httpx.HTTPError):
    """Surfaces 4xx errors so callers (and tenacity) can see them."""
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
def _post_with_retry(client, url, **kwargs):
    r = client.post(url, **kwargs)
    if 500 <= r.status_code < 600:
        r.raise_for_status()  # HTTPStatusError -> tenacity retry
    elif 400 <= r.status_code < 500:
        raise _HTTPClientError(r.status_code, f"HTTP {r.status_code}: {r.text[:200]}")
    return r
'''


def fix4_api_client_retry(path: Path) -> bool:
    """Add tenacity retry helper and route the 5 raw httpx callers through it."""
    text = path.read_text(encoding="utf-8")
    if "_post_with_retry" in text:
        return False  # already applied

    # 1. Top-level import block: add tenacity line if not present.
    if "from tenacity import" not in text:
        text = re.sub(
            r"^(import httpx[^\n]*\n)",
            r"\1from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type\n",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        print("  [fix4] api_client.py: added tenacity import")

    # 2. Insert helper just AFTER `_get_client` is defined.
    text = re.sub(
        r"(def _get_client\([^\n]*\n(?:[ \t]+[^\n]*\n)*?)",
        lambda m: m.group(1) + HELPER_CODE,
        text,
        count=1,
    )

    # 3. Wrap 5 raw `c.post(URL, ...)` sites with `_post_with_retry(c, URL, ...)`.
    # The 5 callers all have the shape:
    #     r = c.post(URL, headers=..., json=...)
    #     r.raise_for_status()
    # We swap to _post_with_retry which already raises on 5xx, then keep
    # r.raise_for_status() but only for the 4xx branch (it never fires now).
    # Simplest: just replace the c.post with the helper call.
    # Use 5 named patterns to avoid over-matching.
    called = 0
    for url_marker in [
        "DEEPSEEK_API_URL",
        "GEMINI_API_URL",
        "KIMI_API_URL",
        "MINIMAX_API_URL",
        "CUSTOM_API_URL",
    ]:
        pat = re.compile(
            rf"(\s+)r = c\.post\(({url_marker}),",
        )
        new_text, n = pat.subn(rf"\1r = _post_with_retry(c, \2,", text)
        if n:
            text = new_text
            called += n
    path.write_text(text, encoding="utf-8")
    print(f"  [fix4] api_client.py: routed {called} httpx callers through _post_with_retry")
    return called > 0


# ---------- Fix 2: orchestrator.py — node_rewrite 补 run_compliance ----------
# 在 run_normalizer 之后 + run_checker 之前，插入一段重新跑 compliance。
# 用一个 sentinel 标记来定位。
FIX2_SENTINEL_AFTER = "        _add_cost(state, _normalize_cost)"
FIX2_INJECT = '''
        # Bug 2 fix: re-verify compliance after rewrite
        comp_result, cost = run_compliance(clean_text, state.get("platform", "fanqie"))
        _add_cost(state, cost)
        if not comp_result["passed"]:
            log(f"  🛡️  重写后仍违规：{comp_result.get('reason', '')}", state)
            task["_draft_text"]          = clean_text
            task["_compliance_failed"]   = True
            task["_compliance_feedback"] = comp_result.get("reason", "违规内容需重写")
            state["current_task"]        = task
            return state  # skip checker; route_after_rewrite 路由回 rewrite
'''


def fix2_orchestrator_compliance(path: Path) -> bool:
    """Insert re-compliance check after normalizer in node_rewrite."""
    text = path.read_text(encoding="utf-8")
    if "Bug 2 fix: re-verify compliance after rewrite" in text:
        return False
    if FIX2_SENTINEL_AFTER not in text:
        print(f"  [fix2] orchestrator.py: sentinel not found ({FIX2_SENTINEL_AFTER!r}). "
              "Patch the file by hand from 2026-06-28-novel_ai-mvp-fixes.md.")
        return False
    new_text = text.replace(FIX2_SENTINEL_AFTER, FIX2_SENTINEL_AFTER + FIX2_INJECT, 1)
    path.write_text(new_text, encoding="utf-8")
    print("  [fix2] orchestrator.py: added re-compliance after rewrite")
    return True


# ---------- Fix 3: orchestrator.py — 放宽预算 ----------
def fix3_orchestrator_budget(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "BUDGET_WARN   = 1.00" in text:
        return False  # already relaxed
    new_text = re.sub(
        r"BUDGET_WARN\s*=\s*0\.80\s*#\s*80%发警告",
        "BUDGET_WARN   = 1.00   # 100% 发警告（MVP 放宽）",
        text,
    )
    new_text = re.sub(
        r"BUDGET_HARD\s*=\s*0\.95\s*#\s*95%硬停",
        "BUDGET_HARD   = 1.50   # 150% 硬停（防失控，95% 太严）",
        new_text,
    )
    if new_text == text:
        print("  [fix3] orchestrator.py: budget lines not found (no change)")
        return False
    path.write_text(new_text, encoding="utf-8")
    print("  [fix3] orchestrator.py: budget thresholds relaxed (0.80/0.95 -> 1.00/1.50)")
    return True


# ---------- driver ----------
def main() -> int:
    if not NOVEL_AI_DIR.exists():
        print(f"ERROR: novel_AI/ not found at {NOVEL_AI_DIR}")
        print("       clone or copy the original novel_AI source here first.")
        return 1
    if not PATCHES_MD.exists():
        print(f"ERROR: patches md missing: {PATCHES_MD}")
        return 1

    api_client = NOVEL_AI_DIR / "api_client.py"
    orchestrator = NOVEL_AI_DIR / "orchestrator.py"
    if not api_client.exists() or not orchestrator.exists():
        print(f"ERROR: novel_AI source files missing under {NOVEL_AI_DIR}")
        return 1

    print(f"Applying 4 fixes to {NOVEL_AI_DIR}")
    fix1_api_client_pool(api_client)
    fix4_api_client_retry(api_client)
    fix2_orchestrator_compliance(orchestrator)
    fix3_orchestrator_budget(orchestrator)
    print("Done. Verify with:  cd novel_AI && python -m tools.system_test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
