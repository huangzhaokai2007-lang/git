# AI Banking Agent · 小组共创

用自然语言查询账户、管理卡片与订阅、办理模拟转账和理财业务。大模型负责理解和措辞，金额、权限与执行由代码决定。

> 全部为合成数据，只用于模拟演示，不连接真实银行。

**本轮目标：保留原架构，精细化重做 24 项功能，2026 年 10 月 25 日收束。**

[认领功能 / 查看进度](docs/features/README.md) · [怎么参与](CONTRIBUTING.md) · [五周排期](docs/04-功能排期.md) · [周日总结](docs/weekly/README.md)

## 24 个功能，从这里进入

点击功能查看代码位置、已有基础、待做内容、依赖和验收点。**功能编号 F01–F24 与历史施工卡 card-* 分开。**

| 周次 / 周日收束 | 功能 |
|---|---|
| **W1 · 09-27 · 4 项** | [F01 卡片查询](docs/features/F01-card-query.md) · [F02 流水查询](docs/features/F02-transactions.md) · [F03 异常检测](docs/features/F03-anomalies.md) · [F04 订阅取消](docs/features/F04-cancel-subscription.md) |
| **W2 · 10-04 · 5 项** | [F05 申请卡](docs/features/F05-apply-card.md) · [F06 调整额度](docs/features/F06-card-limit.md) · [F07 锁卡](docs/features/F07-card-lock.md) · [F08 解锁](docs/features/F08-card-unlock.md) · [F09 挂失](docs/features/F09-card-lost.md) |
| **W3 · 10-11 · 5 项** | [F10 风险测评](docs/features/F10-risk-assessment.md) · [F11 理财推荐](docs/features/F11-wealth-recommend.md) · [F12 理财申购](docs/features/F12-wealth-buy.md) · [F13 理财赎回](docs/features/F13-wealth-redeem.md) · [F14 账单分析](docs/features/F14-bill-analysis.md) |
| **W4 · 10-18 · 5 项** | [F15 AA 收款](docs/features/F15-aa-collect.md) · [F16 定时转账](docs/features/F16-scheduled-transfer.md) · [F17 收款人解析](docs/features/F17-resolve-payee.md) · [F18 订阅提醒](docs/features/F18-subscription-reminder.md) · [F19 订阅列表](docs/features/F19-subscriptions.md) |
| **W5 · 10-25 · 5 项** | [F20 送礼计划](docs/features/F20-gift-plan.md) · [F21 单笔转账](docs/features/F21-single-transfer.md) · [F22 新增收款人](docs/features/F22-add-payee.md) · [F23 余额查询](docs/features/F23-balance.md) · [F24 账单报告](docs/features/F24-bill-report.md) |

**先看实际边界：**订阅取消与多数卡片/理财写操作还需接通对话流程；定时转账已有意图入口，但工具仍拒绝预约参数；订阅提醒尚无实现。已有代码也需本轮重新验收。[查看逐项现状](docs/features/README.md#当前代码基础)

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
