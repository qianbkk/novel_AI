ROLE_REGISTRY = [
    {"role_key": "structured_logic", "label": "结构逻辑"},
    {"role_key": "creative_detail", "label": "创意细节"},
    {"role_key": "consistency_check", "label": "一致性复核"},
    {"role_key": "orchestrator", "label": "总控编排"},
    {"role_key": "planner", "label": "设定规划"},
    {"role_key": "outline", "label": "大纲拆章"},
    {"role_key": "writer", "label": "正文写作"},
    {"role_key": "normalizer", "label": "文本规范化"},
    {"role_key": "compliance", "label": "平台合规"},
    {"role_key": "checker_main", "label": "主检查器"},
    {"role_key": "checker_cross1", "label": "交叉检查一"},
    {"role_key": "checker_cross2", "label": "交叉检查二"},
    {"role_key": "rewriter", "label": "改写润色"},
    {"role_key": "tracker", "label": "记忆追踪"},
    {"role_key": "summarizer", "label": "摘要归档"},
]


ROLE_KEYS = {item["role_key"] for item in ROLE_REGISTRY}
