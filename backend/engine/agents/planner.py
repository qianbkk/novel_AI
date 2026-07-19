"""Planner Agent — 根据世界观概念生成完整设定包 (P2 移植版)

v1：novel_AI 原 planner_agent.py 通过 api_client.call_llm。
P2 移植：用 backend.engine.llm_router.get_active_router()，
       复用同进程 LLMRouter；MODEL_ROUTES["planner"] 决定 model。
v3：写入前 validate against backend/schema/setting_package.schema.json
    （防止字段名漂移再次让 5 张表全空）
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# 把 backend 加进 path 以便 import app.schema_validator
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..config.paths import SETTING_PATH_STR, novel_config_path
from ..utils import parse_llm_json_response, atomic_write_json
from app.schema_validator import validate_setting_package, SchemaError


def _find_novel_config() -> Path:
    """novel_config.json 的真实落盘位置（NOVEL_AI_DIR env 优先，与 push-concept 写入端一致）。

    历史：早期版本曾 fallback 到 novel_AI/config/ 兼容旧 CLI 引擎；该目录已于 2026-07-16 删除。
    """
    return novel_config_path()


def _load_novel_config() -> dict:
    """读取 novel_config.json。"""
    p = _find_novel_config()
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


PLANNER_SYSTEM = """你是一位顶级网络小说设定策划，擅长把一段模糊的世界观概念扩展为完整的可执行设定包。
你的输出会被后续的"章节任务拆解 Agent"使用，所以必须结构化、可被代码解析。

【输出字段】（严格 JSON，不要任何额外文字）：
{
  "novel_id": "（与输入一致）",
  "platform": "fanqie",
  "genre": "（与输入一致）",
  "title_candidates": ["候选书名 1", "候选书名 2", "候选书名 3"],
  "tagline": "一句话简介（30 字以内，要够抓人）",
  "protagonist": {
    "name": "主角姓名（2-3 字中文名）",
    "age": 主角初始年龄（数字）,
    "background": "主角背景（一句话，如：律所谈判顾问 / 重生者 / 没落世家子弟）",
    "personality": "性格关键词，逗号分隔（如：克制、敏锐、不动声色）",
    "speech_quirks": ["口癖 1", "口癖 2"],
    "awakening_trigger": "主角觉醒/转折的具体事件（一句话）",
    "initial_power_level": "主角起始境界名"
  },
  "world_setting": {
    "hidden_world_name": "隐藏世界观的名字",
    "hidden_world_history": "隐藏世界的背景（一段话 80-150 字）",
    "surface_world_name": "表面世界名字",
    "unique_elements": ["独特元素 1", "独特元素 2", "独特元素 3"]
  },
  "power_system": {
    "name": "力量体系名称",
    "currency": "资源/货币单位",
    "description": "力量体系说明（100 字内）",
    "levels": [
      {"level": 1, "name": "境界 1 名", "point_threshold": 0, "ability": "能力说明"},
      {"level": 2, "name": "境界 2 名", "point_threshold": 500, "ability": "能力说明"},
      {"level": 3, "name": "境界 3 名", "point_threshold": 2000, "ability": "能力说明"},
      {"level": 4, "name": "境界 4 名", "point_threshold": 8000, "ability": "能力说明"},
      {"level": 5, "name": "境界 5 名", "point_threshold": 30000, "ability": "能力说明"},
      {"level": 6, "name": "境界 6 名", "point_threshold": 100000, "ability": "能力说明"}
    ]
  },
  "key_characters": [
    {"name": "配角 1 姓名", "role": "身份", "speech_quirks": ["口癖"], "background": "一句话背景"}
  ],
  "arc_outline": [
    {
      "arc_id": 1,
      "arc_name": "第 1 弧名",
      "arc_goal": "本弧主要冲突/目标（一句话）",
      "estimated_chapters": 35,
      "arc_climax_description": "弧高潮是什么（一句话）",
      "arc_climax_chapter_offset": 28,
      "emotion_curve": "低开 / 持续上升 / 高潮 / 收尾",
      "new_characters_introduced": ["本弧新登场配角"],
      "arc_ending_state": "弧结束时主角的状态（一句话）",
      "is_final_arc": false
    }
  ],
  "foreshadowing_seeds": [
    {"content": "伏笔种子 1（埋下去等后续章节回收）", "target_arc": 2},
    {"content": "伏笔种子 2", "target_arc": 3}
  ],
  "golden_chapter_hooks": {
    "chapter_1_opening": "第 1 章开篇方向（一句话，30 字以内）",
    "chapter_1_shuang_point": "第 1 章爽点描述",
    "chapter_3_cliffhanger": "第 3 章结尾钩子"
  }
}

【硬约束】
- arc_outline 至少 4 弧（4 弧 × 30 章 ≈ 120 章目标长度）
- key_characters 至少 4 个（除主角外的关键配角）
- title_candidates 3 个，且彼此风格有差异（一个有"金手指"感、一个有"情感"感、一个有"宏大叙事"感）
- power_system 的 6 个境界 point_threshold 必须递增（0 / 500 / 2000 / 8000 / 30000 / 100000）
- 输出必须是合法 JSON（无尾逗号、无注释、字符串内引号转义）
"""


def _snapshot_block(cfg: dict) -> str:
    """一期修复（根因 #3）：worldbuild 结构化快照 → planner prompt。

    有快照时 planner 从「重编者」降级为「补全者」——人物/势力/力量体系/
    伏笔必须沿用快照里的实体（名字、设定、关系不得改动），只补齐快照
    缺失的字段（如 arc_outline / golden_chapter_hooks）。
    """
    snap = cfg.get("worldbuild_snapshot") or {}
    if not snap:
        return ""
    parts = ["【已有结构化设定（必须沿用，不得另起炉灶）】"]
    if snap.get("world_view_rich"):
        wv = snap["world_view_rich"]
        parts.append("■ 世界观七段：" + json.dumps(wv, ensure_ascii=False)[:1200])
    if snap.get("story_core_struct"):
        parts.append("■ 故事核心：" + json.dumps(snap["story_core_struct"], ensure_ascii=False)[:400])
    if snap.get("history_timeline"):
        parts.append("■ 历史时间线：" + json.dumps(
            snap["history_timeline"], ensure_ascii=False)[:800])
    if snap.get("plot_skeleton"):
        parts.append("■ 卷级骨架：" + json.dumps(snap["plot_skeleton"], ensure_ascii=False)[:800])
    if snap.get("characters"):
        chars_brief = [
            {"name": c.get("name"), "role": c.get("role"),
             "personality": c.get("personality"), "background": c.get("background"),
             "catchphrase": c.get("catchphrase")}
            for c in snap["characters"][:8]
        ]
        parts.append("■ 已设定人物（姓名/性格/背景必须原样沿用）：" +
                     json.dumps(chars_brief, ensure_ascii=False)[:1500])
    if snap.get("power_systems"):
        parts.append("■ 力量体系（境界名称与层级必须原样沿用）：" +
                     json.dumps(snap["power_systems"], ensure_ascii=False)[:800])
    if snap.get("factions"):
        parts.append("■ 势力：" + json.dumps(
            [{"name": f.get("name")} for f in snap["factions"][:8]], ensure_ascii=False))
    if snap.get("foreshadowings"):
        parts.append("■ 已设计伏笔（必须全部收入 foreshadowing_seeds，不得丢弃）：" +
                     json.dumps(snap["foreshadowings"][:10], ensure_ascii=False)[:1000])
    parts.append(
        "【沿用规则】上述实体是用户在世界构建阶段的定稿："
        "protagonist / key_characters 用已有人物的名字和设定；"
        "power_system.levels 用已有境界；foreshadowing_seeds 必须包含全部已设计伏笔"
        "（可补充新伏笔）。你的增量工作是：arc_outline、golden_chapter_hooks、"
        "title_candidates、tagline，以及快照中缺失字段的补全。"
    )
    return "\n".join(parts) + "\n\n"


def _build_user_prompt(cfg: dict, novel_id: str) -> str:
    setting_concept = cfg.get("setting_concept", "（无）")
    genre = cfg.get("genre", "玄幻")
    platform = cfg.get("platform", "fanqie")
    budget = cfg.get("budget_limit_usd", 500)
    return f"""【项目基本信息】
novel_id: {novel_id}
platform: {platform}
genre: {genre}
budget_limit_usd: {budget}

【世界观概念】（由用户在前端填写、worldbuild 阶段聚合出的设定概念）
{setting_concept}

{_snapshot_block(cfg)}请基于以上概念，生成完整的设定包 JSON。注意：
1. 主角名字风格与世界观概念匹配（古风 → 古典名，现代 → 通俗名）
2. 力量体系必须支撑 ≥100 章长篇（多级境界 + 资源系统）
3. 弧规划至少 4 弧，每弧 30-40 章
4. 伏笔种子至少 2 个
5. title_candidates 必须是能吸引平台读者的书名，不要文艺腔

直接输出 JSON。"""


def run_planner(args, output_dir: str) -> dict:
    """Planner 命令主入口。"""
    cfg = _load_novel_config()
    if not cfg:
        raise FileNotFoundError(
            f"novel_config.json 不存在。请先在前端点『推送设定』写入。"
        )

    novel_id = cfg.get("novel_id", "default")
    user_prompt = _build_user_prompt(cfg, novel_id)

    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()

    print(f"📋 [Planner] 开始生成设定包 (novel_id={novel_id})...")
    print(f"   概念: {cfg.get('setting_concept', '')[:80]}...")

    text, cost = router.call(
        agent_name="planner",
        system_prompt=PLANNER_SYSTEM,
        user_prompt=user_prompt,
        max_tokens=6000,
        temperature=0.7,
    )

    print(f"   LLM 响应: {len(text)} 字符, 成本 ${cost:.4f}")

    setting = parse_llm_json_response(text, default={})
    if not setting:
        raise RuntimeError(f"Planner LLM 返回无法解析: {text[:500]}")

    # 注入默认值
    setting.setdefault("novel_id", novel_id)
    setting.setdefault("platform", cfg.get("platform", "fanqie"))
    setting.setdefault("genre", cfg.get("genre", "玄幻"))
    setting.setdefault("budget_limit_usd", cfg.get("budget_limit_usd", 500.0))

    # v3: 写盘前 validate，防止「LLM 漏字段」再次让 pull_setting 后 5 张表全空
    try:
        validate_setting_package(setting)
    except SchemaError as e:
        # 把 schema 错误显式打到 stdout，让 BridgeConsole 能看到
        print(f"   ❌ setting_package schema 校验失败: {e}")
        raise

    out_path = Path(output_dir) / "setting_package.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 迭代 #39: 之前直接 open(w) 写一半被杀 → setting_package.json 损坏
    # → 下次 run pull_setting 失败 → 5 张表全空（Phase 1 真实事故源头）。
    # 改用 atomic_write_json：先 .tmp + os.replace，老文件保留，损坏风险降到 0。
    atomic_write_json(str(out_path), setting)

    print(f"   ✅ 设定包已写入: {out_path}")
    print(f"   弧数: {len(setting.get('arc_outline', []))}, "
          f"配角: {len(setting.get('key_characters', []))}, "
          f"力量等级: {len(setting.get('power_system', {}).get('levels', []))}")

    return setting


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_planner(sys.argv[1:], ".") else 1)
