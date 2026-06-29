"""novel_AI agents package.

All 8 agents (writer + normalizer + compliance + checker + rewriter +
tracker + summarizer + outline) are now ported with real implementations.
The legacy stub.py is kept as a fallback in case any agent raises
ImportError during a hot reload; it is not used by the orchestrator.
"""
from .writer    import run_writer, set_active_router as set_writer_router
from .normalizer import run_normalizer
from .compliance import run_compliance
from .checker    import run_checker
from .rewriter   import run_rewriter
from .tracker    import run_tracker
from .summarizer import run_summarizer
from .outline    import run_outline

__all__ = [
    "run_writer", "set_writer_router",
    "run_normalizer", "run_compliance", "run_checker", "run_rewriter",
    "run_tracker", "run_summarizer", "run_outline",
]