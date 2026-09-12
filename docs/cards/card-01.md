<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 01`（无人值守）或在 Hermes 里直接说「做卡 01」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #01】实现数据层建库
范围：data/db.py、data/schema.sql、tests/test_db.py
要求：
1. schema.sql 逐字复制 docs/01-接口规格.md 第 1 节的 DDL，不要改动任何字段
2. db.py 提供 `connect(db_path)`、`init_db(db_path)`、`reset_db(db_path)`；用 stdlib sqlite3，金额是整数分
3. 打开外键约束；所有写入走事务
4. 单测：建库后每张表存在且字段数量与 DDL 一致；reset_db 后数据为空
禁止：引入 ORM（不用 SQLAlchemy）
交付：按模板。
