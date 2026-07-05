# CHANGELOG

本文档按时间倒序记录项目的所有重要变更。commit hash 是稳定锚点。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased] — 2026-07-05

### Bug Fix（迭代 #78 — 内部审计 / CLI 同型扫描）

- **`fix(engine): exporter.py + calibrate_checker.py 不再静默吞异常**（继 #73/#77 之后同型扫描补漏）
  - `engine/tools/exporter.py` 之前 5 处 `except Exception: pass/continue` + 2 处 dead try/except
    - 损坏章节 meta / setting_package / orchestrator_state 文件时静默返回默认 `{}`，
      exporter 拿不到 meta 但不知情
    - 2 处外层 try/except 因为内层 `load_meta` 已 silent pass 后永不触发 → dead code
  - `engine/tools/calibrate_checker.py` 之前 1 处 `except Exception: continue`
    - 校准样本 JSON 损坏时静默跳过
  - **修法**：两个文件都加 module logger + 全部 6 处 except 加 `_log.exception(...)` +
    删 exporter.py 2 处 dead try/except（comment 解释 load_meta 已处理）
  - **加 6 个 invariant test 锁死**（`TestExporterAndCalibrateNoSilentException`）：
    含 exporter.load_meta 行为测试——坏 meta 文件 → 返回 `{}` + caplog 抓到坏文件路径

### Bug Fix（迭代 #77 — 内部审计 / CLI 同型扫描）

- **`fix(engine): style_manager.py 不再静默吞异常**（继 #73 之后同型扫描补漏）
  - `engine/tools/style_manager.py` 之前 4 处 `except Exception: continue` 完全静默吞
    读风格样本 / chapter meta 失败
  - 跟 #73 memory/manager.py 完全同型，但 CLI 工具 — 被同型扫描漏掉
  - **修法**：模块级 `_log` + 4 处都加 `_log.exception(...)` 后 continue
  - **加 5 个 invariant test 锁死**（`TestStyleManagerNoSilentException`）：含行为测试
    —— 坏 chapter meta + 好 meta+章节，验证好样本被提取 + caplog 抓到坏文件路径

### Bug Fix（迭代 #76 — 内部审计 / 低）

- **`fix(engine): router.py proxy mount 失败时 log.warning 不再静默吞掉**
  - `engine/llm/router.py._get_proxied_client` 内层 mount proxy 段之前是
    `except Exception: pass` —— 如果 urlparse 抛异常（畸形 base_url），proxy
    默默不挂载 → caller 看到 "no proxy" 直连，但 provider_proxy 配置了；
    运维以为是网络问题实际是代码 bug
  - **修法**：`log.warning` 带 provider / base_url / exception 类型
    让运维快速定位。client 仍返回（mount 失败时仍能直连），行为不变但有诊断信号
  - **加 2 个 invariant test 锁死**（`TestRouterProxyMountNoSilentException`）：
    源码扫描 except 段必须有 log.* 调用 + 反向保证不能退回 bare pass

### Bug Fix（迭代 #75 — 内部审计 / 文档）

- **`fix(engine): agents/__init__.py 不再误导性引用已删除的 stub.py**
  - 之前注释 "legacy stub.py is kept as a fallback" 指向一个**已经不存在**的模块
    （commit 历史删了 stub.py），留下误导性引用
  - 开发和审计读起来以为有兜底实现，实际 ImportError 直传上层（fail-fast，
    符合 #62 系列修法）
  - **修法**：注释改为准确描述（所有 agent 都是真实实现，无 stub 兜底），
    enumerate planner 和 init_arc 也加上说明
  - **加 3 个 invariant test 锁死**（`TestAgentsPackageDocAccurate`）：
    AST 扫描禁止 stub 模块被加回来 + 文件不存在性检查

### Bug Fix（迭代 #72 — 内部审计 / 严重）

- **`fix(app): get_master_key 同进程稳定（修 in-process key 漂移致命 bug）**
  - **症状**：dev 模式（`MASTER_KEY` 环境变量未设置）下，`get_master_key()`
    每次调用都会生成**新的**随机 Fernet key，导致：
    ```python
    ciphertext = encrypt_api_key('sk-test')     # 用 key_K1
    decrypt_api_key(ciphertext)                  # 用 key_K2 ≠ K1
    # → ValueError "api_key 解密失败（可能是 MASTER_KEY 变了）"
    ```
  - 也就是说 **README 承诺的 "dev 模式不设 MASTER_KEY 也能跑（至少同进程内稳定）" 是不成立的**——一走 "写入 Provider→读取/解密" 这条最基本路径就立刻报错。
  - **祸根**：`get_master_key()` 没有模块级缓存，每次 `encrypt`/`decrypt` 都重新走 "env 没设 → 生成新 key" 分支。`tests/test_invariants.py:1487` 周围甚至留了注释"测试用稳定 key（避免 get_master_key 拿到临时 key）"——写测试的人已经发现这个不稳定性，但选择绕开而非修复，让 bug 一直活到现在。
  - **修法**：模块级 `_dev_master_key` 缓存 + 新增 `reset_master_key_cache()` 公开 API。dev 分支首次生成后复用；env 路径不走缓存（每次重新读 env，作为 source-of-truth，让测试 monkeypatch 切 env 立刻生效）。
  - **加 6 个 invariant test 锁死**（`TestMasterKeyStableAcrossCalls`）：含审计报告里那条 **复现脚本** 的反向测试——dev 模式同进程 encrypt→decrypt 必须成功；env 路径切换立刻生效；`reset_master_key_cache()` 公开 API 可调；源码必须有 `_dev_master_key` 缓存标志。

### Bug Fix（迭代 #73 — 内部审计 / 同型扫描补漏）

- **`fix(engine): memory/manager.py 不再静默吞异常**（CHANGELOG 多次"except Exception → log+fail-fast"模式的补漏）
  - CHANGELOG 里 60+ 次修复都用了同型扫描，但 `engine/memory/manager.py` 被漏扫到——文件里有 **4 处**  `except Exception: continue/pass` 静默吞：
    1. `_get_internal_samples` 读章节 meta 失败（L246）
    2. `_get_internal_samples` 读章节正文失败（L258）
    3. `_get_external_samples` 读风格样本失败（L276）
    4. `maybe_update_style_samples` 清理旧 auto 文件失败（L301）
  - **影响**：记忆/风格样本文件损坏时 Writer 上下文**悄悄变少**，无任何信号告诉运维"为什么最近几章好像不太连贯"。同目录下 `writer.py` 的多处同型 except 已经规范化为 `log.exception(...)` + 降级——确认这个文件是被同型扫描漏掉的，不是没扫。
  - **修法**：模块级 `_log = logging.getLogger("novel_ai.engine.memory.manager")` + 4 处都改成 `_log.exception(...)` 后继续（行为不变，但有诊断信号）。
  - **加 6 个 invariant test 锁死**（`TestMemoryManagerNoSilentException`）：含**行为测试**——写入坏 meta 文件 + 好 meta+章节，验证 `_get_internal_samples()` 仍返回好样本（continue 行为保留）且 caplog 能抓到坏文件路径的 error 记录（之前是被吞掉的）。

### Bug Fix（迭代 #71 — 内部审计）

- **`fix(engine): graph.planner 写完 setting_package.json 显式 invalidate cache**
  - 兜底 `_setting()` mtime 检测的 1 秒精度风险（同一秒内多次写文件 mtime 不变 → cache 漏刷新）
  - `run_graph_task` 里 elif `command == "planner":` 分支写完立刻 `invalidate_setting_cache()`
  - 1 个 invariant test 锁死 planner 分支必须调此 helper

### Bug Fix（迭代 #70 — 内部审计）

- **`fix(engine): orchestrator._setting stat 失败可观测**
  - 之前 `try: mtime = ...stat() except OSError: return cache` 静默 fallback
  - 修法：`log.warning("_setting: stat(%s) failed (%s); falling back")` 让运维知道
  - 1 个 invariant test 锁死（行为测试 + monkeypatch `Path.stat` 抛 OSError + caplog 抓 WARNING）

### Bug Fix（迭代 #69 — 内部审计）

- **`fix(engine): orchestrator._setting 返回 copy 而非内部 cache 引用**
  - 之前返回 `_setting_cache` 直接给调用方，调用方修改会**污染全局缓存**
  - 之前测试用 `assert second is first` 反而**鼓励了**这种危险 identity pattern
  - 修法：返回 `dict(_setting_cache)` 副本；同时把断言改成 value equality
  - 1 个 invariant test 锁死（mutation 必须不影响下次读取）

### Bug Fix（迭代 #68 — 内部审计）

- **`fix(engine): save_state 用 timezone.utc 而非 naive datetime**
  - 之前 `datetime.now()` 没带 timezone → 跨时区/容器部署时 last_updated 时间含义歧义
  - 修法：`datetime.now(timezone.utc).isoformat()`，顶层 import 复用（去掉函数内冗余 import）
  - **重复 invariant test 加严**：源码扫描不再写死 `r"datetime\.now\(\)"`（会被注释里"naive datetime.now()"文本误判）——改成先剥离注释+docstring 再匹配 `r"datetime\.now\(\s*\)"`

### Bug Fix（迭代 #67 — 内部审计）

- **`fix(engine): save_state 用 atomic_write_json 替代手写 .tmp + rename**
  - 跟 state.py 里其他几处（memory/manager.py 等）共用 `utils.atomic_write_json`
  - 减少重复代码 + 跨平台行为一致
  - 旧 .lock + placeholder byte hack 保留（Windows msvcrt 短时锁）

## [Unreleased] — 2026-07-04

### Bug Fix（迭代 #65 — 内部审计）
- **`fix(engine): orchestrator._setting 缓存按 mtime 自动 invalidate**
  - 之前 cache 一旦填就永不刷新 — 同一进程里跑 planner 后
    setting_package.json 更新了，orchestrator 还用旧值（arc_plans / title）
  - 修法：缓存同时存 _setting_mtime，每次 _setting() 检查文件 mtime —
    变了就重新 load。文件不存在时清空 cache（如果后来创建能立刻读到）
  - 模块级 `invalidate_setting_cache()` helper 用于测试或手动 invalidate
  - 加 3 个 invariant test 锁死：文件改 → reload / 同 mtime → cache hit /
    invalidate_setting_cache 必须重置两个状态

### Bug Fix（迭代 #62 — 内部审计）
- **`fix(app): llm_client.py IndexError/TypeError 不再跳出重试循环**
  - `app/llm_client.py:71` 之前 catch 只到 KeyError
  - LLM 返回 `{"choices": []}` → IndexError → 跳出重试 → LLMError
    把最后一次 IndexError 暴露给上层
  - LLM 返回 `{"choices": [{"message": null}]}` → None["content"] → TypeError 同型
  - 这两个都是真实场景（rate limit fallback / 模型降级 / truncated stream）
  - 修法：扩 catch 列表到 `(IndexError, TypeError)`

### Bug Fix（迭代 #60 — 内部审计）
- **`fix(engine): orchestrator.run_summarizer 异常不再静默（跟 #58 同型）**
  - 之前 `except Exception: cost=0.0` 静默 fallback
  - 修法：log error_log + 标 arc_plan._summarizer_failed

### Bug Fix（迭代 #59 — 内部审计）
- **`fix(engine): human_review.py atomic write + load_state 不再 silent fallback**
  - human_review.py save_state 用 raw open(w) 写 orchestrator_state.json
  - load_state 损坏时 `except Exception: pass` → 返回 `{}` →
    人工审核看到空 state 却不知道文件坏了 → 假审核
  - 修法：atomic_write_json + 损坏时 backup 到 .corrupted.{ts} 后 raise
    （跟 iter #36/#53 同型）

### Bug Fix（迭代 #58 — 内部审计）
- **`fix(engine): orchestrator.run_tracker 异常不再静默**
  - orchestrator.node_save_and_track 之前 `except Exception` 静默兜底
    `updated_mem=memory, cost=0` —— tracker LLM 失败时没信号
  - 修法：标 `task._tracker_failed=True` + `error_log` 增量

### Bug Fix（迭代 #57 — scripts/ 原子写扫描）
- **`fix(scripts): rewrite_length.py meta.json 改用 atomic_write_json**
  - 跟 iter #43/#49/#55/#56 同型 — 搜索所有 `with open(...w...); json.dump(...)`
    一次性扫完

### Bug Fix（迭代 #56 — scripts/ 原子写扫描）
- **`fix(scripts): export_openapi.py 改用 atomic_write_json**
  - 之前 `out_path.write_text(json.dumps(spec, ...))` 非 atomic
  - openapi.json 是 CI 校验漂移的基准，半写损坏会掩盖真实漂移

### Bug Fix（迭代 #55 — scripts/ 死代码 + 原子写）
- **`fix(scripts): monitor_run.py `if False` 死代码 + 报告 atomic_write**
  - line 197: `db.query(...).count() if False else 0` —— db 已关的死代码，
    initial_chapter_count 永远 0
  - line 282: report_path.write_text(json.dumps(...)) 非 atomic
  - 修法：把 db 查询移到 db 还开着时；atomic_write_json 写报告

### Bug Fix（迭代 #54 — 内部审计）
- **`fix(api): _drain_stdout daemon 线程异常不再静默死**
  - `_drain_stdout` 是 daemon 线程，之前 try/finally 但没有 except
  - 循环里 DB 错误 / KeyError 会让线程静默死掉，bridge_run.status
    卡在 "running"，下次 /bridge/run 触发 409 Conflict
  - 修法：循环 body 包内层 try/except，异常时 bridge_run 标 failed +
    push error 事件到 queue（SSE consumer 能看到真实原因）
  - 加 3 个 invariant test：源码必须内层 try + 必须 push error 事件 +
    bridge.py 必须 import traceback

### Bug Fix（迭代 #53 — 内部审计）
- **`fix(engine): state 文件损坏不再静默 fallback（progress 丢失）**
  - `engine/graph.py:_load_state_for_project` 之前
    `except Exception: pass` 静默兜底 — state 文件损坏 → load_state 抛异常
    → except 吞掉 → 走 DB 路径 → create_initial_state 返回 fresh state
    → 用户 50 章进度静默丢失
  - 修法：损坏时 backup 文件到 `.corrupted.{ts}`（iter #36 同型），
    然后 raise 让 caller fail-fast — **不允许 silent fallback**
  - 加 3 个 invariant test 锁死：损坏必须 raise + 必须备份 .corrupted.* +
    源码不能再有 except Exception: pass

### Bug Fix（迭代 #52 — 内部审计）
- **`fix(app): config.py MiniMax 默认 endpoint 切到新版**
  - `app/config.py` 的 `minimax_api_base` 默认是旧版 endpoint
    `api.minimax.chat`（router.py iter #32 已切到 `api.minimaxi.com`）
  - 后果：用户没设 `NOVEL_MINIMAX_API_BASE` env 时，`app/llm_router.py`
    通过 `settings.minimax_api_base` 拿旧 endpoint → 调用 404 / 401
  - 修法：默认改为 `api.minimaxi.com`（新 endpoint）+ 默认 model
    改为 `MiniMax-M3`（跟 router.py 一致）
  - 加 3 个 invariant test 锁死

### Bug Fix（迭代 #51 — 内部审计）
- **`fix(engine): anthropic SDK proxy 之前不生效**
  - `_anthropic` 之前用 `Anthropic()` 直接调用，没传 `http_client`
  - 即使 `_PROVIDER_PROXY["anthropic"]` 配了，proxy 永远不生效
  - 后果：GFW 区域用户勾选 anthropic.needs_proxy + 设 ANTHROPIC_PROXY
    → anthropic API 直连 → 超时 / 失败
  - 修法：检测 `_PROVIDER_PROXY.get("anthropic")`，有就构造
    `httpx.Client(proxy=...)` 作为 `http_client` 参数传给 Anthropic SDK
  - 加 3 个 invariant test

### Bug Fix（迭代 #50 — 内部审计）
- **`fix(engine): budget_manager print_report KeyError when log empty**
  - generate_report 在 budget_log 为空时返回的 dict 缺少
    `total_chapters_planned` / `cost_per_chapter_recent20` / 等 key
  - print_report 直接 `report["total_chapters_planned"]` → KeyError
  - 后果：第一次启动 / 删 budget_log 后 → status/budget 命令 → 后端 500
    + traceback 给前端
  - 修法：generate_report 空 records 路径补 `total_chapters_planned` 字段；
    print_report 用 `.get()` 兜底

### Bug Fix（迭代 #49 — 内部审计 + AI 审查 §3.3 同型扫描）
- **`fix(engine): atomic_write_json 推广到剩余报告 JSON**
  - 跟 #43 同型 — 把 atomic_write_json 一次性推广到所有剩余的
    `with open(...w...); json.dump(...)` 写盘点：
    - budget_manager.print_report → budget_report.json
    - calibrate_checker → calibration_result.json
    - chapter_checker.scan_all_chapters → consistency_report.json
    - bootstrap.run_bootstrap → bootstrap_candidates.json
  - 加 5 个 invariant test 锁死

### Bug Fix（迭代 #48 — 内部审计）
- **`fix(engine): chapter_checker LLM 一致性 JSON 解析失败不再 fake-pass**
  - `chapter_checker.llm_consistency_check` 之前 parse 失败时
    返回 `{"has_issues": False}` — silent pass
    （同 compliance iter #41 / orchestrator iter #28 fake-pass 同型）
  - 后果：LLM 检测到的跨章节矛盾（人物等级跳变 / 道具未获得 /
    时间线错乱）JSON 解析失败 → 报告「无问题」 → 错误积累
  - 修法：parse 失败时 has_issues=True + issues 加 "解析失败" + _parse_failed=True

### Bug Fix（迭代 #47 — 内部审计）
- **`fix(engine): summarizer JSON parse 失败 log warning + placeholder 标记**
  - `summarizer.summarize_arc` 之前 parse 失败时静默写 placeholder 到
    L5.arc_summaries，没有 log warning 让运维知道
  - 修法：log.warning(resp[:200]) + placeholder dict 加 _parse_failed=True
    标记，让 UI / 后续审计能识别哪些 arc 的 placeholder

### Bug Fix（迭代 #46 — 内部审计）
- **`fix(engine): _get_proxied_client 永远 fallback 到无代理 client**
  - `_get_proxied_client(provider, ...)` 之前读
    `_proxy_mounts.get(provider)` 期望拿到 URL 字符串，但
    `_proxy_mounts` 实际是 `dict[str, httpx.Client]`（client 缓存）
  - 真 URL 在 `_PROVIDER_PROXY`（由 set_proxy_map 写入）
  - 后果：用户勾选 Provider.needs_proxy + 设 DEEPSEEK_PROXY env 后，
    deepseek / kimi / anthropic 等流量**不**走代理 —— GFW 区域用户
    无法调用海外 LLM，调试 1 小时以为是网络问题实际是代码 bug
  - 修法：从 `_PROVIDER_PROXY.get(provider)` 读 URL
  - 加 4 个 invariant test 锁死

### Simplify（迭代 #45 — 内部审计）
- **`refactor(engine): _call_with_budget 去重 + writer.py 单一 router 来源**
  - writer.py + rewriter.py 之前各有一份几乎相同的 `_call_with_budget`
    （~30 行：try/catch httpx 错误 + sleep(30) + retry 循环）。抽到
    `engine.utils.call_with_budget_with_retry(router, ..., sleep_seconds=30,
    max_attempts=2)` 共享。
  - 副作用：writer.py 之前自己有 `_ACTIVE_ROUTER` 模块状态 + `set_active_router`
    + `_get_router`（跟 rewriter.py 各自存一份），删掉，统一从
    `engine.llm_router.get_active_router()` 读。engine/agents/__init__.py
    里 `set_writer_router` 别名也跟着删。
  - 收益：~30 行重复 → 1 个工具函数 + 2 个薄包装；多份 router state → 单一来源
    （避免 drift：之前 8 个 agent 模块各存一份 _ACTIVE_ROUTER，谁先谁后更新
    完全靠 import 顺序）。
  - 加 9 个 invariant test 锁死：utils 导出 + 参数签名 + writer/rewriter 用共享
    helper + writer 无私有 router 状态 + retry 行为（first success / retry /
    exhaust attempts raise）。

### Frontend（迭代 #44 — 内部审计）
- **`fix(frontend): 4 处 silent-swallow + Provider 类型安全 + JSON 错误可读**
  - **BridgeConsole.tsx**: 3 处 `.catch(() => setChapters([]))` / `.catch(() => {})`
    改为 `toast.error` / `toast.warn`（listChapters 失败原因：project_id 不存在
    / 权限 / DB 锁等）。
  - **RuleCenter.tsx**: `getProject` 404 之前静默吞，改为 `toast.error`。
  - **types.ts**: `Provider` 类型**移除** `api_key?: string | null` 字段
    （API 绝不返回明文，类型允许会误导 spread 误清空）。新增 `ProviderForm`
    （表单用）和 `ProviderCreate`（API 请求用）两个明确类型。
  - **api/client.ts**: `request()` 的 `JSON.parse` 失败不再透出原始 SyntaxError，
    改为「响应不是有效 JSON (path): <err> | body[:200]=...」让用户看到
    HTML 错误页 / 半写文件 / proxy 拦截的真实原因。
  - **Providers.tsx**: 同步类型重构（payload 用 `ProviderCreate`，不再依赖
    `Omit<Provider, 'id'>`）。

### Bug Fix（迭代 #43 — 内部审计）
- **`fix(engine): atomic_write_json 全局推广（5 个 critical 写盘点）**
  - 之前 `engine/state.py:save_state` 已 atomic、`engine/memory/manager.py:save_l2/save_l5`
    跟 `engine/agents/planner.py` 已 atomic，但 **其他 critical 写盘点仍是 raw
    open(w)+json.dump**，同样的「半写损坏→静默覆盖→数据丢失」风险：
    1. `engine/orchestrator.save_chapter` → `ch_NNNN_meta.json` (chapter meta)
    2. `engine/orchestrator.node_load_arc_tasks` → `arc_N_tasks.json` (task sheet，
       是 chapter_task_queue 的磁盘镜像，损坏 → 整次 run 启动失败)
    3. `app/bridge/setting_sync.push_concept` → `novel_config.json` (用户 concept)
    4. `app/bridge/reports.apply_review` → `orchestrator_state.json` (bridge state)
    5. `engine/tools/bootstrap` → `ch_NNNN_meta.json` (x2)
  - 修法：5 个点全部改用 `engine.utils.atomic_write_json`（iter #39 公共工具），
    一次性扫完所有 critical JSON 写入点。
  - 这次审计的核心教训（独立审查 §3.3）：发现某类 bug 时值得**顺手搜一遍
    代码库里其他同构调用点**，一次性修完，而不是分好几轮迭代才补齐。
  - 加 7 个 invariant test 锁死（6 个 source-level + 1 runtime）。

### Tests（持续加固）
- **`fix(security): NOVEL_PRODUCTION MASTER_KEY 强制检查测试补齐**
  - `app/main.py:_check_master_key_in_production`（独立审查 §3.2 提到的高危点）
    **代码已存在 + 已在 lifespan 调用**，但缺测试。本轮补 5 个 invariant test：
    - 源码必须有 NOVEL_PRODUCTION env 检查
    - 必须在 lifespan 调用（启动时 fail-fast）
    - 必须在 run_migrations **之前**调用（否则先读到 api_key_encrypted
      → decrypt 失败）
    - production + no MASTER_KEY → RuntimeError
    - dev mode + no MASTER_KEY → 不抛（warn 但继续）

### Bug Fix（迭代 #42 — 内部审计）
- **`fix(engine): init_arc setting_package.json 损坏返回清晰错误**
  - `engine/agents/init_arc.py:21` 之前直接 `json.loads(raw read)`——
    setting_package.json 损坏时原始 `JSONDecodeError` / `UnicodeDecodeError`
    透出抛 RuntimeError + 几百行 traceback 给用户。
  - 跟 pull_setting_package（迭代 #35）同型问题，同样的修法。
  - 修法：catch 两个异常转抛 RuntimeError 带可读信息（"setting_package.json
    损坏请重新跑 planner"）。
  - 加 2 个 invariant test 锁死：源码必须 catch 两个异常；损坏文件
    必须抛 RuntimeError。

### Bug Fix（迭代 #41 — 内部审计）
- **`fix(engine): compliance LLM JSON 解析失败不能再 fake-pass**
  - `engine/agents/compliance.py:llm_semantic_check` 之前
    `except Exception: result = {"passed": True, "hard_rejects": [], ...}`——
    **fake-pass** 同型问题（iter #28 / #32 / #37）。
  - 后果：LLM 检测到 hard reject（「未成年人性暗示」「详细血腥描写」等
    关键词扫描抓不到的语义违规）→ JSON 解析失败 → 所有 hard_rejects
    丢失 → passed=True → 违规内容落盘 → 平台审查删书。
  - 修法：保守策略——parse 失败时设 passed=False + hard_rejects 里加
    `PARSE_ERROR` 条目，suggestion 给用户可读 hint「请重跑合规检查」。
    `run_compliance` 第 127 行会基于 hard_rejects 重算 passed，
    parse 失败的 PARSE_ERROR 会让 passed 保持 False。
  - 加 3 个 invariant test 锁死：parse 失败 → passed=False + PARSE_ERROR；
    run_compliance 透传；源码不能再有 `except Exception → passed=True`。

### Bug Fix（迭代 #40 — 内部审计）
- **`fix(engine): tracker LLM JSON 解析失败不能再静默丢数据**
  - `engine/agents/tracker.py:83` 之前 `parse_llm_json_response(resp, {})`——
    parse 失败时返回 `{}`，下游所有 `updates.get(...)` 是空 list / 空 dict，
    `chapter_summary` / `world_events` / `constraints` / `foreshadowing` **全部
    静默丢失**。
  - 后果：50 章跑完 `meta.total_chapters_tracked=50` 但
    `recent_summaries=[]`、`world_events=[]`、`character_states={}`——
    writer 拿到的 memory 永远是「第 0 章状态」，文章脱节但没有任何
    错误信号。
  - 修法：`parse_llm_json_response(resp, None)` + 检测 None；
    parse 失败时 log warning + 在 meta 里写
    `last_tracker_parse_failure_chapter` + `tracker_parse_failure_count`
    （不静默丢失信号，UI 可以从 meta 看到哪几章 tracker 失败）。
  - 配合：engine/utils.py `_coerce_type` 增加 `default=None` 哨兵分支——
    让调用方能用 None 区分「parse 失败」vs「合法空 dict」。
  - 加 3 个 invariant test 锁死：源码必须用 None（不是 {}）；
    parse 失败 → log warning + meta 标记；正常路径 meta 不应出现
    失败标记。

### Bug Fix（迭代 #39 — 内部审计）
- **`fix(engine): planner setting_package.json 改用 atomic_write**
  - `engine/agents/planner.py:198-199` 之前直接 `open(out_path, "w")`
    写 setting_package.json——写一半进程被杀 → 文件损坏 → 后续
    `pull_setting` 失败 → 5 张表全空（**Phase 1 真实事故源头**）。
  - 跟 save_l2（迭代 #36）同型问题，**比 save_l2 更危险**：setting_package.json
    是全书唯一来源（力量体系 / 弧结构 / 角色口癖 / 伏笔种子），损坏后
    没有 backup 路径重建，只能重新跑 planner。
  - 修法：把 `engine/memory/manager.py` 的私有 `_atomic_write_json` 提到
    `engine/utils.py` 当公共 `atomic_write_json`（复用 `engine.state.save_state`
    的 .tmp + os.replace 模式），planner.py 改用公共版本。
  - memory/manager.py 同时去掉自己的私有定义，统一从 utils 导入。
  - 加 5 个 invariant test 锁死：utils 必须暴露 atomic_write_json；
    planner 必须 import + 不能用 raw open(w)；实际写盘 round-trip；
    memory/manager 必须 import 公共版本 + 不能自己 `def`。

### Bug Fix（迭代 #38 — 内部审计）
- **`fix(engine): llm_router 静默吞 decrypt 错误要 log warning**
  - `engine/llm_router.py:load_routes` 之前 `except Exception` 静默吞
    Provider.api_key_decrypt 错误（MASTER_KEY 变了 → key=""），无 log。
  - 后果：用户改 MASTER_KEY env 后所有 LLM 不可用，错误日志里没任何线索，
    排查只能从 DB 翻 Provider.api_key_encrypted 自己 decode。
  - 修法：log warning（带 provider id + role_key + 错误类型）让运维知道。
    仍设 key=""（不阻断 load_routes，但下游 LLM 调用会失败可追到原因）。
  - 加 2 个 invariant test 锁死：mock decrypt 抛异常 → 必须 log warning；
    源码必须 log.warning。

### Bug Fix（迭代 #37 — 内部审计）
- **`fix(api): rules post-process LLM 失败不能再 fake-pass**
  - `app/api/rules.py:_llm_call_for_postprocess` 之前 `except Exception`
    返回占位文本（"[tool] LLM 调用失败..."）+ cost=0。
  - 后果：前端收到占位 + cost=0，误以为"逻辑评估/毒舌查漏/去 AI 痕迹 完成"
    实际 LLM 失败。用户拿到的是空壳，没有真评估。
  - 修法：改为 `raise HTTPException(503, "LLM 调用失败...")`，
    让用户/前端能区分"成功完成"和"LLM 不可用"。
  - 加 2 个 invariant test 锁死：mock LLM 抛异常 → 必须 503；
    源码必须 raise HTTPException 不能 return 占位。

### Bug Fix（迭代 #36 — 内部审计）
- **`fix(engine): save_l2 / save_l5 atomic write + 损坏文件备份**
  - `engine/memory/manager.py` save_l2 / save_l5 之前直接 `open(path, "w")`
    写一半进程被杀 → 文件损坏 → get_l2 / get_l5 静默返回 empty
    → 下次 save 覆盖空数据 → **L2/L5 记忆永久丢失**。
  - 跟 `engine.state.save_state` 同样的 atomic write 模式：
    1. 写 `.tmp` + `os.replace`（原子重命名，Windows 上重试 3 次）
    2. `fsync` 强制落盘（best-effort）
  - `get_l2` / `get_l5` 损坏文件不再静默 fallback，而是备份为
    `.corrupted.{ts}` 后再返回 default（让用户能事后取回数据）。
  - 加 5 个 invariant test 锁死：源码必须 atomic write / 必须备份损坏
    文件 / save→get round-trip 数据不丢。

### Bug Fix（迭代 #35 — 内部审计）
- **`fix(bridge): pull_setting_package JSON 错误返回清晰信息**
  - `app/bridge/setting_sync.py` 之前损坏的 setting_package.json
    让原始 `JSONDecodeError` / `UnicodeDecodeError` 透出到 API 层 → 500 +
    几百行 Python traceback 暴露给前端。
  - 修法：catch (json.JSONDecodeError, UnicodeDecodeError) 转抛
    ValueError 带用户可读信息（"文件损坏请重新跑 planner"）。
  - 加 3 个 invariant test 锁死：损坏 JSON → ValueError；非 UTF-8 编码
    → ValueError；源码必须 catch 两个异常。

### Bug Fix（迭代 #34 — 内部审计）
- **`fix(engine): export_chapters 单章坏不能阻断整批导出**
  - `engine/tools/exporter.py` 之前单章坏让整个 export 失败：
    encoding 错 / meta 损坏 / OSError → 整批抛异常，**之前已写好的
    chapters 也没保存**。
  - 跟 import_chapters 是同型问题（迭代 #31），同样的修法。
  - 修法：每章独立 try/except，log warning + `continue` 跳过该章。
    同样修 `print_stats`（stats 视图同样需要单章坏不阻断）。
  - 加 3 个 invariant test 锁死：源码必须有 try/except + continue，
    正常文件场景跑通返回正确结果。

### Bug Fix（迭代 #33 — 内部审计）
- **`fix(api): SSE queue 内存泄漏**
  - `_run_queues` (bridge.py) 和 `_job_queues` (worldbuild/orchestrator.py)
    之前只创建 queue 从不清理。SSE consumer 读完 done 事件后 dict 里的
    queue 永远不被移除。
  - 后果：生产长期跑 N 个 run 后 dict 里堆 N 个 Queue + 内部 buffer，
    内存持续涨。重启后释放，但长跑进程会逐渐 OOM。
  - 修法：SSE consumer 退出（break / 异常 / 客户端断开）时通过
    `try/finally` 调 `cleanup_*_queue`，从 dict 移除 queue。
  - 加 5 个 invariant test 锁死：consumer 读 done 后 dict 被清理、
    重复清理幂等、event_generator 必须 try/finally 包裹。

### Bug Fix（迭代 #32 — 内部审计）
- **`fix(engine): MiniMax M3 reasoning_content 检测（避免静默空文本）**
  - `engine/llm/router.py:_minimax` 之前 line 456-458 对 reasoning_content
    存在但 content 为空的响应有死代码 fallback（重新赋 msg.get("content", "")
    还是空），导致 M3 思考模式被意外开启时静默返回空文本。
  - 后果：caller 拿到 "" 当成"正常生成" → 后续 checker 给空文本打 0 分
    PASS，save_and_track 落盘 0 字章节。
  - 触发场景：服务端配置变了 / 用户覆盖了 MINIMAX_BASE_URL 到旧版
    endpoint / 代理把 thinking 字段剥掉。
  - 修法：检测到 reasoning_content 非空 + content 空时显式 raise ValueError，
    让配置 bug 暴露而不是静默空文本污染下游。
  - 加 3 个 invariant test 锁死：reasoning_content + empty content → raise；
    正常 content → 正常返回；content 空 + 无 reasoning_content → 走兜底
    text 字段。

### Bug Fix（迭代 #31 — 内部审计）
- **`fix(bridge): import_chapters 单文件坏不能阻断整批**
  - `app/bridge/chapter_import.py` 之前一个坏文件就让整批 import 失败：
    - 文件名畸形（ch_xyz.txt 而不是 ch_0001.txt）→ IndexError
    - 编码错（Latin-1 而非 UTF-8）→ UnicodeDecodeError
    - meta.json 损坏 → JSONDecodeError
  - 后果：50 章里只要有 1 章坏 → import 抛异常 → 0 章导入，
    用户没法定位是哪个文件坏。
  - 修法：每文件独立 try/except，log warning + 跳过该文件继续下一个；
    同样修 `_force_reimport`。
  - 加 2 个 invariant test 锁死：3 个文件（1 正常 + 1 meta 坏 + 1 坏 filename）→
    正常文件被导入，整个 import 不抛异常。

### Bug Fix（迭代 #30 — 内部审计）
- **`fix(api): run_bridge 删除死锁代码（false sense of security）**
  - `app/api/bridge.py` 之前用 `_get_project_lock(project_id).locked()` 做
    "同 project 重复 run"并发保护，但该 `asyncio.Lock` 永不被 acquire
    （grep 证实无 `async with _get_project_lock`），检查永远 False
    → 给 false sense of security（代码看起来"有锁"但实际没有）。
  - 真实保护只有两层：
    1) DB 层 `BridgeRun.status='running'` 检查
    2) lifespan 启动时 `_recover_orphan_bridge_runs`（清理崩溃遗留）
  - 修法：删 `_project_locks` 字典 + `_get_project_lock()` 函数 + 调用点，
    注释说明 DB 层 + orphan recovery 是真实保护。
  - 副作用：tests/test_phase1_5_smoke.py 也 import 了已删的 `_get_project_lock`
    导致 collection error，顺手修：删 import + 删 asyncio.Lock 单测段（保留
    SQL 409 兜底测试）。
  - 加 2 个 invariant test 锁死：bridge.py 不应再定义/调用 _project_locks；
    run_bridge 真代码行不该有 .locked() 假并发检查。

### Bug Fix（迭代 #29 — 内部审计）
- **`fix(bridge): apply_review 静默 pop 错任务**
  - `app/bridge/reports.py:152-169` 之前 `_find_task_index` 在没匹配时
    fallback 到 0 — 用户提交 review with task_id="X" 但 X 不存在时，
    第一条 pending 被静默移除（数据完整性破坏）。
  - 后果：review_history 记的是 "X" 但实际 pop 的是另一条 task；
    用户以为"处理了 X"但 pending 列表里 task-A（不是 X）消失了。
  - 修法：_find_task_index 在没找到时显式返回 None（不 fallback）；
    apply_review 加 `matched` 字段告诉前端"是否匹配"，方便 UI 显示"未匹配"。
  - 加 3 个 invariant test 锁死：unmatched task_id/chapter_number 不 pop，
    matched task_id pop 对的任务。

### Bug Fix（迭代 #28 — 内部审计）
- **`fix(engine): node_rewrite post-rewrite compliance fake-pass**
  - `orchestrator.py:391-394` 之前当 `run_compliance`（post-rewrite）抛异常
    时兜底 `comp_result = {"passed": True}`，跟之前修过的 `node_write_pipeline`
    里的 compliance fake-pass 同型问题。
  - 后果：重写后即便合规检查完全失败（异常被吞），章节也走"通过"路径
    → 违规内容落盘 + checker 用 stale cr 可能误判 save。
  - 修法：跟 node_write_pipeline 对称 — 标记 `_compliance_check_failed=True`
    并提前 return；同时给 `route_after_rewrite` 加防御性检查（防止旧 cr
    分数遮蔽新失败标记）。
  - 加 4 个 invariant test 锁死：post-rewrite compliance 抛异常 → escalate。

- **`fix(engine): node_load_arc_tasks outline cost 双重计费**
  - `orchestrator.py:209` 之前在 try/except 之外多调一次 `_add_cost(state, cost)`，
    而每个分支（card / talk / batch）内部已经调过 → 实际计费 2 倍。
  - 后果：50 章跑下来 `budget_used_usd` 虚高 100%，超预算提前 escalate。
  - 修法：删掉 line 209 的重复调用，保留分支内部调用。
  - 加 4 个 invariant test 锁死：batch/card/talk 三种模式各只增一次，
    异常时不应计费。

### Bug Fix（独立 AI 审查发现）
- **`aa969a5` fix(engine): orchestrator human_escalation 边 → load_arc_tasks**
  - 独立 AI 深度审查（2026-07-03）发现：`orchestrator.py:573` 之前是
    `human_escalation → END`，与 `engine/graph.py:290` 不一致。
  - 后果：run/resume 章节触发人工介入 → stream() 立即终止 →
    chapters_done < max_chapters 但 exit_code=0（静默提前结束）。
  - 修法：把 orchestrator 边改成 load_arc_tasks，加 3 个 invariant test
    锁死两个文件的图拓扑必须一致。

### Tests（持续加固）
- 本轮新增 invariant test 类：TestMockProviderEndToEnd /
  TestOpenApiExport / TestMasterKeyRotation / TestOpenApiExportEndToEnd /
  TestMasterKeyScriptsEndToEnd / TestRotateMasterKeyEndToEnd /
  TestGraphCommandFailurePaths / TestSaveStateTrueConcurrency /
  TestBudgetManager / TestAuditProjectItself / TestMigrationsIdempotent /
  TestGetDbDependency / TestApplyReviewInputValidation /
  TestLoadStateRobustness / TestDocCodeConsistency /
  TestSecurityConstants / TestProviderTableSchema /
  TestHumanEscalationNotEndRun / 等
- 总 invariant suite：**228 passed / 0 warnings**

## [Unreleased] — 2026-07-02

### Security（高危）
- **`889a47e` fix(security): Provider API key 加密存储（Fernet + MASTER_KEY env）**
  - 历史背景：`Provider.api_key` 之前明文存 SQLite，DB 泄漏 = 全部供应商 key 曝光
  - 新增 `backend/app/security.py`（Fernet encrypt/decrypt + MASTER_KEY bootstrap）
  - 新增 `backend/app/migrations.py`（启动时 idempotent ALTER TABLE）
  - Schema：`api_key` 列已 DROP，新增 `api_key_encrypted`（ciphertext）+ `api_key_suffix`（明文后 4 位）
  - `ProviderOut` 不再返回明文，只返回 `api_key_set` + `api_key_suffix`
  - 前端 `Providers.tsx`：编辑时必须重新填 api_key（后端不返回明文，无法预填）
  - 部署前必设 `MASTER_KEY` env；脚本：`python -m scripts.generate_master_key`

- **`c8f764b` fix(main): lifespan handler + BridgeRun 孤儿自愈 + CORS 收紧**
  - 启动时清理孤儿 `BridgeRun.status="running" & finished_at IS NULL` 行（进程崩溃后无法再 409）
  - CORS 从 `*` 收紧为默认 `[http://localhost:5293]`，可通过 `ALLOWED_ORIGINS` env 覆盖
  - 弃用 `@app.on_event` → `@asynccontextmanager lifespan`

### Bug Fixes
- **`af3ddc4` fix(engine): llm_router 读 api_key_encrypted** — 之前读已删字段
- **`4f79ae4` fix(bridge): reports.py 路径统一** — 走 `NOVEL_AI_DIR` env，与 engine 一致
- **`2055746` fix(bridge): 清理 _run_bridge_async 死代码**
- **`e7b7215` fix(api): submitReview 字段对齐** — 前端 `edited_content` → `content`
- **`d503446` fix(ports): 统一 backend 端口 8123→8132**（README/dev.bat/docs/run_mvp 一并改）

### Chore（依赖升级 / 弃用清理）
- **`d618dd4` chore(deps): Pydantic class Config 迁 ConfigDict + datetime.utcnow() 弃用清理**
  - 7 处 `class Config: from_attributes=True` → `model_config = ConfigDict(...)`
  - 9 处 `datetime.utcnow()` → `datetime.now(timezone.utc)`
  - pytest warnings 从 15 降到 0

### Features
- **`bfd68cd` feat(engine): Mock LLM provider**
  - 不读任何 API key env，CI 不需要 secret
  - 每个 agent 给 schema 化 JSON 固定响应
  - writer 模拟 ~2000 字章节满足 `call_with_length_budget`

### Refactor
- **`9418791` refactor(engine): graph.py 日志统一** — 16 处 `capture.write("[engine] ...")` → `log.xxx(...)`

### Docs
- `README.md` 加「部署」章节（MASTER_KEY / CORS / 端口 / 迁移 / 范围外）
- `docs/superpowers/plans/2026-06-27-phase1-5-fusion-debugs.md` 标记 SUPERSEDED + commit 索引

### Tests（invariant suite）
`pytest tests/` 从 22 → **96 passed**，0 warnings。新增关键测试类：
- `TestFrontendBackendPortConsistency`（5）— 端口硬编码锁死
- `TestReviewContract`（3）— submitReview schema 一致
- `TestBridgeDeadCodeRemoved`（2）— `_run_bridge_async_imported` 不再出现
- `TestOrphanBridgeRunRecovery`（5）— lifespan cleanup 真测
- `TestReportsPathUnified`（3）— `NOVEL_AI_DIR` env 解析
- `TestProviderApiKeyEncrypted`（7）— 明文不入库 + API 不暴露
- `TestMockLLMProvider`（4）— mock provider 离线可用
- `TestEngineLoggingUnified`（2）— `[engine] capture.write` 已清零
- `TestFrontendTypesAligned`（2）— BridgeRun + ChapterFull 类型
- `TestDeploymentDocs`（2）— MASTER_KEY 脚本 + README 部署章节
- 还有 `TestParseLLMJsonResponseTypeGuard`（7）、`TestTrackerUsesParseWithDictDefault`（3）、`TestSaveStateUpdatesLastUpdated`（2）等

---

## 2026-07-01 — Phase 1.5 收尾 + 12 commit 修复链

| Commit | 类型 | 标题 |
|---|---|---|
| `62baf44` | bug | run 进程走 subprocess（uvicorn 重启不杀 in-flight run） |
| `dd1e14a` | bug | writer / rewriter 网络异常重试一次 |
| `e4eaca1` | bug | orchestrator 全 pipeline 异常走 escalate（5 处 fake-pass） |
| `08a8f02` | bug | state 路径统一 NOVEL_AI_DIR env |
| `5d1f83e` | bug | writer 失败不再写 `[writer-stub]` 假 PASS |
| `17a20fc` | fix | parse None 注释 / monitor 文档 / 测试不依赖活服务 |
| `936f58d` | chore | 删 FUSION_BUILD_SPEC.md 死文档 |
| `33a5c09` | bug | save_state 自动更新 last_updated |
| `48870c6` | feat | monitor_run.py 后台监控脚本 |
| `af8f073` | bug | parse_llm_json_response 类型保护（tracker bug 根因） |
| `3278a77` | bug | 前端端口 8123→8132 |
| `bdff57a` | bug | graph.stream 必须传 thread_id（17 小时静默失败的根因） |

---

## 2026-06-28 — Phase 1 融合

| Commit | 标题 |
|---|---|
| `a481006` | feat: schema-driven contracts + audit + invariant tests (5 root-cause fixes) |
| `efd6345` | fix: chapter-entity backfill + junk-header strip + v3 guide (#2) |
| `8955017` | Merge pull request #1 |
| `82865ea` | fix(bridge): worldbuild data + chapter titles + persistent logs |
| `9ad873e` | fix(engine): unblock 50-chapter run + planner/init_arc shortcuts |
| `cb73b3c` | fix(frontend): Dashboard / WorldBuild 错误态加显式提示 |
| `58b9a3a` | chore: 删 docs/ 旧版 html |
| `4a3cef3` | feat(frontend): 设计系统升级 — 高级优雅 · 工业曲线 · 微交互 |
| `d93a3d0` | feat: 补齐 4 处 review 缺口 |
| `dea9f59` | feat(api+ui): 补齐前后端缺失接口 |
| `0ca95a0` | feat(engine): P2/P3 完成 — 8 agents 真实实现 + L2/L5 记忆 + SqliteSaver + 10 tools |
| `e24223b` | feat(engine): drop novel_AI/ dependency — backend now runs independently |

---

## 历史里程碑

- **Phase 1（2026-06-26 ~ 06-28）**：novel-assistant + novel_AI 融合，backend 从依赖 novel_AI/ 切到独立运行
- **Phase 1.5（2026-06-29 ~ 07-01）**：12 commit 收尾链，1 个 commit 修了 17 小时静默失败的根因（thread_id 缺失）
- **深度修复轮（2026-07-02）**：10 commit 全面修复 — API key 加密、孤儿 running 自愈、Mock provider、Pydantic/utcnow 弃用清理、logging 统一、文档补全