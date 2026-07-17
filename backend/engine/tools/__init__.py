"""Novel AI engine tools package.

Submodules are intentionally loaded on demand. Eagerly importing every CLI tool
caused avoidable startup side effects and made ``python -m
engine.tools.system_test`` initialize the module twice.
"""

__all__ = [
    "bootstrap", "budget_manager", "chapter_checker", "fingerprint_checker",
    "exporter", "human_review", "style_manager", "calibrate_checker",
    "acceptance_tests", "system_test",
]
