<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 03`（无人值守）或在 Hermes 里直接说「做卡 03」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #03】实现 DAO 层
范围：data/dao.py、tests/test_dao.py
要求：
每个 DAO 函数只做一件事，返回纯 dict 或 Pydantic 模型，不做业务判断：
get_balance(account_type) / list_txn(...) / sum_by_category(period) /
find_payee(query) / get_card(card_id) / update_card(card_id, **fields) /
list_subscriptions(user_id, status) / get_subscription(sub_id) / update_subscription(...) /
list_products(risk_level=None) / get_product(product_id) /
insert_txn(...) / insert_audit(...) / insert_risk_event(...)
单测：每个函数至少 3 条（正常 / 边界如空结果 / 非法参数）
禁止：在这里做权限判断或风控判断（那是 guard 的活）
交付：按模板。
