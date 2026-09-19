VERDICT: FAIL
CARDS: card-19b
REVIEWED_DIFF: 已提交 3ca70d4（6 文件），本审为提交后复核：tests/test_web_layering.py(224→268，改：扫 `interfaces` + `app`)、tests/test_cli.py(98，新)、tests/test_demo.py(82，新)、scripts/verify.sh(81，改：第 4 段清 key)、README.md(309→290，改)、.dockerignore(13→21，改)。基线 997 passed（=970+27 新，未破）。
CHECKED:
  - 接口一致性：通过。README 290 行（≤300 ✓）；`.dockerignore` 硬化（+.env.*/.env.local/*.key/*.pem/credentials*）。
  - 越界改动（红线 1）：`tests/test_web_layering.py` 现 `LAYER_DIRS = (INTERFACES, APP_DIR)`，`layer_sources()` 用 `rglob` 扫两棵树；`test_the_scan_really_covers_the_interface_tree` 断言 `{"app.py","components.py","redteam_page.py","cli.py"} <= names`（防空扫描假绿），新增 `test_the_scan_also_covers_the_app_root`。**变异自检**：给 `app/cli.py` 加 `import tools` → `test_layer_tree_never_imports_tools_data_guard_or_sqlite3`（`app\cli.py: tools`）+ `test_the_scan_also_covers_the_app_root` **2 条真变红**（已还原）。守卫扩 app/ ✓。
  - 测试真实性（红线 3）：新增单测独立复跑 `uv run pytest tests/test_cli.py tests/test_demo.py` → **26 passed**（analyst 报 27，实为 26：CLI 18 + demo 8，差 1 属计数笔误，不影响内容）。用例有牙：`test_every_offline_rule_is_reachable`（自证规则表不是摆设）、`test_main_offline_uses_the_real_tool_and_exits_zero`（回执余额与 `conftest.balance` **独立复算**比对）、`test_section_write_really_executes_the_transfer`（余额真减 10,000 分）、`test_inprocess_sections_all_pass`（三段演示在干净临时库必须全绿）。
  - 边界与异常（红线 2）：verify 第 4 段改为 `LLM_API_KEY= $PY -m app.cli "…"`（清空 key）——**实测确实离线**（无 key → 3 次重试失败 → 降级）。但见 MUST_FIX 1。
  - 权限/审计/安全：`app/` 纳入 AST 守卫后，`app/cli.py` 的分层与 SQL 违规都会被机器抓（此前只能人眼）。README 精简 19 行仍保留全部关键事实。
RISKS:
  1. **项目未收尾**：card-19 的 MUST_FIX 在 HEAD 中**仍未闭环**——`docs/答辩提纲.md` line 47 依旧写「证明：现场转 600 元 → 档位升到 L2 且要求 OTP」，而实测 600 元 → `OVER_LIMIT`、`tier=None`（>单笔上限 500 元）。见 MUST_FIX 2。
  2. 新增单测数 26 ≠ analyst 报的 27（计数笔误，内容无缺）。
  3. test_demo.py 覆盖 `section_im/guard/write` 三段，**网页端那一段（section_web）不进单测**（Streamlit AppTest 开销大，仍由 `scripts/demo.py` 本体 + 卡 16 界面自检兜）——已在文件 docstring 说明，可接受。
  4. `app/cli.py` 的离线替身规则表是**手工维护**（关键词 → 意图）：新话术认不出 → out_of_scope 追问；不像分类器那样能泛化。演示/验收够用，属设计取舍。
MUST_FIX:
  1. `scripts/verify.sh` 第 4 段（**本卡自己的改动**）削弱了验证力：清空 key 后 `app.cli` 离线降级，实测输出 `没太理解您的意思…`（`intent=out_of_scope`、`工具=-`）——**只验证了降级链路，完全没碰工具层**；而卡 19 当时的同一条命令（不清 key）走的是真实工具链（回执 `2026-08 一共支出 9,152.00 元，环比下降 32%`）。于是「CLI → 编排层 → 工具体系」这条链路的回归**在 verify 里抓不到了**。改法：第 44 行改 `$PY -m app.cli --offline "帮我看看上个月花了多少"`（`--offline` 保留真实工具链、且仍不依赖外网），并把 rc-only 升级为**内容断言**（回执含 `2026-08`、轨迹含 `analyze_spending`）。改完 `bash scripts/verify.sh` 第 4 段应打印真实账单回执、rc=0。（= analyst 已派的那条补丁。）
  2. `docs/答辩提纲.md` §1.3 line 47：card-19 的 MUST_FIX 未闭环。把「现场转 600 元 → 档位升到 L2 且要求 OTP」改成实测成立的说法（如「转 100 元 → 新收款人 → `L2` + OTP；转 600 元 → 单笔超限 `OVER_LIMIT`（不落库）」），并把档位行的「L2 … 金额 > 500 元」注明「受单笔上限遮蔽、代码不可达」。根因是**规格 §5 自身矛盾**（L2「>500元」 vs 硬约束「单笔 ≤500元」），请 analyst 一并走 SPEC-CHANGE。（= analyst 已派的另一条补丁。）
EVIDENCE:
  - 变异：`app/cli.py` 加 `import tools` → `test_web_layering` **2 failed**（`app\cli.py: tools`）（已还原）
  - `uv run pytest --tb=no` → **997 passed**；`uv run pytest tests/test_cli.py tests/test_demo.py` → 26 passed
  - verify 第 4 段实跑：`LLM_API_KEY= uv run python -m app.cli "帮我看看上个月花了多少"` → `意图=out_of_scope 工具=- 已执行=否`、rc=0（**降级链路，未碰工具**）
  - `grep -rn "变异 M" app/ tests/ scripts/` → 无残留；`wc -l README.md` → 290
VERDICT_REASON: card-19b 自身 6 文件基本达标（守卫扩 app/ 且变异真红、26 条新单测有牙、README 290≤300、.dockerignore 硬化），但**红线性质的两处未闭环**：① 本卡的 verify 第 4 段把「真实工具链」换成「降级链路」，verify 从此抓不到 CLI→工具体系的回归；② card-19 的 MUST_FIX（答辩提纲 §1.3 的 600元→L2）在 HEAD 中仍未修，项目不能算「收尾」。两条补丁都是 1~2 行，落地后我复跑即可 PASS。
