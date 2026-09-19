VERDICT: PASS
CARDS: card-16c
REVIEWED_DIFF: 已提交 9b14523（3 文件，+344/-19），本审为提交后复核。tests/test_web_layering.py（新，224：① 数字逐字回显 ② AST 分层/SQL 扫描）、tests/test_dao_threads.py（改，131→136：flaky 竞速用例→确定性锁语义）、scripts/mutcheck_16c.py（新，96：自带 5 变异自检）。基线 926=895+31。
CHECKED:
  - 接口一致性：守卫断言「数字只解析不重算」——`interfaces/**` 解析出的每个数字必须**逐字等于**回执文本里的字面数字（期望值来自本文件手写 `RECEIPT` + `EXPECTED_*` 字面量，**不复用被测实现算出的值**）。分层断言：`interfaces/**/*.py` 不得 import tools/data/guard/sqlite3、不得出现 SQL 语句。
  - 越界改动：16c 改了 16b 的 tests/test_dao_threads.py（analyst 已接受）——把「两线程 sleep 竞速」的 deadlock 用例换成**确定性锁语义**用例（`test_transaction_takes_the_write_lock_at_begin`：holder 进事务读一次后，另一连接的 `BEGIN IMMEDIATE`（busy_timeout=50ms）必须排队超时）。方向是**消除 flaky**，不是「改测试让测试通过」（我实测：新用例 12 连跑零 flaky；DEFERRED 变异 10/10 确定性变红）。
  - 测试真实性（守卫星真能被触发，独立抽验 3 处，均已还原）：① `components._yuan` 加 1.0 → test_web_layering **7 条变红**（逐字回显 / analysis 形状 / 4 条 _yuan 格式化 / 合计不等于分类之和）——正是 card-16 时全绿的那个变异，现在真被抓；② 往 components.py 加 `import tools` → test_interface_tree_never_imports_… 变红并报 `components.py: tools`；③ 往 app.py 加 `SQL = "SELECT * FROM txn"` → test_interface_tree_never_writes_sql 变红并报 `app.py: 'SELECT * FROM'`。另跑 worker 自带 `scripts/mutcheck_16c.py` → **5/5 变红**（逐字节还原 + sha256 复核、源码零改动）。
  - 边界与异常（AST 健壮性）：`forbidden_imports`/`sql_hits` 用 `ast.walk` 精确判定（Import/ImportFrom 的顶层根 + 只看字符串常量里的 SQL），**不是 grep**——app.py 文档字符串里正当提到「sqlite3」不被误伤（有 test_detector_does_not_flag_chinese_prose_or_docstrings 对照）。自证完备：`test_the_scan_really_covers_the_interface_tree` 断言真扫到 3 个已知界面文件且读到允许的 `from agent import`（防**空扫描假绿**）；元用例 `test_a_recomputed_number_would_be_caught` 把 `_yuan` monkeypatch 成「加 1」证明逐字比对非自证；`test_total_comes_from_the_receipt_line_not_from_summing_the_categories` 故意让合计≠分类之和，堵「就地重算出恰好相同结果」的洞。
  - 权限/审计/安全：守卫覆盖 `interfaces/**` 全树（含未来 IM 层），对 card-17 的 interfaces/im/*.py 自动生效。
RISKS:
  1. **AST 守卫只覆盖静态 import**（我实测）：`importlib.import_module("tools")` / `__import__("data.dao")` 与 `subprocess.run([..., "-m", "data.seed"])` 都**不被抓**。真实代码库用静态 import（现状），且 app.py 的 `subprocess python -m data.seed` 是有意保留的 bootstrap 例外；但若日后有人用动态导入/子进程绕层，守卫不拦。建议加一条动态调用探测 + subprocess 调用白名单（worker 待拍板③）。
  2. `forbidden_imports` 未含 `requests`/`urllib`/`http`（铁律 6 不联网）——目前界面确实不联网，但守卫没钉死。建议并入 FORBIDDEN_IMPORT_ROOTS。
  3. 逐字比对依赖回执**形状**：`RECEIPT` 是手写串，若 agent/templates 改了回执格式需同步（当前与真实回执一致，我 card-16 已交叉验证）。
  4. `test_yuan_is_pure_formatting` 直测**私有** `_yuan`（worker 待拍板⑤）：重命名即红，略脆，但它是「不改数字」守卫的核心支点，可接受。
  5. 守卫判据是「解析结果 == 字面值」，对「解析时把数字截断/丢小数位但恰好等值」这类仍需真回执形状覆盖；当前 RECEIPT 已含千分位/小数/负百分比，够用。
MUST_FIX: 无
EVIDENCE:
  - 抽验①（`_yuan` +1.0）→ test_web_layering **7 failed**（已还原）
  - 抽验②（components.py +`import tools`）→ never_imports 变红 `components.py: tools`（已还原）
  - 抽验③（app.py +`SQL="SELECT * FROM txn"`）→ never_writes_sql 变红 `app.py: 'SELECT * FROM'`（已还原）
  - `uv run python scripts/mutcheck_16c.py` → **5/5 个变异如期变红**（逐字节还原、哈希复核通过）
  - AST 探针：静态 import tools→caught；动态 importlib/`__import__`→**不 caught**；subprocess `-m data.seed`→**不 caught**
  - 16b 测试改动：test_dao_threads 12 连跑 **12/12 5 passed（零 flaky）**；DEFERRED 变异 → `takes_the_write_lock` 10/10 确定性 failed（已还原）
  - `uv run pytest --tb=no` → **926 passed**；`bash scripts/verify.sh` → `全部通过 ✅`；`git status` 无残留改动
VERDICT_REASON: 两条机器守卫真能被变异触发（改数字→7 红 / 加 import→红 / 写 SQL→红，另 mutcheck 5/5），AST 实现精确（非 grep、防空扫描自证、有元用例），16b 竞速用例确定性化方向正确且零 flaky，926 基线零回归，无 MUST_FIX；仅「AST 只覆盖静态 import（动态/子进程不拦）」「未含网络 import」2 条非阻塞 RISK。

注：审核期间检测到 **card-17（IM 通道）并发进行**（未跟踪 interfaces/im/{channel,config,feishu,server}.py），与 card-16c（已提交、926 绿）无关。另：16c 的守卫按 `interfaces/**` 全树扫描，card-17 的 IM 文件落地后会被同一条守卫覆盖，建议 17 交付时复跑本守卫。
