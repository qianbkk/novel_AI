# 死代码清理候选清单 — 2026-07-16 盘点

**状态**：本文档仅列候选，**未做任何删除**。等用户研究后逐个确认。

## 1. 确认死代码（grep 验证 0 import）

### 1.1 `frontend/src/components/Dialog.tsx` (57 行)

```tsx
export function Dialog({ open, title, sub, onClose, children, actions, wide, className }: DialogProps) {
  // 浏览器原生 <dialog> + showModal()
  // ESC 关闭 / 焦点陷阱 / ::backdrop
}
```

**状态**：

- 全文 grep `import.*Dialog` → **0 个 importer**
- `LoginDialog.tsx` 自己用 `<dialog>` 直接实现（不基于 Dialog.tsx）
- 注释说"宽屏模式（章节详情用）" — 但章节详情已经重构到 `ChapterReader.tsx` 独立页面（`/projects/:id/chapter/:chapterNo`），不再用 Dialog
- 文档修订日期 `2026-07-16` 标的就是这个章节详情重构

**风险评估**：低。57 行纯函数组件，0 调用，删了零影响。

### 1.2 `backend/scripts/dev_split_invariants.py`

```python
"""按业务域拆分 tests/test_invariants.py 到 tests/invariants/ 子包。"""
# 一次性脚本
```

**状态**：

- 对应 commit `d91db8d feat(phase4): multi-user auth + alembic + prod hardening + test split`
- 拆分完成后 `tests/invariants/` 子包已是主战场（22 个测试文件，1100+ 测试）
- 该脚本**任务已完成**

**风险评估**：低。一次性脚本，任务结束。重新跑一遍应该是幂等的（脚本本身有 idempotent 注释）。

### 1.3 `backend/scripts/fixup_50ch_audit.py`

```python
"""50 章端到端测试暴露的 bug 修复脚本（chapter-entity backfill + junk-header strip）。"""
```

**状态**：

- 对应 commit `efd6345 fix: chapter-entity backfill + junk-header strip + v3 guide (#2)`
- 50 章 bug 修复完成（5 个真实 bug，详见 `docs/root-cause-analysis.md`）
- 该脚本**任务已完成**

**风险评估**：低。一次性脚本，bug 修复 + DB backfill 已完成。

## 2. 误判示例（不是 dead code）

**`_run_bridge_async` stub（`backend/app/api/bridge.py:519`）** —— 看起来像 dead code，
但 commit `2055746 fix(bridge): 清理 _run_bridge_async / _run_bridge_async_imported 死代码`
之后保留 stub + 抛 NotImplementedError 是**主动设计**：防止新代码误用旧的 in-process 路径。

有专门的反退化测试守护：
- `test_no_run_bridge_async_imported_string_in_source`
- `test_run_bridge_async_only_stub`

**`_run_bridge_async_imported` 字符串搜索逻辑** — 同上，属于 anti-regression 防御。

## 3. 不是死代码但看起来像

### 3.1 `tests/test_invariants.py` (26 行)

```python
"""re-export shim，原 8500 行单文件已按业务域拆分到 invariants/"""
from tests.invariants.test_audit import *
from tests.invariants.test_backup import *
# ... 14 行 re-export
```

**不是 dead code**——是 Phase 3 拆分时的**向后兼容 shim**：
- 保留 `pytest tests/test_invariants.py` 命令仍能工作
- git log 检索 `tests/test_invariants.py::` 不全断

可以删，但代价是 break 外部脚本。

### 3.2 `tests/invariants/test_misc.py` (40 行)

```python
def _backend_alive(base_url: str, timeout: float = 1.0) -> bool:
    """socket 探测后端存活，给其他测试做 skipif 用。"""
```

**不是 dead code**——是给其他 `test_*.py` 文件用的 helper
（被 `test_frontend_align.py` 的 `@pytest.mark.skipif` 装饰器引用）。

## 4. 下一步

等用户逐一确认：
- [ ] Dialog.tsx — 确认删除？
- [ ] dev_split_invariants.py — 确认删除？
- [ ] fixup_50ch_audit.py — 确认删除？

每删一个单独 commit（用户偏好），commit message 模板：

```
chore(cleanup): 删 <文件名>（dead code，0 importer / 任务已完成）

验证：
- grep "import.*<filename>" backend/ frontend/src/ → 0 hits
- git log --follow <filename> → 历史可追溯
- 没有测试引用（test_no_<thing>_orphan 模式可加守护）
```