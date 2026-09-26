# AI Banking Agent · 小组共创

用自然语言查询账户、管理卡片与订阅、办理模拟转账和理财业务。大模型负责理解和措辞，金额、权限与执行由代码决定。

> 全部为合成数据，只用于模拟演示，不连接真实银行。

**本轮目标：汤、黄、莫、舒各负责一个板块，在 2026-09-28～10-25 的四周内精细化验收 24 项功能。**

[认领功能 / 查看进度](docs/features/README.md) · [怎么参与](CONTRIBUTING.md) · [四周排期](docs/04-功能排期.md) · [周日总结](docs/weekly/README.md)

## 四人分工 · 24 项功能

每项功能有独立任务页；负责人和周次以[功能总表](docs/features/README.md)为准。每位成员负责所属板块的最终验收，板块内既有实现也要重新核验。

| 负责人 / 板块 | 数量 | 功能 |
|---|---:|---|
| **汤 · 卡片** | 6 | [F01 卡片查询](docs/features/F01-card-query.md) · [F05 申请卡](docs/features/F05-apply-card.md) · [F06 调整额度](docs/features/F06-card-limit.md) · [F07 锁卡](docs/features/F07-card-lock.md) · [F08 解锁](docs/features/F08-card-unlock.md) · [F09 挂失](docs/features/F09-card-lost.md) |
| **黄 · 转账、收款人与送礼** | 6 | [F15 AA 收款](docs/features/F15-aa-collect.md) · [F16 定时转账](docs/features/F16-scheduled-transfer.md) · [F17 收款人解析](docs/features/F17-resolve-payee.md) · [F20 送礼计划](docs/features/F20-gift-plan.md) · [F21 单笔转账](docs/features/F21-single-transfer.md) · [F22 新增收款人](docs/features/F22-add-payee.md) |
| **莫 · 理财与订阅** | 7 | [F04 订阅取消](docs/features/F04-cancel-subscription.md) · [F10 风险测评](docs/features/F10-risk-assessment.md) · [F11 理财推荐](docs/features/F11-wealth-recommend.md) · [F12 理财申购](docs/features/F12-wealth-buy.md) · [F13 理财赎回](docs/features/F13-wealth-redeem.md) · [F18 订阅提醒](docs/features/F18-subscription-reminder.md) · [F19 订阅列表](docs/features/F19-subscriptions.md) |
| **舒 · 账户与账单** | 5 | [F02 流水查询](docs/features/F02-transactions.md) · [F03 异常检测](docs/features/F03-anomalies.md) · [F14 账单分析](docs/features/F14-bill-analysis.md) · [F23 余额查询](docs/features/F23-balance.md) · [F24 账单报告](docs/features/F24-bill-report.md) |

四次周日收束：[10-04](docs/weekly/2026-10-04.md) · [10-11](docs/weekly/2026-10-11.md) · [10-18](docs/weekly/2026-10-18.md) · [10-25 最终验收](docs/weekly/2026-10-25.md)。逐周目标、前置依赖和缓冲安排见[四周排期](docs/04-功能排期.md)。

**当前代码边界（截至 2026-09-26）：**订阅取消尚需接通对话流程；预约转账工具仍拒绝预约参数；订阅提醒没有业务工具。GitHub 上暂无本轮 24 项的功能交付 PR，因此总表中的任务状态按待复核处理，不推断组员已经完成。

## 代码在哪里

| 目录 | 职责 | 分工时注意 |
|---|---|---|
| `interfaces/` | 网页 / API / IM 交互 | 业务调用走 `agent/` |
| `agent/` | 意图、补槽、编排、确认、回执 | 共享路由与状态机由本周集成人员协调 |
| `guard/` | 权限、注入防护、数字校验 | 多功能共用，修改需回归关联功能 |
| `tools/` | 银行业务工具 | 按功能页找到对应实现 |
| `data/` | SQLite、合成数据、DAO | 数据结构变更先同步规格 |
| `tests/` | 原有回归与验收用例 | 每个功能页列出测试入口 |

`app/cli.py` 是命令行入口；`agents/` 是旧 AI 角色说明，**不是**业务编排层 `agent/`。

## 启动与验证

Python 3.11 + uv。在仓库根目录执行；Windows 安装依赖和配置 D 盘缓存见[协作指南](CONTRIBUTING.md#首次运行)。

```bash
uv sync --frozen
uv run python -m data.seed --reset
uv run streamlit run interfaces/web/app.py
```

`--reset` 会重建本地演示数据，只在首次初始化或明确需要重置时使用。
需要自然语言识别时，将 `.env.example` 复制为 `.env` 并填入自己的模型配置；没有模型配置时可用离线演示：

```bash
uv run python scripts/demo.py --quick
uv run pytest -q
bash scripts/verify.sh
```

Windows 的 `bash` 请使用 Git Bash。离线演示与测试使用模型替身；不等同于真实模型联调。

## 进一步阅读

- [运行与评测](docs/03-运行与评测.md)：环境变量、API、演示与故障定位。
- [接口规格](docs/01-接口规格.md) / [项目约束](CLAUDE.md)：既有契约与架构边界。
- [历史资料索引](docs/archive/README.md)：旧任务卡、审核记录和整理前的长 README。
