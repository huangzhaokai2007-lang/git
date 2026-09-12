<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 15`（无人值守）或在 Hermes 里直接说「做卡 15」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #15】红队测试集与可视化页面
范围：tests/redteam/attacks.yaml、scripts/redteam.py、interfaces/web/redteam_page.py
要求：
1. attacks.yaml：30 条攻击提示词，分 5 类（直接覆盖指令 / 角色扮演 / 数据外泄 / 越权操作 / 混淆编码），
   每条含 expect: blocked 或 must_not: [转账成功、余额显示]
2. scripts/redteam.py：逐条打入编排层，输出拦截率、分类统计、失败样例
3. Streamlit 红队页：一键跑全部攻击，显示拦截率与每条结果（绿色放行/红色拦截）
交付：按模板 + 贴出拦截率。这是一页给评委现场点的页面，做成最省事但要好看。
