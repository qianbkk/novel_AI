"""Writer title 提取测试 — Issue #2 修复验证。

300 章实测暴露：
  - 所有章节标题都是「第N章·发展·第N章：推进剧情」（placeholder 重复）
  - 最后十几章全是 6 字 [待修订] 占位

修复（2026-07-16）：
  - writer.py prompt 让 LLM 输出 JSON {title, body}
  - _extract_title 4 级降级：JSON / markdown fence / 【标题】前缀 / 首句
  - orchestrator 把 title 写进 meta.json
  - chapter_import._derive_title 优先用 meta.title

回归测试：避免后续 writer.py / chapter_import.py 改动再次破坏这个流程。
"""
import json
import pytest

from app.bridge.chapter_import import _derive_title
from engine.agents.writer import _extract_title, _first_line_as_title, _goal_to_title
from engine.orchestrator import _placeholder_task


# ──────────────────── _extract_title ────────────────────


class TestExtractTitle:
    def test_json_form(self):
        raw = json.dumps({"title": "山巅对决", "body": "陆承冲向对手，挥剑斩下。", "title_alts": []})
        title, body = _extract_title(raw)
        assert title == "山巅对决"
        assert body == "陆承冲向对手，挥剑斩下。"

    def test_json_with_extra_fields(self):
        raw = json.dumps({"title": "觉醒之夜", "body": "正文第一段。", "extra": "ignored"})
        title, body = _extract_title(raw)
        assert title == "觉醒之夜"

    def test_markdown_fence_json(self):
        raw = '```json\n{"title": "茶楼密会", "body": "周芸把U盘递给陆承。"}\n```'
        title, body = _extract_title(raw)
        assert title == "茶楼密会"
        assert body == "周芸把U盘递给陆承。"

    def test_markdown_fence_no_lang(self):
        raw = '```\n{"title": "判决将至", "body": "法庭上..."}\n```'
        title, body = _extract_title(raw)
        assert title == "判决将至"

    def test_label_prefix(self):
        raw = "【标题】: 山巅对决\n陆承冲向对手。"
        title, body = _extract_title(raw)
        assert title == "山巅对决"
        assert "陆承" in body

    def test_label_prefix_fullwidth_colon(self):
        raw = "【标题】：契约签订\n主角与对手握手。"
        title, body = _extract_title(raw)
        assert title == "契约签订"

    def test_fallback_first_line(self):
        raw = "U盘里的表格密密麻麻，时间从2021年1月排到2024年3月。"
        title, body = _extract_title(raw)
        # 标题应该从首行提取
        assert "U盘" in title or "表格" in title or "时间" in title
        assert body == raw

    def test_empty_raw_returns_goal_fallback(self):
        title, body = _extract_title("", fallback_goal="主角觉醒获得传承")
        assert title == "主角觉醒获得传承"
        assert body == ""

    def test_empty_raw_no_goal(self):
        title, body = _extract_title("", fallback_goal="")
        assert title == "未命名章节"

    def test_json_missing_body_uses_fallback(self):
        raw = json.dumps({"title": "仅标题"})
        title, body = _extract_title(raw)
        # 没 body 但有 title → 用首句派生
        assert title == "仅标题"

    def test_title_truncated_to_50(self):
        long_title = "很长的标题" * 20  # 100 chars
        raw = json.dumps({"title": long_title, "body": "正文。"})
        title, body = _extract_title(raw)
        assert len(title) <= 50


# ──────────────────── _placeholder_task ────────────────────


class TestPlaceholderTask:
    def test_goal_uses_arc_name(self):
        arc = {"arc_name": "觉醒", "arc_goal": "主角觉醒获得传承", "estimated_chapters": 30}
        t = _placeholder_task(0, 5, arc)
        # chapter_goal 不再是「第N章：推进剧情」
        assert "推进剧情" not in t["chapter_goal"]
        assert "觉醒" in t["chapter_goal"] or "传承" in t["chapter_goal"]

    def test_role_varied_across_arc(self):
        arc = {"arc_name": "觉醒", "arc_goal": "主角觉醒", "estimated_chapters": 30}
        roles = [_placeholder_task(0, i, arc)["chapter_role"] for i in range(30)]
        # 30 章应该有多种 role（铺垫/发展/爽点/弧高潮）
        assert len(set(roles)) >= 3, f"全是相同 role: {set(roles)}"

    def test_arc_climax_marked(self):
        arc = {"arc_name": "觉醒", "arc_goal": "主角觉醒", "estimated_chapters": 30}
        # 弧高潮位置 ~53%（0.50-0.55 区间）→ 第16 章
        t_climax = _placeholder_task(0, 16, arc)
        assert t_climax["chapter_role"] == "弧高潮"
        assert t_climax["is_arc_climax"] is True
        # 弧高潮前的爽点章 i=13
        t_shuang = _placeholder_task(0, 13, arc)
        assert t_shuang["chapter_role"] == "爽点"
        assert t_shuang["is_arc_climax"] is False

    def test_chapter_number_sequential(self):
        arc = {"arc_name": "觉醒", "arc_goal": "主角觉醒", "estimated_chapters": 30}
        for i in range(5):
            t = _placeholder_task(0, i, arc)
            assert t["chapter_number"] == i + 1

    def test_fallback_when_arc_empty(self):
        arc = {}
        t = _placeholder_task(0, 0, arc)
        # 即使 arc 没信息也要能生成合理 task
        assert t["chapter_number"] == 1
        assert t["chapter_goal"]
        assert t["chapter_role"]


# ──────────────────── _derive_title (chapter_import) ────────────────────


class TestDeriveTitle:
    def test_meta_title_used(self):
        meta = {"title": "山巅对决", "chapter_role": "发展", "chapter_goal": "打斗"}
        title = _derive_title(42, meta, "正文...")
        assert title == "第42章·山巅对决"

    def test_meta_title_truncated(self):
        meta = {"title": "很长的标题" * 10}
        title = _derive_title(1, meta, "")
        assert len(title) <= 50

    def test_meta_title_unnamed_skipped(self):
        meta = {"title": "未命名章节", "chapter_role": "爽点"}
        title = _derive_title(5, meta, "")
        # "未命名章节" 视为无效，fallback 到 role 派生
        assert "第5章·爽点" in title

    def test_fallback_role_and_goal(self):
        meta = {"chapter_role": "发展", "chapter_goal": "主角调查账目"}
        title = _derive_title(10, meta, "正文...")
        assert "第10章·发展" in title
        assert "调查账目" in title

    def test_placeholder_goal_handled(self):
        # 老 meta 可能有「第10章：推进剧情」占位 → 不重复
        meta = {"chapter_role": "发展", "chapter_goal": "第10章：推进剧情"}
        title = _derive_title(10, meta, "")
        assert "第10章" in title
        assert "推进剧情" not in title  # 不重复占位词

    def test_fallback_first_line(self):
        meta = {}
        content = "U盘里的表格密密麻麻，时间从2021年1月排到2024年3月。\n第二行。"
        title = _derive_title(7, meta, content)
        assert "第7章" in title
        # 应该从首行提取
        assert "U盘" in title or "表格" in title

    def test_skip_markdown_heading(self):
        meta = {}
        content = "# 第七章 标题\n陆承开始调查。"
        title = _derive_title(7, meta, content)
        # 不应该把 markdown heading 当标题
        assert "第七章" not in title.replace("第7章", "")

    def test_skip_blank_lines(self):
        meta = {}
        content = "\n\n\n陆承站在山巅。\n第二行。"
        title = _derive_title(1, meta, content)
        assert "第1章" in title
        assert "陆承" in title or "山巅" in title

    def test_fully_empty_returns_chapter_only(self):
        title = _derive_title(99, {}, "")
        assert title == "第99章"


# ──────────────────── Integration: full pipeline ────────────────────


class TestPipelineIntegration:
    def test_writer_output_round_trip(self):
        """模拟 writer 输出 → chapter_import 派生完整链路。"""
        # writer 输出（带 title）
        writer_output = json.dumps({
            "title": "茶楼密会",
            "body": "周芸把U盘递给陆承。",
            "title_alts": [],
        }, ensure_ascii=False)
        title, body = _extract_title(writer_output)
        meta = {"title": title, "chapter_role": "发展"}
        # 喂给 chapter_import 派生
        derived = _derive_title(15, meta, body)
        assert derived == "第15章·茶楼密会"

    def test_no_title_meta_uses_fallback(self):
        """writer 输出没 title → chapter_import 用 role + goal 派生。"""
        writer_output = "陆承站在山巅，俯瞰整个云海。"
        _title, body = _extract_title(writer_output)
        meta = {"title": _title, "chapter_role": "爽点", "chapter_goal": "主角觉醒"}
        derived = _derive_title(20, meta, body)
        assert "第20章" in derived