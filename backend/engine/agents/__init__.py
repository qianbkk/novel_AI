"""novel_AI agents package.

所有 agent 都有真实实现（writer / normalizer / compliance / checker /
rewriter / tracker / summarizer / outline + planner / init_arc）。
orchestrator 直接调这里列出的 run_* 入口；没有 stub 兜底——某个 agent
import 失败会让整个进程崩溃（fail-fast，符合 CHANGELOG 里 #62 系列的
"silent fallback 反模式"修法）。

#45 简化：writer.py 不再有自己的 set_active_router（删掉了私有 _ACTIVE_ROUTER
# 状态，统一从 engine.llm_router.get_active_router() 读）。__init__ 这里
# set_writer_router 别名也跟着删。

迭代 #75: 之前的注释 "legacy stub.py is kept as a fallback" 指向一个
已经不存在的模块（commit 历史上 stub.py 被删除），留下误导性引用——
开发/审计读起来以为有兜底实现，实际靠 hot-reload 引入是 ImportError
直传上层。已修：注释改为准确描述实际行为。
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