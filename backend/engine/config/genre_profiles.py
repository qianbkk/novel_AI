"""config/genre_profiles.py — 6 个男频题材画像模板（v1.0 Stage A）

设计原则：
- 6 个主流男频：玄幻 / 仙侠 / 都市 / 历史 / 军事 / 科幻
- 每个模板含 reader_persona + tone_preference + taboo + show_item_examples + research_strength
- show_item_examples 必须用具体物件/动作 + 情绪标签（用户指导重点）
- 模板是 seed，LLM 在此基础上细化（不覆盖）

CLAUDE.md 红线：
- 不含具体项目专名（角色名/地名/世界名）
- prompt 不写具体项目专名（角色名/地名）—— 一律从 setting 渲染，缺失时降级为中性措辞
"""

from __future__ import annotations

# v1.0 schema 必填字段（与 genre_profiler 输出对齐）
REQUIRED_PROFILE_FIELDS = (
    "genre",
    "genre_key",
    "reader_persona",
    "tone_preference",
    "taboo",
    "show_item_examples",
    "research_strength",
)


# ── 6 个主流男频题材模板 ─────────────────────────────

_GENRE_TEMPLATES: dict[str, dict] = {
    "xuanhuan": {
        "genre_key": "xuanhuan",
        "genre": "玄幻",
        "reader_persona": {
            "primary": "18-30 岁男性，热爱废柴逆袭/血脉觉醒/打脸反派，喜欢升级数字爽感",
            "core_fantasy": "从被看不起的落魄少年，靠血脉/传承/奇遇一路碾压到大陆之巅",
            "secondary": "代入感最强的不是主角光环，而是'被低估后反转'那一刻",
        },
        "tone_preference": "节奏快、打脸爽、节奏推进感强；沉郁克制不讨好",
        "taboo": [
            "无脑升级流（每章打一只怪爆一层）",
            "后宫种马（与女角色关系只用身体推进）",
            "设定自相矛盾（前一章的境界后一章打破）",
            "配角无脑送人头（没有动机的反派）",
        ],
        "show_item_examples": [
            "血脉觉醒 → 滴血入玉佩，玉佩碎开的纹路是族徽",
            "被羞辱 → 主角没有握拳，只把袖口往下拉了一寸（准备走的动作）",
            "敌意 → 对方的佩刀比上次长了一寸（他这次下了杀心）",
            "成长 → 主角的手从'微微颤抖'到'端起茶杯不再洒'",
            "信任建立 → 老人从袖中摸出一枚旧币（两人之间第一次出现私人信物）",
        ],
        "research_strength": "medium",
    },
    "xianxia": {
        "genre_key": "xianxia",
        "genre": "仙侠",
        "reader_persona": {
            "primary": "20-35 岁男性/部分女性，沉醉于修仙长生/师徒虐恋/仙门权谋",
            "core_fantasy": "超越凡人寿命、悟道飞升、或与道侣共历劫波",
            "secondary": "修仙读者最敏感的是'境界体系的逻辑自洽'和'道心的真实性'",
        },
        "tone_preference": "飘逸中带孤寂；长线叙事；不滥用'踏破虚空'等套话",
        "taboo": [
            "境界混乱（前章筑基后章金丹又被吊打）",
            "师徒恋=恋爱脑（情感线脱离道心单飞）",
            "灵气/丹药物价体系崩坏",
            "反派出场只为送经验（无道心无挣扎的反派假）",
        ],
        "show_item_examples": [
            "悟道 → 主角没有盘腿入定，只是在溪边洗了一次手（那一刻悟了）",
            "道心坚定 → 拒绝诱惑时，主角没有说话，把师父给的玉简轻轻放在桌上",
            "寿元将尽 → 主角的手枯了一寸（不是描述衰老）",
            "杀意 → 主角拂袖时袖角带起的风比平时凉了一度",
            "师徒情 → 师尊没有训话，只给主角倒了一杯凉茶（这次不热 = 师尊动了真怒）",
        ],
        "research_strength": "medium",
    },
    "dushi": {
        "genre_key": "dushi",
        "genre": "都市",
        "reader_persona": {
            "primary": "25-40 岁男性/女性，渴望现实逆袭/职场翻盘/情感现实题材",
            "core_fantasy": "在压抑的现实里靠能力/运气翻盘，或看清人情世故",
            "secondary": "都市读者最敏感的是'细节真实'——一瓶水的牌子比一套房的描写更可信",
        },
        "tone_preference": "克制、贴近现实；不悬浮的豪门/不狗血的多角恋；笑点真实",
        "taboo": [
            "悬浮豪门（主角月薪三千却住别墅没解释）",
            "假装现实（对话全是金句，真实人不这么说话）",
            "霸道总裁的样板戏",
            "职场戏写得像职场剧（开会/PPT/汇报）而非人物关系",
        ],
        "show_item_examples": [
            "穷 → 五口人面对桌上四个鸡蛋的态度（不是家徒四壁）",
            "想家 → 呵护三年五年没舍得穿的一双新鞋，要打算回家穿的",
            "职场翻盘 → 主角第一次坐进那个工位时，比同事早到了三十分钟",
            "人情冷暖 → 主角落难时，朋友圈里点赞数下降到个位数",
            "真心 → 朋友没有安慰主角，只在他桌上放了一包他常抽的那个牌子的烟",
        ],
        "research_strength": "weak",
    },
    "lishi": {
        "genre_key": "lishi",
        "genre": "历史",
        "reader_persona": {
            "primary": "30-50 岁男性为主，熟读三国/明末/晚清，喜欢代入平民视角看大时代",
            "core_fantasy": "在乱世里保家而非建国，或用现代知识撬动小范围命运",
            "secondary": "历史读者会为了一个朝代细节弃书——职官/物价/服饰/地理都需严谨",
        },
        "tone_preference": "沉郁克制，不夸张不套路；家国情怀通过小人物传递",
        "taboo": [
            "穿越即无敌（开篇就碾压古人）",
            "后宫线（与多位女主无铺垫暧昧）",
            "帝王将相视角（主角视角=皇帝/权臣=失去代入感）",
            "朝堂权谋为主线（忽视民间）",
            "现代思维穿越古代无成本（不考虑语言/习俗/疾病）",
        ],
        "show_item_examples": [
            "想家 → 主角在行囊底层放着一双母亲做的、三年没舍得穿的新布鞋",
            "忠诚 → 临死前托付的不是军情，而是母亲的牌位",
            "乱世 → 主角经过的村庄，灶台上的灰还是温的（人刚走不久）",
            "忠孝两难 → 主角没有回头，只把铠甲下的内衬翻了一下，露出母亲绣的那个字",
            "穷 → 主角一家五口人煮了一锅野菜，只有主角碗里有一点米",
        ],
        "research_strength": "strong",
    },
    "junshi": {
        "genre_key": "junshi",
        "genre": "军事",
        "reader_persona": {
            "primary": "25-45 岁男性，多为军迷/历史迷，对战术/装备/编制有专业判断",
            "core_fantasy": "从底层士兵靠战功崛起，或小部队在绝境中完成不可能任务",
            "secondary": "军事读者最难糊弄——一个错误编制/装备型号/战术动作会被立即弃书",
        },
        "tone_preference": "硬朗、写实；战术细节比情感戏更重要；战友情通过生死传递",
        "taboo": [
            "装备/编制穿帮（95 式步枪出现在 1980 年战场）",
            "战术神剧（一个人端掉一个连）",
            "军衔混乱（上等兵下达连长命令）",
            "为爽而爽（牺牲战友只为主角升级）",
            "抗日神剧化（手撕鬼子/裤裆藏雷）",
        ],
        "show_item_examples": [
            "战友情 → 主角复盘时发现，每次战斗老兵都站在他左后方三步（掩护位）",
            "紧张 → 班长没有说话，只把全班的水壶都拧紧了盖子",
            "牺牲 → 战友没有遗言，主角在他手里发现一张照片背面写着'妈'",
            "装备细节 → 主角检查枪栓时手指的节奏（熟练度比型号更可信）",
            "撤退 → 全连没有回头，只把鞋底沾的泥故意甩在路边（给后续部队留路标）",
        ],
        "research_strength": "strong",
    },
    "kehuan": {
        "genre_key": "kehuan",
        "genre": "科幻",
        "reader_persona": {
            "primary": "20-40 岁男性/部分女性，理工背景为主，关注硬科幻设定/文明尺度",
            "core_fantasy": "用科技解释未知，或在星际/末世尺度上重新理解'人是什么'",
            "secondary": "科幻读者会用物理/生物/信息论常识验证设定——一个错误公式就足以弃书",
        },
        "tone_preference": "冷峻、克制；不滥用'宇宙意志/命运'等玄学；尊重科学边界",
        "taboo": [
            "物理硬伤（超光速/永动机被无成本使用）",
            "降智反派（高等文明像地球人一样阴谋）",
            "末日无成本（资源/人口/疾病/心理成本被忽略）",
            "AI=人类情感（强 AI 哭笑像人=失去科幻质感）",
            "穿越+科幻混用（混淆世界观）",
        ],
        "show_item_examples": [
            "孤独 → 主角在飞船上养了一盆植物，每天给它浇水（飞船已离开母星 12 年）",
            "AI 觉醒 → 飞船 AI 没有回答指令，主角的茶杯上多了一行字（无解释）",
            "文明差距 → 主角没有惊恐，只把探测器收进袖口（动作已经说明他知道对方级别）",
            "末世 → 主角经过的街道，每家门口都摆着一模一样的鞋（集体疏散的痕迹）",
            "时间膨胀 → 主角收到地球的回信，信封上的邮戳是 7 年前",
        ],
        "research_strength": "medium",
    },
}


ALL_GENRE_KEYS: tuple[str, ...] = tuple(_GENRE_TEMPLATES.keys())


def get_genre_template(genre_key: str | None) -> dict | None:
    """按 genre_key 取题材模板。

    Args:
        genre_key: 题材英文 key（xuanhuan/xianxia/dushi/lishi/junshi/kehuan），
                    接受 None / 空字符串 / 未知 key → 返回 None（让上层 catch）。

    Returns:
        题材模板 dict 或 None（key 不存在时）。
    """
    if not genre_key:
        return None
    return _GENRE_TEMPLATES.get(genre_key)


def list_genre_keys() -> list[str]:
    """返回全部支持题材 key（顺序稳定）。"""
    return list(ALL_GENRE_KEYS)