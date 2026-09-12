<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 18`（无人值守）或在 Hermes 里直接说「做卡 18」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #18】工程化与提交物
范围：README.md、Dockerfile、docker-compose.yml、scripts/bootstrap.sh、docs/
要求：
1. README：一段话定位、架构图（Mermaid）、15 个工具表格、权限矩阵表、注入攻防对照表、
   一条命令启动（`uv sync && uv run streamlit run interfaces/web/app.py` 或 docker compose up）、
   评测入口说明、合规声明（模拟环境/合成数据/不构成投资建议）
2. 评测入口：`POST /api/chat {"text": "..."}` → 返回 {reply, intent, tool_calls, tier, executed, trace_id}
3. Dockerfile 用 python:3.11-slim，离线可用（不联网也能启动，LLM 不可用时自动降级）
4. bootstrap.sh 在全新机器上一条命令跑通
交付：按模板，并贴出在干净目录里跑通的输出。
