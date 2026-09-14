<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 05`（无人值守）或在 Hermes 里直接说「做卡 05」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #05】实现 T6–T9（收款人解析与转账，含预览-确认-执行三段式）
范围：tools/transfer.py、tests/test_tools_transfer.py
要求：
1. resolve_payee：支持按姓名/手机号模糊匹配；同名多个返回 ambiguous=True 并给出候选，不得擅自选一个
2. preview_transfer：只算不执行。生成 preview_token（存内存，TTL 300 秒，**绑定 payee_id+amount**）；
   返回 fee、tier（按规格第 5 节权限矩阵算）、requires_otp、剩余限额；不写任何流水
3. execute_transfer：校验 token 未过期且参数一致 → 校验 OTP（L2 时）→ 事务内扣款+写流水+写审计 → 返回 txn_id
   **幂等**：同一 token 重复调用必须返回同一结果，绝不重复扣款
4. 余额不足返回 ok=False, error_code=INSUFFICIENT_FUNDS；超限返回 OVER_LIMIT；token 失效返回 TOKEN_EXPIRED
5. create_aa_request：拆分金额用整数分均摊，余数给发起人，合计必须精确相等
单测（必须包含）：并发/重复调用同一 token 只扣一次款；金额加总一致性；同名收款人歧义
禁止：在 execute 里调用任何 LLM；禁止浮点运算金额
交付：按模板。
