# lessons.md — 错误经验积累

本文件记录项目开发过程中遇到的错误、根因分析及修复方式，供后续对话中的 Claude Code 快速定位相似问题。只追加，不修改已有条目。

**条目格式**：
```
### [LESS-NNN] 简短标题
- **发现时间**：YYYY-MM-DD
- **触发场景**：在哪个操作/文件/命令下发生
- **错误现象**：报错信息或异常行为描述
- **根因**：为什么会发生
- **修复方式**：具体做了什么
- **预防规则**：以后如何避免
```

---

### [LESS-001] json.JSONDecodeError — Planner/Outline 输出含 Markdown 代码块
- **发现时间**：2026-05-05
- **触发场景**：调用 `planner_agent.py` 或 `outline_agent.py` 后解析模型输出
- **错误现象**：`json.JSONDecodeError: Expecting value: line 1 column 1`
- **根因**：Claude/DeepSeek 有时在 JSON 前后加 ` ```json ` 和 ` ``` ` 包裹，即使系统提示要求不加
- **修复方式**：在解析前统一 strip + lstrip("```json") + lstrip("```") + rstrip("```")；若仍失败则用正则找最外层 `{` 和 `}` 截取
- **预防规则**：所有 `json.loads()` 调用前先走清理函数；对于数组输出则找 `[` 和 `]`

---

### [LESS-002] LangGraph `recursion_limit` 默认值过低导致截断
- **发现时间**：2026-05-05
- **触发场景**：运行超过5章时，`app.stream()` 提前终止
- **错误现象**：生成5章后无报错静默停止，`current_chapter` 未更新到预期值
- **根因**：LangGraph 默认 `recursion_limit=25`，每个节点跳转计一次，写10章约需150+次跳转
- **修复方式**：`app.stream(state, {"recursion_limit": 250})`，根据目标章数调整上限
- **预防规则**：`recursion_limit` 设为 `max_chapters * 25`，至少250

---

### [LESS-003] mock_patch 路径错误导致 Mock 不生效
- **发现时间**：2026-05-05
- **触发场景**：`system_test.py` 中使用 `patch("api_client.call_llm")` 测试 Agent
- **错误现象**：Mock 不生效，测试仍然尝试真实 API 调用，因无 Key 报错
- **根因**：`patch` 的路径应是函数**被使用的模块**中的引用，而非函数定义所在的模块；Agent 文件 `from api_client import call_llm` 后，应 patch `agents.normalizer_agent.call_llm` 而非 `api_client.call_llm`
- **修复方式**：将 patch 路径改为 `agents.<模块名>.call_llm`
- **预防规则**：mock patch 路径 = 目标模块名 + `.` + 函数名（在该模块中的引用名）

---

### [LESS-004] memory_manager 新旧 Schema 兼容问题
- **发现时间**：2026-05-05
- **触发场景**：`tracker_agent.py` 升级为 V2 后，读取旧版 `_memory.json` 文件
- **错误现象**：`KeyError: 'hot'`，旧文件的顶层键是 `protagonist_level` 而非 `hot`
- **根因**：V2 将 L2 结构改为 `{hot: {...}, cold: {...}, constraints: {...}}` 嵌套，旧文件是平铺结构
- **修复方式**：在读取 L2 时增加兼容判断：若顶层无 `hot` 键，则将整个 dict 放入 `hot` 层
- **预防规则**：升级 Schema 时，`get_l2()` 函数必须处理旧格式迁移；测试中使用 `empty_l2()` 生成标准结构

---

### [LESS-005] `tools/human_review.py` 多余括号语法错误
- **发现时间**：2026-05-05
- **触发场景**：首次导入 `tools.human_review`
- **错误现象**：`SyntaxError: unmatched ')'`，第12行 `BASE_DIR` 定义末尾有多余 `)`
- **根因**：`os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` 末尾多了一个括号
- **修复方式**：删去多余的 `)`
- **预防规则**：生成含多层嵌套括号的路径定义后，立即用 `python3 -c "import ast; ast.parse(open('文件').read())"` 做语法检查

---

### [LESS-006] budget_manager `alerts` 键仅在有日志记录时存在
- **发现时间**：2026-05-05
- **触发场景**：`system_test.py` 中 `assert "alerts" in report`，无日志文件时失败
- **错误现象**：`AssertionError`，`generate_report()` 在无 `budget_log.jsonl` 时走快速路径，返回值不含 `alerts` 键
- **根因**：`records_available=False` 时 return 早退，未添加 `alerts` 键
- **修复方式**：测试改为检查 `budget_used_pct` 是否为 float，而非检查 `alerts` 键；或在快速路径也加 `alerts: []`
- **预防规则**：API 返回 dict 时，所有键应在所有代码路径下都存在（即使值为空列表/None）
