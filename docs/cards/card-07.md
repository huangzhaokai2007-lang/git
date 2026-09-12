<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 07`（无人值守）或在 Hermes 里直接说「做卡 07」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #07】实现 T13–T16
范围：tools/wealth.py、tools/cross_scene.py、tests/
要求：
1. assess_risk：5 题问卷 → **代码按计分规则**得出 R1–R5（规则表写在代码注释里），LLM 不参与判定
2. recommend_wealth：只返回 risk_level ≤ 用户等级的 产品，按收益排序；**不得推荐超风险等级产品**
3. trade_wealth：买入前校验风险等级匹配 + 余额充足；卖出按 product 的赎回规则
4. plan_gift：跨场景联动——锁定资金（lock_id）+ 生成 mock 预订清单（鲜花/蛋糕），返回 total 与 items
单测：风险等级越界的产品必须被过滤；未成年人/未测评用户的边界
交付：按模板。
