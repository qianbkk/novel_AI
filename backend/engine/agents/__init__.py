"""novel_AI agents package.

P1 scope: only Writer is fully implemented. Other agents have stub
implementations in .stub that satisfy the orchestrator's call
signature (return sensible defaults so the graph can be exercised
end-to-end without each agent being production-ready). P2 will
replace stubs with real implementations imported one at a time.
"""
from .writer import run_writer
from .stub import (
    run_normalizer, run_compliance, run_checker, run_rewriter,
    run_tracker, run_summarizer, run_outline,
)

__all__ = [
    "run_writer",
    "run_normalizer", "run_compliance", "run_checker", "run_rewriter",
    "run_tracker", "run_summarizer", "run_outline",
]
