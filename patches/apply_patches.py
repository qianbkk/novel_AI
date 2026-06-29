"""apply_patches.py — 一键把 patches/ 里的全部 fix apply 到 novel_AI/

Why: novel_AI/ is .gitignored in this repo (it is a reference submodule, not
versioned source). All hand-applied fixes (4 from 2026-06-28-novel_ai-mvp-fixes.md
plus 3 from the fusion audit) live only in this script + the .md descriptions.
Anyone who clones this repo on a new machine needs to re-apply them; doing
it by hand from prose is error-prone.

What this does: takes 7 fix descriptions (4 from .md + 3 fusion-audit) and
applies them in-place to 4 files in novel_AI/ (api_client.py, orchestrator.py,
run.py, agents/checker_agent.py, agents/tracker_agent.py) and creates
output/chapters/. Idempotent — running twice is safe.

Usage (from project root):
    python patches/apply_patches.py
    # verify:
    cd novel_AI && python -m tools.system_test     # 20/20 expected

Coverage (7 fixes total):
  P0 #1  api_client.py  - 5x 'with _get_client(N) as c:'  -> bare assignment
  P0 #4  api_client.py  - add tenacity _post_with_retry helper + 5 callers
  P0 #2  orchestrator.py - node_rewrite: re-run compliance after rewrite
  P0 #3  orchestrator.py - BUDGET_WARN/HARD relaxed (0.80/0.95 -> 1.00/1.50)
  P1     run.py          - 5 cmd_xxx defined AFTER dispatcher -> NameError
  P2     agents/checker_agent.py + tracker_agent.py - 'call_llm' not bound
                            to module attribute, so monkeypatch.setattr failed
  P3     output/chapters/ - 'export' tests crash if dir missing
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


# ---------- Fix 5: run.py — 5 个 cmd_xxx 必须定义在分发表之前 ----------
# 原版把 cmd_test / cmd_calibrate / cmd_acceptance / cmd_memory / cmd_fingerprint
# 写在了 if __name__ == "__main__" 的分发表之后，python run.py test 直接 NameError。
# 特征：line 226 if __name__ 之后紧跟 dispatch 字典，字典里出现 "calibrate/fingerprint/
# ac/test/memory" 几个 key —— 这意味着函数体还在更下面。
# 修法：找到 "calibrate": lambda: cmd_calibrate(), 那一行的位置，把
#  "def cmd_calibrate" 开始的整段 5 个函数定义挪到 "─ 路由 ─" 注释之前。
# 朴素做法：直接 delete 末尾的 5 个 def，再 insert 到 "─ 路由 ─" 之前。
RUN_PY_FIVE_CMDS_MARKER = '"calibrate": lambda: cmd_calibrate(),'


def fix5_run_py_command_order(path: Path) -> bool:
    """Move the 5 trailing cmd_xxx function definitions to before the dispatch
    block. Detected by the presence of the '─ 路由 ─' divider line."""
    text = path.read_text(encoding="utf-8")
    if "─ 路由 ─" not in text:
        return False

    # Find the 5 cmd_xxx definitions that come AFTER the if __name__ block.
    # The if __name__ block is delimited by 'if __name__ == "__main__":' and
    # an unindented line at column 0 after it.
    lines = text.splitlines(keepends=True)
    if_main_idx = next(
        (i for i, l in enumerate(lines) if l.startswith('if __name__ == "__main__":')),
        None,
    )
    if if_main_idx is None:
        return False
    # Find the line that ends the if block — the next unindented non-empty
    # line after the dispatch table.
    end_idx = None
    for j in range(if_main_idx + 1, len(lines)):
        if lines[j].startswith("def cmd_") and not lines[j].startswith("    def "):
            end_idx = j
            break
    if end_idx is None:
        return False  # already fixed

    # The 5 trailing cmd_xxx functions form a contiguous block from end_idx
    # until the next non-def content (or EOF).
    block_end = end_idx
    while block_end < len(lines) and (
        lines[block_end].startswith("def cmd_")
        or lines[block_end].strip() == ""
        or lines[block_end].lstrip().startswith(("#", '"', "'", ".", "_"))
    ):
        block_end += 1
    # Trim trailing blank lines
    while block_end > end_idx and lines[block_end - 1].strip() == "":
        block_end -= 1

    # Extract the 5 cmd_xxx block (with one trailing blank for spacing).
    moved = lines[end_idx : block_end + 1]

    # Find the "# ── 路由 ──" divider comment that precedes the if __name__
    # block. We want to insert the 5 functions immediately before that divider.
    router_idx = next(
        (i for i, l in enumerate(lines) if l.startswith("# ── 路由 ──")),
        None,
    )
    if router_idx is None:
        return False

    # Reassemble: pre + moved + between + rest
    new_lines = (
        lines[:router_idx]
        + moved
        + ["\n"]
        + lines[router_idx:end_idx]
        + lines[block_end + 1 :]
    )
    new_text = "".join(new_lines)
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    print("  [fix5] run.py: moved 5 cmd_xxx (calibrate/fingerprint/ac/test/memory) before dispatcher")
    return True


# ---------- Fix 6: agents/checker_agent.py + tracker_agent.py — bind call_llm ----------
# 原版用 call_llm(...) 闭包/global 查找，没绑到模块属性，monkeypatch 失败。
# 修法：import 行加 'from api_client import call_llm'，与其他 agent 一致。
def fix6_agents_call_llm_binding(agents_dir: Path) -> bool:
    """Bind call_llm as a module attribute on checker_agent and tracker_agent
    so unittest.mock.patch('agents.X.call_llm') actually works."""
    any_change = False
    for name in ("checker_agent", "tracker_agent"):
        path = agents_dir / f"{name}.py"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "from api_client import call_llm" in text:
            continue
        # Insert 'from api_client import call_llm' after the
        # 'sys.path.insert(...)' line. That keeps the bootstrap ordering
        # intact (api_client must be importable from the agent module).
        marker = "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        if marker not in text:
            print(f"  [fix6] {name}.py: path-insert marker not found, skipped")
            continue
        new_text = text.replace(
            marker,
            marker + "from api_client import call_llm\n",
            1,
        )
        path.write_text(new_text, encoding="utf-8")
        print(f"  [fix6] {name}.py: added 'from api_client import call_llm'")
        any_change = True
    return any_change


# ---------- Fix 7: output/chapters/ — export tests crash if dir missing ----------
def fix7_output_chapters_dir(novel_ai_dir: Path) -> bool:
    """Create output/chapters/ so the export tool tests don't crash on a fresh
    clone. novel_AI's own runs will create this naturally, but a brand-new
    clone runs the tests before any chapter has been generated."""
    chapters = novel_ai_dir / "output" / "chapters"
    if chapters.exists():
        return False
    chapters.mkdir(parents=True, exist_ok=True)
    # Drop a .gitkeep so the empty dir survives any future copy of novel_AI/
    # (currently gitignored, but be defensive).
    (chapters / ".gitkeep").write_text(
        "created by patches/apply_patches.py — keeps directory present for export tests\n",
        encoding="utf-8",
    )
    print(f"  [fix7] output/chapters/ created (with .gitkeep)")
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
    run_py = NOVEL_AI_DIR / "run.py"
    agents_dir = NOVEL_AI_DIR / "agents"
    for p in (api_client, orchestrator, run_py, agents_dir):
        if not p.exists():
            print(f"ERROR: novel_AI source missing: {p}")
            return 1

    print(f"Applying 7 fixes to {NOVEL_AI_DIR}")
    # 4 fixes documented in 2026-06-28-novel_ai-mvp-fixes.md
    fix1_api_client_pool(api_client)
    fix4_api_client_retry(api_client)
    fix2_orchestrator_compliance(orchestrator)
    fix3_orchestrator_budget(orchestrator)
    # 3 fixes from the fusion audit (novel-ai-fusion-changes-guide)
    fix5_run_py_command_order(run_py)
    fix6_agents_call_llm_binding(agents_dir)
    fix7_output_chapters_dir(NOVEL_AI_DIR)
    print("Done. Verify with:  cd novel_AI && python -m tools.system_test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
