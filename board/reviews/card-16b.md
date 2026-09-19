VERDICT: PASS
CARDS: card-16b
REVIEWED_DIFF: 已提交 bdbe818，本审为提交后复核。范围：data/db.py（BEGIN IMMEDIATE + BUSY_TIMEOUT_SECONDS=5 + init_db 只补缺表 + schema_drift/SchemaDriftError）、data/_dao_core.py（连接改 threading.local 每线程一份）、data/dao.py、agent/classifier.py（SLOT_VALUE_ALIASES 归一化 + build_messages role-separated）、agent/llm.py（范围外第 4 文件：messages_of 支持 str|list[Message]）、tests/test_dao_threads.py（新）、tests/test_classifier_history.py（新）、scripts/mutcheck_16b.py（新）。独立基线 895=864+31（排除 16c 未跟踪测试后实测 895 passed）。
CHECKED:
  - 接口一致性：通过。`chat_json(system, user, schema)` 的 user 扩为 `str | list[Message]`，`messages_of` 对 str 逐字返回单条 user 消息（**与旧行为一致**）、对 list 展开 role-separated —— 向后兼容，调用方零改动。`transaction()` 公开签名不变（仍 `(conn)`）。
  - 越界改动：agent/llm.py 是清单外第 4 文件（role-separated 必须能传消息数组），改法向后兼容、未改 business 语义，可接受（analyst 已接受并已告知）。tests/test_classifier.py 改 1 条旧断言——它钉的正是「history 串味」行为，卡第 3 条要求改，属正向修正。
  - 测试真实性（变异抽查 5/5 真报警，均已还原）：① `_dao_core` 把 `threading.local()` 换成跨线程共享类 → test_each_thread_gets_its_own_connection + parallel_writes + deadlock 3 失败 2 错误，报**同一条** `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`（正是本卡要修的坑）；② `BEGIN IMMEDIATE`→`BEGIN DEFERRED` → deadlock 用例 10/10 失败（sleep 定序，确定性复现升级死锁）；③ `normalize_slot_values` 改 no-op → account_type 别名用例全红；④ `build_messages` 改回「历史+当前话拼一条」→ separate_user_messages + history_marker 两条 FAIL；⑤ `init_db` 改回「无条件执行全量 DDL」→ repairs_a_partially_stale_database 报 `table user already exists` FAIL。
  - 边界与异常（红线 1 线程安全**真并发**独立验证）：`tests/test_dao_threads.py` 5 条连跑 7 次全绿；其中「并发写 + 死锁」两条再连跑 15 次 **15/15 通过**（0 flaky）。用例本身过硬：`threading.Barrier` 制造同时冲线、每线程各写 5 笔后断言「余额逐笔对得上 + 流水条数 + 每线程只见自己 5 笔 + COUNT(DISTINCT id) 不重复」（真丢账/串账会被抓）；死锁用例用固定 sleep 时序（不靠 CPU 竞速）。余额/条数断言均读库独立复算。
  - 权限/审计/安全（红线 2/4）：init_db **只补缺失的表**（`missing = [name for name in TABLES if not _table_exists]`，只执行缺失表的 CREATE），建完 `schema_drift()` 自检缺列 → `SchemaDriftError`（消息带 `data.seed --reset` 重建命令），**不自动迁移**（合理：缺列无法安全补建）；`reference_columns()` 用内存库真跑一遍 DDL 取真值（不解析 SQL 文本，防解析漂移）；`tables`/`reset_db` 表名取自 sqlite_master、表清单 12 张同步规格。
RISKS:
  1. 线程局部连接**无显式 close**：线程结束时连接靠 threading.local 被回收 + GC 关闭，长跑多线程（FastAPI 大量请求线程）在 GC 前可能短暂累积未关闭连接。demo/评测够用，生产建议线程池回收钩子或 `check_same_thread` 口径复核。
  2. `reference_columns()` 带 `@lru_cache(maxsize=1)`：同进程内 schema.sql 改了不重启不生效（dev 期改 schema 要清缓存）；schema 是常量，运行期无碍。
  3. `BUSY_TIMEOUT_SECONDS=5.0` + `BEGIN IMMEDIATE`：短事务下并发写被串行化，但**长事务/高并发**仍可能 SQLITE_BUSY（5s 等不到）。demo 足够；若上真并发生产需调大或改 WAL。
  4. role-separated 修好了**底层**，但接口仍不传 history（app.py `HISTORY=None`）：跨轮指代（「那再查上上个月」）没有上下文，靠确认/OTP 在途流程承载。属接口层有意取舍，记此备查（若现场演示要「连续追问」，需要 agent/ 层开 history 的受控用法）。
  5. `init_db` 缺列只报错不迁移 → 旧库需用户手动 `--reset`；错误消息已给命令（可接受）。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no --ignore=tests/test_web_layering.py` → **895 passed**（card-16b 独立基线，=864+31）
  - `bash scripts/verify.sh` → `全部通过 ✅`
  - 线程安全：`tests/test_dao_threads.py` 连跑 7 次 → 5 passed×7；`-k "parallel_writes or deadlock"` 连跑 15 次 → **15/15 passed**（无 flaky）
  - 变异①（共享连接）→ 3 failed 2 errors（ProgrammingError）；②（DEFERRED）→ deadlock 10/10 failed；③（不归一化）→ 别名用例红；④（history 拼一条）→ 2 failed；⑤（无条件 DDL）→ `table user already exists` failed（均已还原）
  - `grep -rn 变异 agent/ data/` → 无残留
VERDICT_REASON: 四条红线全过——data 线程安全（thread-local 连接 + BEGIN IMMEDIATE + 5s busy_timeout，真并发 15/15 稳、5 个变异真报警）、结构漂移只补表缺列报错不迁移、槽位归一化、history role-separated，895 基线零回归，无 MUST_FIX；仅连接回收/缓存/超时/界面不传 history 等 5 条非阻塞 RISK。

注：审核期间检测到 **16c 并发进行**（未跟踪 tests/test_web_layering.py + scripts/mutcheck_16c.py，含入后 pytest 926 passed = 895 + 16c 31 条）——正是我 card-16 建议的「界面分层守卫」落地。与 card-16b（已提交、895 绿）无关，请 analyst 知悉并安排 16c 收口后复核。
