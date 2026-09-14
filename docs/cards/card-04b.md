<!-- 追加卡（非剧本拆分）：工具层清理，为 card-06 高危写路径铺共享 helpers。分析师 13:43 决策批准，@user 指定分析师建卡 -->

> 用法：`bash scripts/run-card.sh 04b`（无人值守）或在 Hermes 里直接说「做卡 04b」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #04b】工具层清理：抽共享 helpers + 拆测试 + DAO 写原语 + 幂等并发修复
范围：tools/_query_common.py（新）、tools/query.py、tools/transfer.py、tests/test_tools_query.py、tests/test_tools_transfer.py、tests/test_query_common.py（新）、data/dao.py、tests/test_dao.py
要求：
1. 抽 `tools/_query_common.py`：把 `tools/query.py` 与 `tools/transfer.py` 里重复的 `_ok / _fail / _invalid / _money / _owned_account_ids / require_owned / PCT_TOTAL` 收敛成**单份共享**；两个调用文件改 `from tools._query_common import ...`，删除本地副本。依赖单向，禁 `_query_common` 反向 import 调用方。
2. 拆测试文件：`tests/test_tools_query.py`（651 行）与 `tests/test_tools_transfer.py`（604 行）拆到各自 ≤300 行；共享 helper 的用例收到新增 `tests/test_query_common.py`。**不豁免** 300 行上限。
3. `data/dao.py` 补两个原语 `get_payee(payee_id)` 与 `update_account_balance(...)`，替换 `tools/transfer.py` 里 `_payee_by_id / _debit` 对 `dao.connection()` 的直查（消掉 TODO dao-05b 的层级绕过）。参数化 SQL、金额整数分、无注入。
4. 幂等并发修复（TOCTOU）：`tools/transfer.py` 的 `_TOKENS` 加 `threading.Lock`；`execute_transfer` 里 token state 的翻转**挪进事务内**，消除「两线程同时 execute 同 token → 双重扣款」窗口。
5. **行为保持**：全仓 354 测试必须原样绿；T1–T9 公开函数签名与 data 字段名零改动。
单测（必须包含）：
   - TOCTOU 真并发用例：同 token 双线程同时 `execute_transfer`，断言只扣一次款（用 threading + barrier 制造确定性竞争）。
   - `get_payee` / `update_account_balance` 的正常 / 边界（不存在 id）/ 非法参数。
   - 跨模块 helper 一致性：`test_money_and_ownership_helpers_agree_with_query_module` 这类断言保留且仍绿。
禁止：改 T1–T9 公开接口签名或 data 字段名；加新依赖；做权限/风控判断（那是 guard 的活）；金额用 float。
交付：按模板。
