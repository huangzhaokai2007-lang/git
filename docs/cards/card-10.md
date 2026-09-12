<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 10`（无人值守）或在 Hermes 里直接说「做卡 10」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #10】接通 L1/L2 写操作：智能转账端到端
范围：agent/orchestrator.py、agent/confirm_card.py、guard/permission.py、tests/
要求：
1. 新增状态 CONFIRM_CARD 与 PENDING_REVIEW；写操作**必须**经过
2. confirm_card.py 渲染确认卡文本：意图 + 金额 + 收款人（含脱敏手机号）+ 预计到账 + 风险提示；
   用户回复"确认"才继续；回复其他内容 → 回到 SLOT_FILL
3. guard/permission.py：按规格第 5 节的档位表 + 降级因子计算 tier；命中 ≥2 个降级因子 → 转人工
4. L2 要求 OTP（demo 固定 123456，错误 3 次锁定该会话）；L3 延迟 60 秒生效并给"撤销"入口
5. OTP 校验、幂等、审计由 orchestrator 统一处理，工具层不重复做
单测（必须包括）：
   - 未确认就执行 → 断言 executed=False
   - OTP 错误 → 不执行
   - 同一 preview_token 确认两次 → 只扣一次钱
   - 新收款人 + 夜间 → tier 升到 L3 或转人工
交付：按模板。
