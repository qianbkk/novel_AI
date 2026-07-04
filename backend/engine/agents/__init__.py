"""novel_AI agents package.

All 8 agents (writer + normalizer + compliance + checker + rewriter +
tracker + summarizer + outline) are now ported with real implementations.
The legacy stub.py is kept as a fallback in case any agent raises
ImportError during a hot reload; it is not used by the orchestrator.

#45 简化：writer.py 不再有自己的 set_active_router（删掉了私有 _ACTIVE_ROUTER
# 状态，统一从 engine.llm_router.get_active_router() 读）。__init__ 这里
# set_writer_router 别名也跟着删。
"""
from .writer    import run_writer
from .normalizer import run_normalizer
from .compliance import run_compliance
from .checker    import run_checker
from .rewriter   import run_rewriter
from .tracker    import run_tracker
from .summarizer import run_summarizer
from .outline    import run_outline

__all__ = [
    "run_writer",
    "run_normalizer", "run_compliance", "run_checker", "run_rewriter",
    "run_tracker", "run_summarizer", "run_outline",
]