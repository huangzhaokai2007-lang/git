<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 00`（无人值守）或在 Hermes 里直接说「做卡 00」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #00】初始化可运行的项目骨架

第一步：读 CLAUDE.md 和 docs/01-接口规格.md，用 5 行复述你理解的架构约束。
范围：仓库根目录、pyproject.toml、scripts/、interfaces/、agent/、guard/、tools/、data/、tests/
要求：
1. 按 CLAUDE.md 的目录结构建好空包（每个包一个 __init__.py）
2. pyproject.toml 用 uv 管理，依赖按 CLAUDE.md 的技术栈；Python 版本 3.11
3. 写 scripts/verify.sh：依次跑 `uv run pytest -q`、冒烟对话、安全用例，任一失败即退出码非 0
4. 写 .gitignore（含 .env、*.db、__pycache__）
5. 写 .env.example：LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / DB_PATH
6. 现在还没有业务代码，verify.sh 允许"未实现"跳过并打印 SKIP
禁止：写任何业务逻辑
交付：按模板。
