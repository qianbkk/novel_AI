"""split_invariants.py — 把 test_invariants.py 拆分到 invariants/ 子包

按类名按业务域分组：
  schemas        — schema validator / _derive_title / pydantic nullable / build summary
  build          — length budget / rewriter budget / parser / save state / memory
  engine         — orchestrator / agents / writer / checker / outline / tracker
  mock_provider  — mock LLM provider / rate limit / mock provider auto-activate
  bridge         — bridge run / bridge guard / bridge dead code / bridge subprocess / concurrency
  worldbuild     — worldbuild stages / post-process / import / pull setting / export
  security       — master key / provider api key / rotation / persisted across restarts
  deploy         — openapi export / docs / lint / pyenv aware / deployment
  frontend_align — port consistency / frontend types aligned / agents package doc
  audit          — audit review feedback / reports path / exporter / calibrate
  rate_limit     — middleware / memory cleanup
  backup         — backup DB / restore

不在测试运行路径上（一次性的迁移脚本），可执行后删。
"""
import re
from pathlib import Path

INVARIANTS_DIR = Path(__file__).resolve().parents[1] / "tests" / "invariants"
SOURCE = Path(__file__).resolve().parents[1] / "tests" / "test_invariants.py"

# 类名 → 子包文件
DOMAIN_MAP = {
    # schemas
    "TestSettingPackageSchema": "schemas",
    "TestChapterMetaSchema": "schemas",
    "TestDeriveTitle": "schemas",
    "TestPydanticNullable": "schemas",
    "TestBuildSummary": "schemas",
    "TestWorldViewRichSchema": "schemas",
    "TestCharacterCardSchema": "schemas",
    "TestEntityRelationRichSchema": "schemas",
    "TestSchemaLenientOnUnion": "schemas",

    # build (length budget, parser, save_state)
    "TestLengthBudget": "build",
    "TestRewriterLengthBudget": "build",
    "TestParseLLMJsonResponseTypeGuard": "build",
    "TestTrackerUsesParseWithDictDefault": "build",
    "TestSaveStateUpdatesLastUpdated": "build",
    "TestSaveStateConcurrencySafe": "build",
    "TestSaveStateTrueConcurrency": "build",
    "TestSaveStateWindowsEmptyFileLock": "build",
    "TestMemorySaveAtomic": "build",
    "TestSchemaValidatorFailFast": "schemas",  # cross-domain — 放 schemas

    # mock provider
    "TestMockLLMProvider": "mock_provider",
    "TestMockProviderAutoActivate": "mock_provider",
    "TestMockProviderEndToEnd": "mock_provider",
    "TestLLMRouterInstallMockContract": "mock_provider",
    "TestLlmRouterMiniMaxReasoningContent": "mock_provider",
    "TestLLMRouterMockMount": "mock_provider",
    "TestLlmClientRetryCatchesAll": "mock_provider",
    "TestParseLlmJsonResponseAllStrategiesFail": "mock_provider",

    # engine / agents / orchestrator
    "TestWriterFailureNoFakeStub": "engine",
    "TestOrchestratorNoFakePass": "engine",
    "TestAgentNetworkRetry": "engine",
    "TestStatePathFromBinding": "engine",
    "TestOrchestratorSummarizerNotSilent": "engine",
    "TestOrchestratorPipelineStateCoherence": "engine",
    "TestEngineStateSafetyInvariants": "engine",
    "TestOrchestratorSettingLoadError": "engine",
    "TestOrchestratorSettingCacheInvalidates": "engine",
    "TestMemoryManagerNoSilentException": "engine",
    "TestStyleManagerNoSilentException": "engine",
    "TestRouterProxyMountNoSilentException": "engine",
    "TestPlannerMockPayloadValid": "engine",
    "TestBudgetManager": "engine",
    "TestOutlineCostNotDoubleCharged": "engine",
    "TestHumanReviewAtomicAndLoadNoSilent": "engine",

    # bridge
    "TestBridgeDeadCodeRemoved": "bridge",
    "TestOrphanBridgeRunRecovery": "bridge",
    "TestReviewContract": "bridge",
    "TestBridgeSubprocessArchitecture": "bridge",
    "TestRunBridgeConcurrencyGuard": "bridge",
    "TestBridgeEndpointsWorldbuildGuard": "bridge",
    "TestBridgeRunConcurrencyGuard": "bridge",

    # worldbuild / chapters
    "TestWorldbuildStagesEndpoint": "worldbuild",
    "TestPostProcessLLMFailure": "worldbuild",
    "TestImportChaptersResilient": "worldbuild",
    "TestExportChaptersResilient": "worldbuild",
    "TestPullSettingJsonErrorHandling": "worldbuild",
    "TestPullSettingFKCascade": "worldbuild",
    "TestReportsPathUnified": "worldbuild",
    "TestReportsPathUnifiedExtra": "worldbuild",
    "TestGraphCommandFailurePaths": "worldbuild",
    "TestGraphPyEnvAwareOutputDir": "worldbuild",
    "TestSubprocessEnvContract": "worldbuild",

    # security (master key, encryption)
    "TestMasterKeyRotation": "security",
    "TestProviderApiKeyEncrypted": "security",
    "TestMasterKeyScriptsEndToEnd": "security",
    "TestRotateMasterKeyEndToEnd": "security",
    "TestMasterKeyStableAcrossCalls": "security",
    "TestMasterKeyPersistedAcrossRestarts": "security",

    # backup
    "TestBackupDB": "backup",

    # frontend alignment
    "TestFrontendBackendPortConsistency": "frontend_align",
    "TestFrontendTypesAligned": "frontend_align",
    "TestAgentsPackageDocAccurate": "frontend_align",

    # deploy / docs / openapi
    "TestDeploymentDocs": "deploy",
    "TestOpenApiExport": "deploy",
    "TestHealthEndpointDBCheck": "deploy",
    "TestPytestCollection": "deploy",  # 是基础设施检查，放 deploy
    "TestSQLitePragmas": "deploy",  # db infra 调整，放 deploy

    # audit / reports / exporter
    "TestAuditReviewFeedbackApplied": "audit",
    "TestExporterAndCalibrateNoSilentException": "audit",

    # rate limit
    "TestRateLimitMiddleware": "rate_limit",
    "TestRateLimitMemoryCleanup": "rate_limit",
    "TestRateLimitHeaderAccuracy": "rate_limit",

    # SSE / streams
    "TestSSEQueueCleanup": "engine",  # 与 engine 配合

    # orphans (31 个没在第一版映射) — 填补
    "TestLlmRouterDecryptFailureLogging": "mock_provider",
    "TestMigrationsIdempotent": "schemas",
    "TestGetDbDependency": "deploy",
    "TestApplyReviewInputValidation": "bridge",
    "TestLoadStateRobustness": "build",
    "TestSecurityConstants": "security",
    "TestProviderTableSchema": "security",
    "TestHumanEscalationNotEndRun": "engine",
    "TestAtomicWriteJsonPromoted": "build",
    "TestPlannerAtomicWrite": "engine",
    "TestTrackerParseFailureLogged": "engine",
    "TestComplianceParseFailNotFakePass": "engine",
    "TestInitArcJsonDecodeHandling": "engine",
    "TestAtomicWriteJsonPropagated": "build",
    "TestNovelProductionEnforcement": "deploy",
    "TestCallWithBudgetDedupe": "engine",
    "TestWriterNoPrivateRouterState": "engine",
    "TestProxyApplied": "mock_provider",
    "TestSummarizerParseFailureNotSilent": "engine",
    "TestChapterCheckerNoFakePass": "engine",
    "TestAtomicWriteJsonFinalPropagation": "build",
    "TestBudgetReportEmptyLogNoKeyError": "engine",
    "TestAnthropicProxyApplied": "mock_provider",
    "TestMinimaxEndpointUpdated": "mock_provider",
    "TestLoadStateNoSilentFallback": "build",
    "TestDrainStdoutExceptionHandling": "bridge",
    "TestMonitorRunNoDeadCode": "scripts",  # 检查 scripts/
    "TestRewriteLengthAtomicMeta": "build",
    "TestOrchestratorTrackerNotSilent": "engine",
}


def split():
    src = SOURCE.read_text(encoding="utf-8")
    print(f"[debug] SOURCE size: {len(src)}")
    if not src.startswith('"""'):
        raise RuntimeError("Unexpected source format")

    # 提取头部（imports + module docstring）
    docstring_end = src.find('"""', 3) + 3
    header = src[:docstring_end]

    # 切分出所有 class / def 顶级元素
    # 用 python AST 比 regex 稳（class 跨行 / 嵌套容易搞错）
    import ast
    tree = ast.parse(src)
    classes = {}  # class_name → source_segment
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            seg = ast.get_source_segment(src, node)
            if seg:
                classes[node.name] = seg
    print(f"[debug] classes parsed: {len(classes)}")

    # 按域分组
    buckets: dict[str, list[str]] = {}
    orphans = []
    for name, seg in classes.items():
        domain = DOMAIN_MAP.get(name)
        if domain is None:
            orphans.append(name)
            domain = "misc"
        buckets.setdefault(domain, []).append(seg)

    if orphans:
        print(f"[warn] {len(orphans)} 类没在 DOMAIN_MAP 里 → 放进 misc：{orphans}")
    print(f"[debug] buckets: {list(buckets.keys())}")

    INVARIANTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── 公共 imports ──
    # 原文件顶部声明：stdlib + pytest + 路径设置 + app.schema_validator 系列
    # 全子文件都注入——over-include 是 OK 的，类级 imports 也各自有，
    # 重复只是 last-wins，零行为差异。
    common_imports = (
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        "import pytest\n"
        "\n"
        "BACKEND = Path(__file__).resolve().parents[2]\n"
        "sys.path.insert(0, str(BACKEND))\n"
        "\n"
        "# ── 原 test_invariants.py 顶部声明的 app.schema_validator 系列 ──\n"
        "from app.schema_validator import (  # noqa: E402,F401\n"
        "    validate_setting_package, validate_chapter_meta, SchemaError,\n"
        "    get_setting_package_schema, get_chapter_meta_schema,\n"
        "    validate_world_view_rich, validate_character_card, validate_entity_relation_rich,\n"
        "    get_world_view_rich_schema, get_character_card_schema, get_entity_relation_rich_schema,\n"
        ")\n"
    )

    # ── 跨文件 helper 解析 ──
    cross_refs: dict[str, list[str]] = {}
    # _backend_alive: def 在 misc，但被 frontend_align 用→复制到 frontend_align
    if "_backend_alive" in classes:
        cross_refs.setdefault("test_frontend_align.py", [])
        cross_refs["test_frontend_align.py"].append(classes["_backend_alive"])

    for domain, segs in buckets.items():
        out = INVARIANTS_DIR / f"test_{domain}.py"
        body = "\n\n\n".join(segs)
        # 注入跨文件 helper
        extra = "\n\n\n".join(cross_refs.get(out.name, []))
        if extra:
            body = extra + "\n\n\n" + body
        out.write_text(
            f'"""{domain}/ — Phase 3 测试拆分\n\n'
            f'不变量测试按业务域分文件存放。\n原文件位置：tests/test_invariants.py（已替换为 re-export shim）\n"""\n\n'
            f'{common_imports}\n{body}\n',
            encoding="utf-8",
        )
        print(f"[ok] {out.name}: {len(segs)} test class(es)")

    # 重写 test_invariants.py 为 shim
    shim_lines = [
        '"""test_invariants.py — Phase 3 子包 re-export shim',
        '',
        f'原 8500 行单文件已按业务域拆分到 {INVARIANTS_DIR.relative_to(SOURCE.parent)}/',
        '本文件保留作为向后兼容入口（pytest discoverable 会先收这一个）—',
        '实际测试现在跑在子包文件里。',
        '',
        '为什么留这个 shim 而不直接删：',
        '  - 外部脚本 / CI 命令 `pytest tests/test_invariants.py` 仍可工作',
        '  - git log 检索 `tests/test_invariants.py::` 不全断',
        '  - `pytest tests/` 默认全部 collect 一次，子包会自动被收',
        '"""',
        '',
    ]

    for domain in sorted(buckets.keys()):
        shim_lines.append(f"from tests.invariants.test_{domain} import *  # noqa: F401,F403  # Phase 3 split")
    shim_lines.append("")

    SOURCE.write_text("\n".join(shim_lines), encoding="utf-8")
    # 同时在 invariants/ 里放个 __init__.py 让它成为包
    (INVARIANTS_DIR / "__init__.py").write_text("", encoding="utf-8")
    print(f"[ok] {SOURCE.name} 改成 re-export shim")


if __name__ == "__main__":
    split()
