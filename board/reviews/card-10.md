VERDICT: PASS
CARDS: card-10（含 10b write_flow 拆分）
REVIEWED_DIFF: 6 个文件（已提交 fdc3865，本审为提交后复核）—— agent/orchestrator.py（改，写路径分流 + _read_flow/_apply）、agent/confirm_card.py（新，264 行）、agent/write_flow.py（新，257 行）、guard/permission.py（新，128 行）、tests/test_confirm_flow.py（新，195 行）、tests/test_orchestrator_readonly.py（改，机械改名）。全 ≤300 行、函数 ≤40。
CHECKED:
  - 接口一致性：通过。guard.permission 复用 tools._transfer_risk._assess（§5 内核唯一实现，不另写阈值表，避免两份漂移）；INTENT_BASE_TIERS 逐字来自 §5 表（调额/取消代扣/申购赎回→L2、挂失/解锁→L3）；未登记写意图 fail-closed 取 L3+转人工；TierVerdict 字段（tier/factors/escalation/requires_otp/to_human/delayed/source）自洽。
  - 越界改动：无。写路径只 import agent/guard/tools（无反向回边，write_flow 不 import orchestrator）；确认卡数字全部来自预览事实包（card_numbers_outside_facts 守门）。
  - 测试真实性（变异抽查 2/2 真报警，均已还原）：① guard.permission fail-closed 把 L3 改成 L1 → test_guard_escalation_rules_are_pure_code 1 条 FAIL（纯代码定档是真的）；② write_flow.yuan_to_cents 整数乘数 100→10 → test_yuan_to_cents_uses_integer_math 1 条 FAIL（元→分整数运算是真的）。还原后 709 passed 恢复。
  - 边界与异常：通过。铁律 3 四步走一步不少（SLOT_FILL→PRECHECK→CONFIRM_CARD/PENDING_REVIEW→EXECUTE）；未确认不执行（余额/流水不变）；OTP 错 3 次锁会话（剩余次数代码计数）；L3 不自动放行（PENDING_REVIEW + 60s 撤销窗口 + 撤销入口，别人的编号撤销不了）；幂等（同 preview_token 只扣一次，工具层保证，流水=1 审计不新增）；金额元→分字符串整数运算禁 float（0.1 元=10 分不踩浮点坑）；收款人脱敏（mask_phone 保留前3后4）；同名收款人歧义绝不擅自选（追问）。
  - 权限/审计/安全：通过。tier 判定纯代码（LLM 无权干预）；档位/因子复用工具层预览事实包；每请求 audit_log（铁律 5）；确认卡数字全部可溯（铁律 2）；OTP 校验复用 execute_transfer（不重写比对）；新收款人+夜间→L3 且 factors 明细可断言（night）。
RISKS:
  1. 【延续 card-09】agent 直调 data 层未收敛：orchestrator.py 仍 `from data import dao` + `_write_audit` 里 `dao.insert_audit(...)`，以及 `from data.seed import AS_OF`。card-09 已记硬 TODO「卡 13 收敛」，本卡未动（写路径同样直调 dao 写审计）。判定延续 RISK 不阻塞，但卡 13 必须一并收口。
  2. L3 无真正放行口：PENDING_REVIEW 登记了 60s 窗口 + 撤销（due() 会判到期、revoke() 会撤销），但窗口到期后没有「自动放行执行」的路径（放行口留给后续卡）。当前 L3 转人工后实际不会执行，属已登记的遗留项。
  3. 会话态进程内存：confirm_card 的 _CONFIRMATIONS/_OTP_ERRORS/_LOCKED/_PENDING 都在进程内存，重启即清、多 worker 不共享。demo 口径已记，生产需共享存储。
  4. resolve_payee 未计入 Turn.tool_calls：write_flow.resolve_target 调 transfer.resolve_payee 解析收款人，但该调用没进 tool_calls 轨迹（只有 preview_transfer/execute_transfer 计入）。属可观测性缺口，不影响正确性。
  5. expected_arrival 为 demo 措辞（L3「人工复核通过后实时到账」、其余「实时到账」），§5 未定义到账口径。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 709 passed（独立复跑，非引用）
  - `bash scripts/verify.sh` → 709 passed、3/5–5/5 SKIP、`全部通过 ✅`
  - `git status --short` → 仅 ?? board/reviews/card-09.md（本卡 6 文件已提交，无漂移）
  - 变异①（fail-closed L3→L1）→ test_guard_escalation_rules_are_pure_code 1 FAIL（已还原）；变异②（yuan_to_cents 100→10）→ test_yuan_to_cents_uses_integer_math 1 FAIL（已还原）
  - 还原后复跑 709 passed；grep 确认 TIER_ORDER[-1] 与 int(whole)*100 均恢复
  - worker 报的 error_code_of 修复已核对：`str(getattr(code, "value", code))` 兼容枚举与裸字符串，orchestrator._code 单点转发，无口径变化
VERDICT_REASON: 6 文件范围干净、709 测试稳定全绿、铁律 3 四步走/纯代码档位判定/元→分整数运算/OTP 复用工具层/幂等/L3 不自动放行全部落实且变异抽查证伪有效，无 MUST_FIX；仅越层延续与 L3 放行口等 5 条非阻塞 RISK。
