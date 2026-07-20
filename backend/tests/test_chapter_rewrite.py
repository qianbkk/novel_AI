"""单章改写 API：保留原章、产出候选版本、不覆盖。

属于 goal 2026-07-19 授权的「已有小说」特性族最后一段。
链路：rewrite_chapter() → engine mock writer → 原子写 ch_NNNN_vX.txt →
更新 rewrite_candidates.json。绝不修改原 Chapter.content 与 ch_NNNN.txt。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest

from tests._test_db import isolated_test_db  # noqa: F401


@pytest.fixture
def project_with_bound_engine(api_client):
    """新建 project + 种 2 章 + 绑定 engine-e2e 目录 + 种 setting_package.json。
    返回 (project_id, engine_dir, original_chapter_text)。"""
    from app.database import SessionLocal
    from app.models import Chapter, NovelAIBinding, Project

    pid = "test-rewrite-" + uuid.uuid4().hex[:8]
    engine_dir = _BACKEND / "data" / "engine-e2e"

    db = SessionLocal()
    try:
        db.add(Project(id=pid, title="改写测试", genre="都市", config_json={}))
        db.commit()  # 必须先 commit Project，否则 FK ref 不到
        db.add(NovelAIBinding(
            project_id=pid, novel_ai_dir=str(engine_dir), novel_id=pid,
        ))
        db.commit()
    finally:
        db.close()

    text = (
        "第一章 风起\n林渊在云州的早晨醒来，苏晚栀从窗台递过账本。"
        "债主委员会的人已经在门外等着。\n\n"
        "第二章 暗涌\n林渊与苏晚栀合计第一笔交易。\n"
    )
    api_client.post(f"/projects/{pid}/chapters/import-text", json={"text": text})

    db = SessionLocal()
    try:
        ch1 = db.query(Chapter).filter_by(project_id=pid, chapter_no=1).first()
        original_content = ch1.content
    finally:
        db.close()

    # 种一个最小 setting_package.json（不含也可，service 会容错）
    pkg_path = engine_dir / "output" / "setting_package.json"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    pkg_path.write_text(json.dumps({
        "protagonist": {"name": "林渊", "age": 32,
                        "background": "云州林氏长子",
                        "personality": "克制、精算",
                        "speech_quirks": ["这局我来开局。"]},
        "world_setting": {"surface_world_name": "云州",
                          "hidden_world_name": "九霄",
                          "hidden_world_history": "灵气复苏百年，修士崛起为隐性财阀。"},
        "power_system": {"name": "债感修炼体系",
                         "description": "通过回应他人对你的债来修炼。"},
    }, ensure_ascii=False), encoding="utf-8")

    # 清理上次测试可能留下的 candidates（ch_0001_v*.txt）
    ch_dir = engine_dir / "output" / "chapters"
    if ch_dir.exists():
        for p in ch_dir.glob("ch_0001_v*.txt"):
            p.unlink()

    return pid, engine_dir, original_content


class TestRewriteApi:
    def test_rewrite_writes_candidate_and_keeps_original(
        self, api_client, project_with_bound_engine,
    ):
        """基本路径：POST /chapters/{no}/rewrite 落 ch_NNNN_vD.txt，
        原 Chapter.content 与 ch_NNNN.txt 都不动。"""
        pid, engine_dir, original = project_with_bound_engine

        r = api_client.post(
            f"/projects/{pid}/chapters/1/rewrite",
            json={"instruction": "把开头改为更冷峻的内视角"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["chapter_no"] == 1
        assert body["version_label"] == "D"   # 默认从 D 起
        assert body["original_unchanged"] is True
        assert "ch_0001_vD.txt" in body["candidate_path"]
        assert body["candidate_word_count"] > 0

        # 文件存在 + 含 snapshot 关键词（task #6 注入路径）
        cand = engine_dir / "output" / "chapters" / "ch_0001_vD.txt"
        assert cand.exists(), f"候选文件未生成: {cand}"
        text = cand.read_text(encoding="utf-8")
        assert "林渊" in text or "苏晚栀" in text, (
            f"候选文本没含 snapshot 关键词: {text[:200]}"
        )

        # 原 Chapter.content 未变
        from app.database import SessionLocal
        from app.models import Chapter
        db = SessionLocal()
        try:
            ch = db.query(Chapter).filter_by(project_id=pid, chapter_no=1).first()
            assert ch.content == original, "原 Chapter.content 被改写动到了！"
        finally:
            db.close()

    def test_rewrite_custom_label(self, api_client, project_with_bound_engine):
        """指定 version_label=K → 落 ch_0001_vK.txt。"""
        pid, engine_dir, _ = project_with_bound_engine
        r = api_client.post(
            f"/projects/{pid}/chapters/1/rewrite",
            json={"instruction": "测试", "version_label": "K"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version_label"] == "K"
        assert (engine_dir / "output" / "chapters" / "ch_0001_vK.txt").exists()

    def test_rewrite_duplicate_label_409(
        self, api_client, project_with_bound_engine,
    ):
        """同 label 二次改写 → 409（detail.code=rewrite_label_exists）。
        保留候选；不覆盖原 ch_NNNN_vX.txt。"""
        pid, engine_dir, _ = project_with_bound_engine
        r1 = api_client.post(
            f"/projects/{pid}/chapters/1/rewrite",
            json={"instruction": "第一次", "version_label": "D"},
        )
        assert r1.status_code == 200

        # 记录第一版内容
        cand_path = engine_dir / "output" / "chapters" / "ch_0001_vD.txt"
        first_content = cand_path.read_text(encoding="utf-8")

        r2 = api_client.post(
            f"/projects/{pid}/chapters/1/rewrite",
            json={"instruction": "第二次", "version_label": "D"},
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["detail"]["code"] == "rewrite_label_exists"
        # 文件未变
        assert cand_path.read_text(encoding="utf-8") == first_content

    def test_rewrite_replace_true_overwrites(
        self, api_client, project_with_bound_engine,
    ):
        """replace=true 时同 label 覆盖（用户显式授权）。"""
        pid, engine_dir, _ = project_with_bound_engine
        api_client.post(
            f"/projects/{pid}/chapters/1/rewrite",
            json={"instruction": "第一版", "version_label": "E"},
        )
        cand_path = engine_dir / "output" / "chapters" / "ch_0001_vE.txt"
        first_mtime = cand_path.stat().st_mtime

        # 等 1.1s 让 mtime 变化
        import time as _t
        _t.sleep(1.1)

        r2 = api_client.post(
            f"/projects/{pid}/chapters/1/rewrite",
            json={"instruction": "覆盖版", "version_label": "E", "replace": True},
        )
        assert r2.status_code == 200, r2.text
        assert cand_path.stat().st_mtime > first_mtime

    def test_rewrite_invalid_label_400(self, api_client, project_with_bound_engine):
        """version_label 不是单个大写字母 → 400/422（pydantic Field 拦截也是合规失败）。"""
        pid, _, _ = project_with_bound_engine
        r = api_client.post(
            f"/projects/{pid}/chapters/1/rewrite",
            json={"instruction": "x", "version_label": "AB"},
        )
        assert r.status_code in (400, 422), r.text

    def test_rewrite_empty_instruction_400(
        self, api_client, project_with_bound_engine,
    ):
        pid, _, _ = project_with_bound_engine
        r = api_client.post(
            f"/projects/{pid}/chapters/1/rewrite",
            json={"instruction": "   "},
        )
        assert r.status_code == 400

    def test_rewrite_chapter_not_found_404(
        self, api_client, project_with_bound_engine,
    ):
        pid, _, _ = project_with_bound_engine
        r = api_client.post(
            f"/projects/{pid}/chapters/999/rewrite",
            json={"instruction": "no such chapter"},
        )
        assert r.status_code == 404

    def test_rewrite_unknown_project_404(self, api_client):
        r = api_client.post(
            "/projects/no-such-pid/chapters/1/rewrite",
            json={"instruction": "x"},
        )
        assert r.status_code == 404

    def test_list_candidates_includes_rewrite(
        self, api_client, project_with_bound_engine,
    ):
        """GET /chapters/{no}/candidates 应列出本次改写的候选。"""
        pid, engine_dir, _ = project_with_bound_engine
        api_client.post(
            f"/projects/{pid}/chapters/1/rewrite",
            json={"instruction": "测试改写", "version_label": "D"},
        )
        r = api_client.get(f"/projects/{pid}/chapters/1/candidates")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["chapter_no"] == 1
        versions = [c["version"] for c in body["candidates"]]
        assert "D" in versions
        d_entry = next(c for c in body["candidates"] if c["version"] == "D")
        assert d_entry["word_count"] > 0
        assert d_entry["instruction_preview"] == "测试改写"

    def test_rewrite_uses_next_available_label(
        self, api_client, project_with_bound_engine,
    ):
        """连续改写三次：默认 label 依次 D/E/F（不撞已有）。"""
        pid, _, _ = project_with_bound_engine
        labels = []
        for _ in range(3):
            r = api_client.post(
                f"/projects/{pid}/chapters/1/rewrite",
                json={"instruction": "x"},
            )
            assert r.status_code == 200
            labels.append(r.json()["version_label"])
        assert labels == ["D", "E", "F"]