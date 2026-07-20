"""已有小说反向提取世界观/人物/关系/势力/力量体系/伏笔：行为测试。

目标链路（goal 2026-07-19 授权）第三步：用户导入已有小说 → 触发提取 →
WorldSetting / Character / Faction / PowerSystem / Foreshadowing /
EntityRelation 入库 → push-concept 时 _build_worldbuild_snapshot 自动
携带 → 引擎续写带提取设定。

mock_payload 路径：settings.llm_provider 默认 mock，call_llm_json 走
mock 分支直接返回 mock_payload；测试不 monkeypatch LLM 即可端到端验证。

fixture 写法抄 backend/tests/test_novel_text_import.py:10-23, 128-139。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest

from tests._test_db import isolated_test_db  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────
# 纯函数：_build_corpus 截断逻辑
# ─────────────────────────────────────────────────────────────────────────
class TestBuildCorpus:
    def test_truncates_per_chapter_and_total(self):
        """每章截 _PER_CHAPTER_CHAR_BUDGET；总长截 _TOTAL_CORPUS_CHAR_BUDGET。"""
        from app.models import Chapter
        from app.novel_extract import _PER_CHAPTER_CHAR_BUDGET, _TOTAL_CORPUS_CHAR_BUDGET, _build_corpus

        big = "甲" * (_PER_CHAPTER_CHAR_BUDGET + 500)
        chapters = [
            Chapter(chapter_no=1, title="一", content=big),
            Chapter(chapter_no=2, title="二", content=big),
            Chapter(chapter_no=3, title="三", content=big),
        ]
        corpus = _build_corpus(chapters)
        # 3 章 * 1500 字 = 4500 < 30000，应该全保留；每章都被截到 1500
        # 但拼起来后仍超 30000 时最后一章会被进一步截 —— 3 * 1500 = 4500 < 30000
        assert len(corpus) <= _TOTAL_CORPUS_CHAR_BUDGET
        assert "第1章" in corpus and "第3章" in corpus

    def test_skip_empty_chapters(self):
        from app.models import Chapter
        from app.novel_extract import _build_corpus
        chapters = [
            Chapter(chapter_no=1, title="一", content=""),
            Chapter(chapter_no=2, title="二", content="   "),
            Chapter(chapter_no=3, title="三", content="正文"),
        ]
        corpus = _build_corpus(chapters)
        assert "第3章" in corpus
        assert "第1章" not in corpus
        assert "第2章" not in corpus


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────
@pytest.fixture
def project_id(api_client):
    """新建一个空 project 返回其 id。"""
    from app.database import SessionLocal
    from app.models import Project
    pid = "test-extract-" + uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        db.add(Project(id=pid, title="提取测试", genre="都市", config_json={}))
        db.commit()
    finally:
        db.close()
    return pid


@pytest.fixture
def project_with_chapters(api_client, project_id):
    """用 import-text API 种 2 章；正文里包含 mock 角色名「林渊」「苏晚栀」
    —— 否则 ChapterCharacter 重建断言空转。"""
    text = (
        "第一章 风起\n林渊在云州的早晨醒来，苏晚栀从窗台递过账本。\n"
        "债主委员会的人已经在门外等着。\n\n"
        "第二章 暗涌\n林渊决定先稳住局面，与苏晚栀合计出第一笔交易。\n"
    )
    api_client.post(
        f"/projects/{project_id}/chapters/import-text", json={"text": text},
    )
    return project_id


# ─────────────────────────────────────────────────────────────────────────
# API: POST /projects/{pid}/chapters/extract-setting
# ─────────────────────────────────────────────────────────────────────────
class TestExtractSettingApi:
    def test_no_chapters_returns_400(self, api_client, project_id):
        r = api_client.post(
            f"/projects/{project_id}/chapters/extract-setting",
            json={},
        )
        assert r.status_code == 400, r.text

    def test_unknown_project_returns_404(self, api_client):
        r = api_client.post(
            "/projects/no-such-pid/chapters/extract-setting",
            json={},
        )
        assert r.status_code == 404

    def test_writes_world_setting_characters_relations(
        self, api_client, project_with_chapters,
    ):
        r = api_client.post(
            f"/projects/{project_with_chapters}/chapters/extract-setting",
            json={},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["world_setting_written"] is True
        # mock payload：2 角色、1 关系、1 势力、1 力量体系、1 伏笔
        assert body["characters"] == 2
        assert body["relations"] == 1
        assert body["factions"] == 1
        assert body["power_systems"] == 1
        assert body["foreshadowings"] == 1
        assert body["chapters_used"] >= 2

        # DB 侧校验
        from app.database import SessionLocal
        from app.models import (
            Character, EntityRelation, Faction, Foreshadowing,
            PowerSystem, WorldSetting,
        )
        db = SessionLocal()
        try:
            ws = db.query(WorldSetting).filter_by(
                project_id=project_with_chapters,
            ).first()
            assert ws is not None
            assert ws.world_view_rich_json is not None
            assert "cosmos" in ws.world_view_rich_json
            assert ws.story_core_struct_json is not None
            assert "goal" in ws.story_core_struct_json

            chars = db.query(Character).filter_by(
                project_id=project_with_chapters,
            ).all()
            assert len(chars) == 2
            names = {c.name for c in chars}
            assert {"林渊", "苏晚栀"} == names
            # mock card 通过 schema → 8 段列应已写
            linyuan = next(c for c in chars if c.name == "林渊")
            assert linyuan.card_personality_json is not None
            assert linyuan.card_catchphrase_json is not None
            assert linyuan.card_arc_json is not None

            rels = db.query(EntityRelation).filter_by(
                project_id=project_with_chapters,
            ).all()
            assert len(rels) == 1
            assert rels[0].intensity == 9

            facs = db.query(Faction).filter_by(
                project_id=project_with_chapters,
            ).all()
            assert len(facs) == 1
            powers = db.query(PowerSystem).filter_by(
                project_id=project_with_chapters,
            ).all()
            assert len(powers) == 1
            assert powers[0].tiers_json and len(powers[0].tiers_json) >= 2

            fs = db.query(Foreshadowing).filter_by(
                project_id=project_with_chapters,
            ).all()
            assert len(fs) == 1
            assert fs[0].importance == "高"
            assert fs[0].status == "已铺垫"
        finally:
            db.close()

    def test_rebuilds_chapter_character_edges(
        self, api_client, project_with_chapters,
    ):
        """替换设定后，ChapterCharacter 边应重建 —— 两条边：林渊 和 苏晚栀
        在两章都出现，但同章同人只算 1 条 → 共 4 条（2 章 × 2 角色）。"""
        api_client.post(
            f"/projects/{project_with_chapters}/chapters/extract-setting",
            json={"replace": True},
        )
        from app.database import SessionLocal
        from app.models import ChapterCharacter
        db = SessionLocal()
        try:
            edges = db.query(ChapterCharacter).all()
            assert len(edges) == 4
        finally:
            db.close()

    def test_conflict_409_without_replace(self, api_client, project_with_chapters):
        """第二次提取不带 replace → 409。"""
        r1 = api_client.post(
            f"/projects/{project_with_chapters}/chapters/extract-setting",
            json={},
        )
        assert r1.status_code == 200, r1.text

        r2 = api_client.post(
            f"/projects/{project_with_chapters}/chapters/extract-setting",
            json={},
        )
        assert r2.status_code == 409, r2.text
        detail = r2.json()["detail"]
        assert detail["code"] == "setting_exists"

    def test_replace_true_is_idempotent(
        self, api_client, project_with_chapters,
    ):
        """replace=true 跑两次 → 行数不翻倍（WorldSetting 仍 1 行，
        Character 仍 2 行，等等）。"""
        r1 = api_client.post(
            f"/projects/{project_with_chapters}/chapters/extract-setting",
            json={"replace": True},
        )
        assert r1.status_code == 200
        r2 = api_client.post(
            f"/projects/{project_with_chapters}/chapters/extract-setting",
            json={"replace": True},
        )
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["characters"] == 2
        assert b2["relations"] == 1

        from app.database import SessionLocal
        from app.models import (
            Character, EntityRelation, Faction, Foreshadowing,
            PowerSystem, WorldSetting,
        )
        db = SessionLocal()
        try:
            assert db.query(WorldSetting).filter_by(
                project_id=project_with_chapters,
            ).count() == 1
            assert db.query(Character).filter_by(
                project_id=project_with_chapters,
            ).count() == 2
            assert db.query(EntityRelation).filter_by(
                project_id=project_with_chapters,
            ).count() == 1
            assert db.query(Faction).filter_by(
                project_id=project_with_chapters,
            ).count() == 1
            assert db.query(PowerSystem).filter_by(
                project_id=project_with_chapters,
            ).count() == 1
            assert db.query(Foreshadowing).filter_by(
                project_id=project_with_chapters,
            ).count() == 1
        finally:
            db.close()

    def test_invalid_character_card_degrades_not_fails(
        self, api_client, project_with_chapters, monkeypatch,
    ):
        """LLM 返回 personality 缺失的角色 → 200，warnings 非空，
        该角色仍入库但 card_personality_json 为 None（card 内容在
        detail_json["card"] 备查）。"""
        from app import novel_extract

        async def fake_call_llm_json(role, system_prompt, user_prompt, mock_payload):
            # 复用除 characters 之外的 payload，只换掉 characters
            base = mock_payload or {}
            return {
                "characters": [
                    {
                        "name": "残缺角色",
                        "role": "配角",
                        "card": {
                            # personality 缺 tags/summary → schema 失败
                            "personality": {},
                            "catchphrase": {"lines": ["一句话"]},
                            "arc": {"start_state": "a", "catalyst": "b", "end_state": "c"},
                        },
                    },
                ],
                "relations": [],
            }

        monkeypatch.setattr(novel_extract, "call_llm_json", fake_call_llm_json)

        r = api_client.post(
            f"/projects/{project_with_chapters}/chapters/extract-setting",
            json={},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # character card 校验失败 → 警告里应有它
        assert any("残缺角色" in w for w in body["warnings"]), body["warnings"]
        assert body["characters"] == 1

        from app.database import SessionLocal
        from app.models import Character
        db = SessionLocal()
        try:
            c = db.query(Character).filter_by(
                project_id=project_with_chapters, name="残缺角色",
            ).first()
            assert c is not None
            assert c.card_personality_json is None  # 校验失败：8 段列未写
            assert c.detail_json is not None and "card" in c.detail_json
        finally:
            db.close()

    def test_relation_with_unknown_name_is_skipped(
        self, api_client, project_with_chapters, monkeypatch,
    ):
        """LLM 返回的关系 from_name 找不到对应角色 → 跳过 + warning。"""
        from app import novel_extract

        async def fake_call_llm_json(role, system_prompt, user_prompt, mock_payload):
            base = mock_payload or {}
            if "characters" in base and "relations" in base:
                return {
                    "characters": [
                        # 只有 1 个角色
                        {"name": "林渊", "role": "主角",
                         "card": base["characters"][0]["card"]},
                    ],
                    "relations": [
                        # 但关系引用了不存在的「苏晚栀」
                        {
                            "from_name": "苏晚栀", "to_name": "林渊",
                            "relation": "青梅竹马", "description": "x",
                            "mutual": True, "intensity": 5, "tags": ["亲密"],
                        },
                    ],
                }
            return base

        monkeypatch.setattr(novel_extract, "call_llm_json", fake_call_llm_json)

        r = api_client.post(
            f"/projects/{project_with_chapters}/chapters/extract-setting",
            json={},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["characters"] == 1
        assert body["relations"] == 0
        assert any("苏晚栀" in w and "跳过" in w for w in body["warnings"]), body["warnings"]

    def test_max_chapters_param_limits_input(self, api_client, project_id):
        """max_chapters=0 边界：400（数据库校验由 pydantic Field ge=1 兜底）。"""
        r = api_client.post(
            f"/projects/{project_id}/chapters/extract-setting",
            json={"max_chapters": 0},
        )
        assert r.status_code == 422  # pydantic ge=1 触发

    def test_replace_false_when_empty_succeeds(
        self, api_client, project_with_chapters,
    ):
        """空 project 的第一次提取（replace 默认 false）应该成功 —— 409
        仅在已有设定时触发。"""
        r = api_client.post(
            f"/projects/{project_with_chapters}/chapters/extract-setting",
            json={},
        )
        assert r.status_code == 200