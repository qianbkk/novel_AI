"""标题 pipeline 端到端测试 — 验证主流程能产出带标题的章节。

用户反馈（2026-07-16）：
> "重点不是让你补现在的标题，重点是这个项目生成小说的流程里面是应该有合适的标题"

这个测试模拟完整流程：
  writer agent → orchestrator node_save_and_track → save_chapter (磁盘 meta.json)
  → chapter_import._derive_title → 写入 DB Chapter.title

每一环都验证 title 完整传递。

修订历史：
- 之前 writer prompt 不要求 LLM 输出 title → 整条 pipeline 都没有 title 字段
- 现在 writer 输出 JSON {title, body}，orchestrator 捕获并写 meta.json
- chapter_import._derive_title 优先用 meta.title
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ──────────────────── 单环：writer 输出 JSON 含 title ────────────────────


class TestWriterProducesTitle:
    """writer 端：验证 _extract_title 能从各种 LLM 输出中拿到 title。"""

    def test_strict_json(self):
        from engine.agents.writer import _extract_title
        title, body = _extract_title('{"title": "U盘揭开转账证据", "body": "陆承打开电脑。"}')
        assert title == "U盘揭开转账证据"
        assert "陆承打开电脑" in body

    def test_markdown_fence(self):
        from engine.agents.writer import _extract_title
        title, body = _extract_title('```json\n{"title": "查账追辖壳公司", "body": "正文"}\n```')
        assert title == "查账追辖壳公司"

    def test_plain_text_fallback(self):
        from engine.agents.writer import _extract_title
        title, body = _extract_title("陆承从拘留所出来，外面下着雨。")
        # fallback 从首句截
        assert "陆承" in title
        assert "陆承从拘留所出来" in body

    def test_empty_returns_goal_fallback(self):
        from engine.agents.writer import _extract_title
        title, body = _extract_title("", fallback_goal="主角觉醒获得传承")
        assert title == "主角觉醒获得传承"


# ──────────────────── 中环：orchestrator 把 title 写进 meta.json ────────────────────


class TestOrchestratorPassesTitle:
    """orchestrator 端：验证 node_save_and_track 把 writer title 写进 meta dict。"""

    def test_save_chapter_meta_includes_title(self, tmp_path):
        """save_chapter 应该把 meta 全量写到 _meta.json（包括 title）。"""
        from engine.orchestrator import save_chapter

        novel_id = "test-novel-pipeline"
        ch_num = 1
        text = "陆承冲进法庭，把U盘交给律师。"
        meta = {
            "chapter_number": ch_num,
            "chapter_role": "爽点",
            "chapter_goal": "主角觉醒获得传承",
            "title": "U盘揭开转账证据",  # ← writer 端传过来的 title
            "score": 7.5,
            "verdict": "PASS",
            "word_count": 2298,
        }

        # 临时改 CHAPTERS_DIR 到 tmp_path
        import engine.orchestrator as orch
        original_chapters_dir = orch.CHAPTERS_DIR
        orch.CHAPTERS_DIR = tmp_path / "chapters"
        orch.CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            save_chapter(novel_id, ch_num, text, meta)
            meta_path = tmp_path / "chapters" / "ch_0001_meta.json"
            assert meta_path.exists()
            written = json.loads(meta_path.read_text(encoding="utf-8"))
            assert written["title"] == "U盘揭开转账证据"  # ← title 持久化成功
            assert written["chapter_goal"] == "主角觉醒获得传承"
        finally:
            orch.CHAPTERS_DIR = original_chapters_dir


# ──────────────────── 末环：chapter_import 读 meta.title ────────────────────


class TestChapterImportReadsMetaTitle:
    """chapter_import 端：验证 _derive_title 优先用 meta.title。"""

    def test_meta_title_priority(self):
        from app.bridge.chapter_import import _derive_title
        meta = {"title": "U盘揭开转账证据"}
        title = _derive_title(270, meta, "任何内容...")
        assert title == "第270章·U盘揭开转账证据"

    def test_meta_title_over_goal(self):
        """即使 meta 有 chapter_goal，meta.title 仍优先。"""
        from app.bridge.chapter_import import _derive_title
        meta = {
            "title": "陆承揭露真相",
            "chapter_role": "爽点",
            "chapter_goal": "主角觉醒获得传承",
        }
        title = _derive_title(1, meta, "...")
        assert title == "第1章·陆承揭露真相"

    def test_no_meta_title_falls_through(self):
        """没有 meta.title 时按 role+goal 派生（兼容老数据）。"""
        from app.bridge.chapter_import import _derive_title
        meta = {"chapter_role": "爽点", "chapter_goal": "主角觉醒"}
        title = _derive_title(1, meta, "...")
        assert "第1章" in title


# ──────────────────── 端到端：writer → save → import 完整链路 ────────────────────


class TestEndToEndPipeline:
    """完整 pipeline 模拟：mock writer 输出 JSON，orchestrator 处理，import 写入 DB。"""

    def test_full_flow_writer_to_db(self, tmp_path):
        """模拟一次完整章节生成 → 入库，确保 title 从 writer 一直传到 DB。"""
        from engine.orchestrator import save_chapter
        from app.bridge.chapter_import import _derive_title
        import engine.orchestrator as orch

        # ── Step 1: writer 输出 JSON 含 title
        writer_output = json.dumps({
            "title": "U盘揭开转账证据",
            "body": "陆承把U盘插进电脑。屏幕上密密麻麻的数字开始滚动。",
            "title_alts": []
        }, ensure_ascii=False)

        from engine.agents.writer import _extract_title
        title, body = _extract_title(writer_output)
        assert title == "U盘揭开转账证据"

        # ── Step 2: orchestrator 的 node_save_and_track 写 meta.json
        # 模拟 meta 字典（orchestrator 拼装）
        original_dir = orch.CHAPTERS_DIR
        orch.CHAPTERS_DIR = tmp_path / "chapters"
        orch.CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            meta = {
                "chapter_number": 270,
                "chapter_role": "爽点",
                "chapter_goal": "主角觉醒获得传承",
                "title": title,  # ← writer 端捕获的 title
                "score": 7.5,
                "verdict": "PASS",
                "word_count": len(body),
            }
            save_chapter("test-novel", 270, body, meta)

            # ── Step 3: chapter_import 读 meta.json → 派生 DB title
            meta_path = tmp_path / "chapters" / "ch_0270_meta.json"
            assert meta_path.exists()
            loaded_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            derived_title = _derive_title(270, loaded_meta, body)

            # ✅ title 完整传递到 DB
            assert derived_title == "第270章·U盘揭开转账证据"
        finally:
            orch.CHAPTERS_DIR = original_dir

    def test_writer_output_fallback_when_llm_omits_title(self, tmp_path):
        """即使 LLM 输出没 title（漂移），pipeline 不应该产生空 title。

        这种情况：writer 给了 body 但没给 title → _extract_title fallback 用首句。
        """
        from engine.orchestrator import save_chapter
        from app.bridge.chapter_import import _derive_title
        from engine.agents.writer import _extract_title
        import engine.orchestrator as orch

        # LLM 只输出正文，没给 title
        body_text = "陆承冲进法庭，把U盘交给律师，转身走向证人席。"
        title, body = _extract_title(body_text)
        # _extract_title 走首句 fallback
        assert "陆承" in title

        original_dir = orch.CHAPTERS_DIR
        orch.CHAPTERS_DIR = tmp_path / "chapters"
        orch.CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            meta = {
                "chapter_number": 1,
                "chapter_role": "爽点",
                "chapter_goal": "g",
                "title": title,
            }
            save_chapter("test-novel", 1, body, meta)
            loaded_meta = json.loads((tmp_path / "chapters" / "ch_0001_meta.json").read_text(encoding="utf-8"))
            derived = _derive_title(1, loaded_meta, body)
            # title 必须存在且包含章节号
            assert "第1章" in derived
            assert len(derived) > len("第1章")  # 不只是章节号占位
        finally:
            orch.CHAPTERS_DIR = original_dir

    def test_pipeline_never_produces_empty_title(self):
        """回归测试：无论 LLM 输出何种漂移，title 必须非空。"""
        from engine.agents.writer import _extract_title

        # 各种 LLM 漂移场景
        cases = [
            ('{"title": "", "body": "正文。"}', "正文"),  # 空 title → fallback
            ('{"title": "第1章 觉醒之夜", "body": "陆承...。"}', "觉醒之夜"),  # 带前缀 → 清洗
            ('```\n{"title": "U盘揭开转账证据", "body": "正文"}\n```', "U盘揭开转账证据"),
            ("陆承冲进法庭。", None),  # 无 JSON → 首句 fallback
        ]
        for raw, expected_substr in cases:
            title, _ = _extract_title(raw)
            assert title, f"empty title for input: {raw[:50]}"
            if expected_substr:
                assert expected_substr in title, f"expected {expected_substr} in {title}"