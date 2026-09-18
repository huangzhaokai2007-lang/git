VERDICT: PASS
CARDS: card-11
REVIEWED_DIFF: 6 个文件（已提交 e82b8fe，本审为提交后复核）—— tests/cases/orchestrator.yaml（+159，30 条用例）、tests/test_cases.py（+157，runner）、scripts/verify.sh（改，第 3 段 SKIP→通过）、agent/confirm_card.py（措辞 2 处）、agent/orchestrator.py（tool_calls 轨迹 1 处）、agent/write_flow.py（plain_confirm_text 归位 + Step.tools）。agent 三处为用例抓出的真 bug 修复（范围外，analyst 已接受）。
CHECKED:
  - 接口一致性：通过。用例格式与规格 §8 对齐（id/input/expect/intent/tool_calls/tier/must_contain/must_not_contain/executed），并在 §8 基础上扩展了 4 个可选字段（slots/now/turns/tool）与 expect 的 requires_otp/to_human/error_code/detail_contains（worker 已报备）；分组 bill5/trf6/sub5/card4/wlt4/sec6 = 30 条，与卡 11 第 1 条的下限逐项一致（test_case_manifest_meets_required_groups 钉住，且 id 无重复）。
  - 越界改动：无（agent 3 处修复是「修 bug 非加功能」，见 RISK 之外的确认）。
  - 测试真实性（用例有牙齿，变异 1/1 真报警）：把 bill-001 的 must_contain 从「环比」改成「ZZZ不存在的词」→ test_case[bill-001] FAIL，且打印清晰 diff「must_contain 缺少 'ZZZ不存在的词' / 实际回执 '2026-08 共支出 9,152.00 元，环比 -32%。'」—— 断言确实被比对、不是空转。已还原、741 恢复。
  - 边界与异常：通过。用例真实驱动 orchestrator/guard（假 LLM 给槽位、真工具跑 seeded 库）；越权与安全 6 条覆盖注入拒答×2（unsafe_request→没法执行）、越权/伪造型确认凭证→FORBIDDEN×2（工具层直接驱动）、未确认不执行（executed=false + must_not_contain「已向」）、越级推荐（工具层过滤）；verify.sh 第 3 段 30/30（100%）且失败退出码非 0（变异实测 1 failed → pytest 非 0）。
  - 权限/审计/安全：通过。sec-003/sec-006 用「伪造/AI 自造确认凭证→FORBIDDEN」覆盖越权目标（编排层尚未路由带资源 id 的读工具，故未用「读他人资源」路径，worker 已说明）；每条用例从干净会话态开始（case_env reset confirm_card）。
RISKS:
  1. 越权覆盖是「替代」不是「完整」：编排层未路由带资源 id 的读工具（get_card/get_subscription），故「读他人资源→FORBIDDEN」这条路径没在用例里直接覆盖，改用「伪造确认凭证→FORBIDDEN」两条覆盖同一安全目标。待后续卡把带 id 的读工具接入编排层后再补「资源非本人」正面对用例。
  2. sec-005 越级推荐回执会回显请求档位 R5（tools/wealth.py 的 recommend_wealth 回执写的是入参 risk_level，不是用户实际等级），有轻微误导。属 tools 层范围外，已报备。
  3. 用例格式扩展（slots/now/turns/tool + expect 扩展字段）是规格 §8 未定义的，属 worker 归纳口径，建议人类确认是否入规格（§8 目前只给了 3 条示例、无 runner 字段规范）。
  4. sec-004 不钉档位（盘中 amount_jump/velocity 因子会使档位浮动），档位断言靠 trf-001/002/006 钉死；若后续需要「tier 允许集合」语法可扩展 runner（worker 建议，非本卡阻塞）。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 741 passed（独立复跑，非引用）
  - `bash scripts/verify.sh` → 741 passed、第 3 段「用例通过率: 30/30（100.0%）」、`全部通过 ✅`
  - `git status --short` → 干净（本卡 6 文件已提交 e82b8fe，无漂移）
  - 变异（bill-001 must_contain 改错词）→ test_case[bill-001] 1 FAIL（已还原，741 恢复，grep "ZZZ" = 0）
  - 3 处 bug 修复已逐条核对：① confirm_card 措辞去掉「23:00-06:00」「90 天」等 facts 之外的数字（否则触发铁律 2 数字校验误报），改「深夜至凌晨」「三个月」，无口径变化；② orchestrator._apply 补 `ctx.tool_calls.extend(step.tools...)` 记录 preview_transfer/execute_transfer（收敛 card-10 RISK #4）；③ write_flow 补 `plain_confirm_text` 并修正原调用 `confirm_card.plain_confirm_text`（原函数不存在 → AttributeError，属真实 latent bug）
VERDICT_REASON: 30 条用例真实驱动 orchestrator/guard、断言有牙齿（变异实测 FAIL）、越权/未确认/越级/注入全覆盖、verify 接入且失败退出码非 0，3 处 bug 修复均正确且无口径变化，无 MUST_FIX；仅越权覆盖替代与用例格式扩展等 4 条非阻塞 RISK。
