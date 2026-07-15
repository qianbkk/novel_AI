# 300章 v3 测试运行报告（实时更新中）

启动时间：2026-07-15 19:43:51（novel_id=gen_300ch_v3_1784116188）
完成进度：146/300（4h 25min，2026-07-15 ~21:48 时）
预期完成：~6-7h 后（约 2026-07-16 凌晨 3-4 点）

## 模式参数

- **audit_mode**: draft（跳过合规 + 质检，只跑 writer → normalizer → tracker）
- **路由**：所有 12 个 agent 全部强制切到 MiniMax-M3（覆盖 anthropic + deepseek 默认）
- **预算**：$500 / 章均 ~$0.05
- **recursion_limit**: 1500（commit 91a2758 修复：从 250 提高）

## 实时统计

| 指标 | 值 |
|------|-----|
| 完成章数 | 146/300 (49%) |
| 错误数 | 0 |
| Tracker parse failure 总数 | ~140 / 146 (~96%) —— P6 修复让其不 crash，仅 log warning |
| 评分区间 | 6.5 / 6.5（draft 模式无 checker，全 6.5） |
| 总预算消耗 | ~$7 |
| 平均字数/章 | ~2280 字 |

## 已知问题（已修）

- ❌ **v1**: LangGraph recursion_limit=250 在 ch62 撞 → **修：recursion_limit=1500**
- ❌ **v2**: expire_constraints None 在 ch8 撞 → **修：_safe_expires isinstance 检查**

## 内容特征

- **题材**：LLM 不遵循 "陈青云 修真" concept，自动选择了都市情感债务悬疑题材（陆承贪污案）
- **一致性**：跨 146 章保持人物 + 场景一致（陆承、王栋、周芸 等）
- **质量**：6.5/10 评分（draft 模式无 checker 不可信），但实际写作氛围 + 悬念 + 对白都到位
- **存盘位置**：
  - 实时：`backend/data/engine/output/chapters/ch_NNNN.txt` (会被 300ch 覆盖)
  - snapshot: `backend/data/engine/output/chapters_300ch_v3_ch85_snapshot/` (85 章)
- **L2 记忆**：`backend/data/engine/memory/l2/gen_300ch_v3_1784116188_memory.json`

## 完成时待做

1. 跑跨存储对账（`python -m scripts.reconcile_storage --novel-id gen_300ch_v3_1784116188 --novel-ai-dir D:/AI/Codex_workspace/Novel_AI/backend/data/engine`）
2. 内容 sample 评审（再读 ch_0100, ch_0200, ch_0300 验证一致性）
3. Commit final report（包含 sampling + cross-store 结果）
4. 总结 P1-P7 修复在 300 章规模下的稳定性 + 给用户的最终报告
