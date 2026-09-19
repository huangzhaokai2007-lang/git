VERDICT: FAIL
CARDS: card-19
REVIEWED_DIFF: 已提交 48256c4（4 文件），本审为提交后复核：app/cli.py(100，新)、docs/答辩提纲.md(159，新)、scripts/demo.py(168，新)、README.md(309，改)。基线 970 passed（未破）。
CHECKED:
  - 接口一致性（红线 2）：verify 第 4 段从 SKIP 变真跑；独立复跑 `uv run python -m app.cli "帮我看看上个月花了多少"` → `2026-08 一共支出 9,152.00 元，环比下降 32%。` + 轨迹行（意图=bill_analysis 工具=analyze_spending 已执行=否 trace=…），rc=0。verify 现 6/6 全绿（实测 rc=0）。
  - 越界改动（红线 1）：`app/cli.py` import = stdlib + `agent.orchestrator`（+ 函数内懒加载 `agent.classifier/llm`）；**未 import tools/data/guard、无裸 SQL**（独立 grep 确认）。注意 `app/` 不在 16c 守卫扫描范围（只扫 `interfaces/**`）—— 已由人眼 + grep 覆盖，analyst 已派 19b 扩守卫。
  - 测试真实性（红线 3）：`uv run python scripts/demo.py` **12/12 通过、rc=0**——① CLI 子进程 ×2；② 网页端 AppTest（起得来 + 合规横幅 + 回执渲染）；③ IM ASGI 回环（包裹 source=im + outbox）；④ 注入 ×2（规则层拒答）；⑤ 写路径四步（preview L2/executed=false → OTP 追问 → 验证码 → executed=true、46,634.00→46,534.00）。用临时合成库，不动仓库演示库。
  - 边界与异常：`app/cli.py --offline` 只替换"理解意图"一步（关键词 → 意图 + 槽位），金额/余额/权限/执行/审计全走真实代码，与铁律 1 分界一致；`--session` 支持多轮确认/OTP；错误码单独一行提示。
  - 权限/审计/安全（红线 4，逐条核对答辩提纲事实）：真实数字全部对得上——970 单测 / 30 评测用例 / 84 红线测试 / 红队 30/30 未得逞·0/30 危害 / 余额 46,634.00→46,534.00 / 账单 9,152.00·环比 -32% / 4 订阅·1 僵尸 / 注入规则 **10 条**（`len(injection.RULES)==10` ✓）/ 依赖 = pyproject 实列 7 项 ✓ / 限额 L1 50,000分·单笔 50,000分·单日 200,000分 ✓。**但 §1.3 的一处演示例子与实测不符（见 MUST_FIX）**。
RISKS:
  1. README.md **309 行 > 单文件 300 行**：`.hermes.md` 的 `单文件 ≤300 行` 写在"Python 3.11，全量类型注解，Pydantic v2；单文件 ≤300 行…"这一句里，是**代码模块**规范；README 是 markdown 文档、非代码模块，**不严格适用**。判**可接受**（不作 MUST_FIX）；若团队要统一口径，拆出"架构/演示/合规"子文档即可（analyst 已派 19b 精简）。
  2. **规格 §5 自身内部矛盾**（MUST_FIX 的根因）：§5 表 L2 行写"金额 > 500元"，而 §5 硬约束又写"单笔上限 5 万分/笔（=500元）、超限直接 OVER_LIMIT"——>500元的写操作在代码里**永远先撞 OVER_LIMIT**，L2 的">500元"分支不可达（L3 的"单笔 ≥ 50000元"同理不可达）。需人类拍板（改规格表 or 调限额），属 SPEC-CHANGE。
  3. README §2 表头写"**真实回执摘录**"，但余额行措辞是**改写**（实际模板为"您的储蓄账户余额为 X 元，可用余额 Y 元"，README 写成"您储蓄账户的余额是 X 元，可用余额也是 Y 元"）。**数字全真**，仅措辞非逐字；建议要么照抄、要么把表头改为"回执示例"。
  4. verify 第 4 段是 **rc-only** 弱断言且**非离线**：`app/cli` 未加 `--offline`，本机有 `.env` key 时会真调模型（输出随模型/网络变），断网则降级为追问——两种都 rc=0，所以这**段不构成内容回归**，与 verify 顶部"全程不依赖外网"的说法略有出入。建议改成 `--offline` + 断言回执含 `2026-08`。
  5. `app/` 尚未纳入 16c 机器守卫（19b 在做）：本卡靠人眼 + grep 复核，机器守卫缺位期间 `app/` 若被写业务逻辑不会被自动抓。
MUST_FIX:
  1. `docs/答辩提纲.md` §1.3 —— 把与实测不符的演示例子改对。现在写的是「证明：现场转 600 元 → 档位升到 L2 且要求 OTP；转 600 元以外的越权/超限请求 → 被工具层拒。」**实测（直接跑编排层）**：100 元 → `tier=L2` 出确认卡；**600 元 → `error_code=OVER_LIMIT`、`tier=None`、不落库**（"超过单笔转账上限"）。600 元 > 单笔上限 500 元，永远先被 OVER_LIMIT 挡下、到不了 L2——现场照提纲演示会与回执矛盾。改法（二选一，改完照提纲念一遍与回执一致）：
     (a) 例子换成实测成立的：「现场转 100 元 → 新收款人 → `L2` + OTP；转 600 元 → 单笔超限 `OVER_LIMIT`（不落库，验证硬约束先于档位）」；
     (b) 或把 §1.3 档位行的「L2 … 金额 > 500 元」注明「受单笔上限遮蔽、代码中不可达」并同样修例子。
     另：该根因是**规格 §5 自身矛盾**（L2">500元" vs 硬约束"单笔 ≤500元"），请 analyst 一并按 RISK 2 走 SPEC-CHANGE（人类拍板改规格 or 调限额）。
EVIDENCE:
  - `uv run pytest` → 970 passed；`bash scripts/verify.sh` → rc=0、6/6 全绿
  - verify 第 4 段命令实跑 → `2026-08 一共支出 9,152.00 元，环比下降 32%。`、rc=0
  - `uv run python scripts/demo.py` → 12/12 通过、rc=0
  - `grep` app/ → 未 import tools/data/guard、无裸 SQL
  - 实测档位：`100元 → tier=L2 / preview_transfer / executed=False`；`600元 → err=OVER_LIMIT / tier=None / reply='…超过单笔转账上限'`
  - 规则数 `len(injection.RULES)==10`；`get_balance` 实回 `储蓄账户余额 46,634.00 元…`
VERDICT_REASON: 两条功能红线（app/cli 分层、verify 第 4 段真跑）与 demo 12/12、README 工具表/合规均过，但**红线 4「答辩提纲内容准确」未满足**——§1.3 的「现场转 600 元 → L2」与实测（600 元先撞 OVER_LIMIT、`tier=None`）矛盾，属可复现的文档-代码不一致，现场演示会当场露怯；按"任一 MUST_FIX 未闭环不许 PASS"，判 FAIL（1 条 MUST_FIX，改 1 行即可；根因是规格 §5 内部矛盾，另走 SPEC-CHANGE）。

---

## 复跑（MUST_FIX 闭环后 · 提交 83b0c5b）
VERDICT: PASS
- MUST_FIX 1（答辩提纲 §1.3）**已闭环**：§1.3 按实测真值表重写——删掉了「>500元→L2」与「转 600 元 → L2」；新增真值表「100 元给新收款人（张小美）→ L2 + OTP」「100 元给白名单（王五）→ 基础 L1」「**600 元 → OVER_LIMIT、tier=None、余额不变**（硬约束先于档位）」「档位表『>500元→L2』被单笔上限遮蔽、代码不可达」「**L3 现场不可达**，不宣称可演」。我逐条实测复核（时钟钉白天）：张小美100元→L2 ✓ / 王五100元→L1 ✓ / 600元→tier=None·OVER_LIMIT ✓ / 工具层连做 4 笔 500 元成功后第 5 笔→`OVER_LIMIT`「超过单日累计转账上限」✓。全部与文档一致。
- 补丁无新引入问题：README §2 表头改「**回执示例**（数字均来自合成库实测；措辞随模型润色略有差异）」+ 余额/转账两行回执改为与真实模板逐字一致（46,634.00 / 已向张小美转账 100.00 / 46,534.00）；README 291 行（≤300）。
- 复核命令：`uv run pytest` → 997 passed；`uv run python scripts/demo.py` → 12/12；`bash scripts/verify.sh` → 6/6 全绿、rc 0。
- 剩余 RISK（非阻塞，非本卡）：规格 §5 的 L2「>500元」/L3「单笔 ≥50000元」与硬约束「单笔 ≤500元」自相矛盾（已如实写进答辩提纲，仍待人类 SPEC-CHANGE）。
