# 代码审查应用（2026-07-13）

本批基于 `D:\Users\桌面\linshi.txt` 的两份历史 commit 审查报告（9821c2e + 70dd44a）。
两份报告独立审查了同一仓库、且都对 70dd44a 做了评——互补但部分重复。

## 批判性评估（辩证）

按 [Prioritize real risks over future armor](../memory/prioritize-real-risks-over-future-armor.md) 过滤：

| 来源 | 项 | 评估 | 决定 |
|------|---|------|------|
| 9821c2e | 🟡-2 stage 端点测试只检查 keys 不检查 order | 中 — 这是本 commit 想解决的 drift，但只验证了一半 | ✅ |
| 9821c2e | 🟡-3 不检查 extras（keys - expected） | 补一行即可 | ✅ (已修，后续 commit 加上了) |
| 9821c2e | 🟢-2 fetch 失败静默 | console.warn 调试友好 | ✅ (已修) |
| 9821c2e | 📌-3 test_invariants.py section 重复注释 | 纯 cosmetic | ⏭ skip |
| 70dd44a | 🟡-1 strip_markdown_fence 死代码（4 个 agent 替换实际只做了 1 个） | **最关键** — commit message 撒谎，未来 fence 处理 bug 漏改 | ✅ 真做替换 |
| 70dd44a | 🟡-2 truncate_preserving_ends 缺 head+tail >= threshold 护栏 | 防 caller 误用 | ✅ |
| 70dd44a | 🟡-3 strip_markdown_fence 类型注解与 None 行为不一致 | 顺手 | ✅ |
| 70dd44a | 🟢-1 有/无 fence 分支返回值不一致 | 顺手 | ✅ |
| 70dd44a | 🟢/📌 杂项（_merge_threads docstring / _norm 嵌套 / 测试拆分 / dict fuzzy dedup） | nitpick | ⏭ skip |

## 应用清单

### #1 stage 端点测试加 order 断言（9821c2e 🟡-2）

- **状态**: ✅ 已修复（2026-07-13）
- **现状**: `test_worldbuild_stages_endpoint` 只 `assert keys >= expected`
  (subset)，没检查顺序（extras 检查实际上后续已修过）。
- **修法**: 期望顺序作为命名常量，加 `assert actual_order == expected_order`。
- **位置**: `backend/tests/test_alignment_smoke.py::test_worldbuild_stages_endpoint`

### #2 strip_markdown_fence 真做替换（70dd44a 🟡-1）

- **状态**: ✅ 已修复（2026-07-13）
- **现状**: 报告反复指出 commit message 把 4 处 inline 副本列在"之前"一栏，但
  实际只删了 checker.py 那行 broken lstrip。outline.py:225-230 +
  manager.py:240-244 还在 inline，utils.py:127-134 parse_llm_json_response
  内部也有副本（parse_llm_json_response 自己也 inline）。
- **修法**:
  1. `backend/engine/utils.py::strip_markdown_fence` 统一行为：类型注解改为
     `str | None` → `str | None`；有/无 fence 两个分支都 strip（行为一致）
  2. `backend/engine/agents/outline.py::_extract_json_array` 改用 helper
  3. `backend/engine/memory/manager.py::_secondary_summarize_cold_history` 改用 helper
  4. `backend/engine/utils.py::parse_llm_json_response` 内部 fence 剥离逻辑改调 helper
  5. `backend/tests/test_utils_helpers.py` 加 `test_outline_and_manager_use_helper_not_inline`
     — 通过静态扫描锁死未来不能回退成 inline（防止重蹈"commit 撒谎"覆辙）

### #3 truncate_preserving_ends 加 head+tail >= threshold 护栏（70dd44a 🟡-2）

- **状态**: ✅ 已修复（2026-07-13）
- **现状**: helper 把阈值参数化、调用点从硬编码改成传参，但没做参数合理性校验。
  当前两个 caller 都满足约束，但未来 caller 不守规矩时静默产出比原文还长的"截断"。
- **修法**: 函数开头加 `if head_chars + tail_chars >= threshold: log.warning(...)`
  并 return 原样——**fail-soft 不 fail-fast**，保留向后兼容（旧 caller 不希望
  生产环境突然崩）。`backend/tests/test_utils_helpers.py::test_truncate_warns_when_head_plus_tail_exceeds_threshold`
  用 caplog 锁死行为。

### #4 strip_markdown_fence 类型 + 行为一致化（70dd44a 🟡-3 / 🟢-1）

- **状态**: ✅ 已修复（2026-07-13，与 #2 合并）
- **现象**: 函数签名 `def strip_markdown_fence(resp: str) -> str` 与实际
  `if not resp: return resp`（容忍 None / 空字符串）不一致；测试里有
  `# type: ignore[arg-type]`。
- **修法**: 类型注解改为 `def strip_markdown_fence(resp: str | None) -> str | None`；
  两个分支都返回 strip 后的字符串（行为一致）；删测试里的 `# type: ignore`。

## 跳过项说明

- **9821c2e 📌-3**：test_invariants.py section 重复注释——纯编辑残留，
  commit diff 范围外，不在本次审阅的目标代码里。
- **70dd44a 🟢-2**：_is_fuzzy_dup window 默认值标注——文档建议，不改行为。
- **70dd44a 🟢-3**：_is_fuzzy_dup 空字符串处理——所有现有 caller 都有
  `if not s:` guard，加函数内 guard 会改变语义（"空不算 dup" vs "空算 dup"）。
  报告本身说"取决于语义选择"，暂无明确 winner，跳过。
- **70dd44a 🟢-4**：test_strip_fence_empty 拆分 None 和空串——nitpick。
- **70dd44a 📌-3**：_append_dedup 对 dict 项 fuzzy dedup 弱——这是项目本身
  设计限制（dict → str 后做 substring 比较本就模糊），不是 helper 引入的问题。
- **70dd44a 📌-4**：parse_llm_json_response default=None 哨兵文档位置——
  nitpick。

## 验证

- `pytest tests/test_utils_helpers.py tests/test_alignment_smoke.py
  tests/test_alignment_stages.py` → 39 passed
- `pytest tests/test_cold_memory.py tests/invariants/test_mock_provider.py
  tests/invariants/test_engine.py tests/test_alignment_stages.py` → 146 passed

## 跳过项说明

- **9821c2e 📌-3**：test_invariants.py section 重复注释——纯编辑残留，
  commit diff 范围外，不在本次审阅的目标代码里。
- **70dd44a 🟢-2**：_is_fuzzy_dup window 默认值标注——文档建议，不改行为。
- **70dd44a 🟢-3**：_is_fuzzy_dup 空字符串处理——所有现有 caller 都有
  `if not s:` guard，加函数内 guard 会改变语义（"空不算 dup" vs "空算 dup"）。
  报告本身说"取决于语义选择"，暂无明确 winner，跳过。
- **70dd44a 🟢-4**：test_strip_fence_empty 拆分 None 和空串——nitpick。
- **70dd44a 📌-3**：_append_dedup 对 dict 项 fuzzy dedup 弱——这是项目本身
  设计限制（dict → str 后做 substring 比较本就模糊），不是 helper 引入的问题。
- **70dd44a 📌-4**：parse_llm_json_response default=None 哨兵文档位置——
  nitpick。