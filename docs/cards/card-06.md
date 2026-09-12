<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 06`（无人值守）或在 Hermes 里直接说「做卡 06」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #06】实现 T10–T12
范围：tools/subscription.py、tools/card.py、tests/
要求：
1. list_subscriptions：并自动标注"疑似僵尸订阅"（最近 3 个月无对应使用记录）
2. cancel_subscription：必须校验 confirm_ref 来自确认卡，否则 FORBIDDEN；写审计
3. manage_card：apply / adjust_limit / set_txn_limit / lock / unlock / report_lost
   —— 每个 action 的权限档按规格第 5 节；report_lost 标 L3（延迟 60 秒生效 + 可撤销）
4. 所有状态流转要合法（已挂失的卡不能解挂，只能补卡 → 返回 INVALID_STATE）
单测：每个 action ≥3 条，含非法状态流转
交付：按模板。
