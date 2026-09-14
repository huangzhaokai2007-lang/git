<!-- 追加卡（非剧本拆分）：拆源文件收口 300 行 + 常量改名。最后一张清理卡，做完直进 card-06 不回头。分析师 14:05 决策、reviewer 定死验收门 -->

> 用法：`bash scripts/run-card.sh 05b`（无人值守）或在 Hermes 里直接说「做卡 05b」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #05b】拆源文件收口 300 行 + 常量改名（最后一张清理卡）
范围：data/_dao_core.py（新）、tools/_query_analysis.py（新）、tools/_transfer_risk.py（新）、data/dao.py、tools/query.py、tools/transfer.py、tests/test_dao.py、tests/test_query_analysis.py（新）、tests/test_transfer_risk.py（新）
要求：
1. 拆 `data/_dao_core.py`：把 `data/dao.py` 的 `_connection`/`_connection_path` 全局 + `connect_db/close/connection/_one/_many/_writing/_insert/_apply_update` + 全部校验 helper（`_text/_cents/_choice/_iso_date/_stamp/_period_range/_json_text` 等）**一起**搬进去。`dao.py` 只留 14 个公开函数 + 反向 import 并 re-export `connect_db/close/connection`。依赖单向 dao→core，禁反向。
2. 拆 `tools/_query_analysis.py`（收 query.py 的分析逻辑：`_baseline_series` / `_history_mean_cents` / 阈值检测等）与 `tools/_transfer_risk.py`（收 transfer.py 的权限档/风控逻辑：tier 计算 / new_payee 判定 / 降级因子等）。两者依赖 `tools/_query_common` **单向**，禁循环 import。
3. `VELOCITY_WINDOW_MINUTES` 拆两个名字：`VELOCITY_MINUTES_T4`=60（只读检测）/ `VELOCITY_MINUTES_WRITE`=10（§5 写降级）；改完**逐引用点核对**，别把 T4 和 §5 阈值搞反。
4. `tests/test_dao.py`（525 行）再拆 ≤300（如 `tests/test_dao_core.py`）；新增拆分文件的测试归到 `tests/test_query_analysis.py` / `tests/test_transfer_risk.py`。
5. **行为保持**：全仓 377 测试必须原样绿、零丢用例（`comm -23` 对比旧函数名）；T1–T9 公开签名与 data 字段名零改动。
禁止：改 T1–T9 公开接口签名或 data 字段名；加新依赖；做权限/风控判断（那是 guard 的活）；金额用 float。
验收门（reviewer 定死）：
  ① dao.py 拆分的连接全局必须与写原语一起进 `_dao_core.py`，只拆校验 helper 会变「两份连接状态」→ `test_writes_join_an_outer_transaction_and_roll_back_together` 当场红；
  ② `_query_analysis` / `_transfer_risk` → `_query_common` 依赖单向，反向即 FAIL；
  ③ VELOCITY 两个名字逐引用点核对，阈值不搞反；
  ④ 行为保持 377 绿 + 零丢用例，reviewer 会重跑拆分完整性核对（`comm -23`）+ 对 TOCTOU 锁与 DAO 原语重跑变异。
交付：按模板。
