# F11 · 理财推荐

[全部功能 / 认领与进度](README.md) · [协作规则](../../CONTRIBUTING.md) · [四周排期](../04-功能排期.md)

**本轮负责人、审核人、计划周次、状态和 Issue/PR 见[功能总表](README.md)。** 本页保留技术范围与验收点。

## 要交付什么

用户示例：“推荐适合我的理财产品。”

现有基础（2026-09-22，`734ac09`）：已有读路由；测评衔接、风险/期限/金额筛选。

业务对应：`wealth_recommend` / `recommend_wealth`；单只产品的日 K 走 T14b `product_daily`。

## 实现口径（2026-10-28 已落地）

| 事项 | 定论 | 落在哪 |
|---|---|---|
| 产品目录 | **14 只**（R1×2 / R2×3 / R3×3 / R4×3 / R5×3） | `data/seed.PRODUCT_ROWS` |
| 推荐卡默认数量 | **4 张**（两行），其余折叠在「展开其余 N 只产品」后面 | `wealth_ui._PREVIEW_COUNT` |
| 走势图 | 卡片上**月 K 5 年**（60 根，可滚轮缩放/拖拽） | `tools/_wealth_risk._product_candles` |
| 日 K | **按需拉取**（近 120 个交易日）：点开哪只取哪只，约 5 KB | T14b `product_daily` + `agent/wealth_daily` |
| 数据性质 | **全部是模拟数据**：按 `sha256(产品 id + 周期)` 确定性生成，同一产品永远同一组 K 线 | 同上；界面明确标注「模拟」 |
| 买不起的产品 | 起购额 > 储蓄余额 → 订购按钮置灰并说明 | facts `affordable`（工具层判定） |

两条口径约束（都踩过）：

1. **K 线区间必须与卡片上的「参考下沿/上沿」同源** —— 各自推导会自相矛盾
   （实测出现过「上沿 +2.10%，历史却 +2.31%」）；
2. **月 K 要有漂移**（年化/12，封顶区间 60%）—— 只给噪声会让 14 只产品**全部**收在起点以下，
   与卡片上的预期年化直接打架。

## 已知限制

- 测评结果存在**进程内私有存储**（`_wealth_risk._ASSESSMENTS`），重启服务后需要重新测评；
- 日 K 只有近半年（120 个交易日），更早的日线需要扩窗口或接真实净值表；
- 产品历史是模拟序列，接真实数据需新增净值表（走规格审批）。

## 从哪里改

- 业务入口：[tools/_wealth_risk.py](../../tools/_wealth_risk.py)
- 编排入口：[状态机](../../agent/orchestrator.py)；读路由见 [read_routes.py](../../agent/read_routes.py)，转账写流程见 [write_flow.py](../../agent/write_flow.py)。后续泛化文件以 F04 合并结果为准。
- 现有回归：[test_tools_wealth_risk.py](../../tests/test_tools_wealth_risk.py)、[test_orchestrator_readonly.py](../../tests/test_orchestrator_readonly.py)
- 依赖：F10；当前传入 risk_level 也能推荐，需明确面向用户的测评来源。

这些是定位入口，不意味着要改全部文件。实现仍落在原有五层目录；共享文件先与本周集成人员约定，新增契约或依赖先按项目规则审批。

## 本轮验收清单

- [ ] 正常路径使用当前用户的有效测评结果并筛选期限、金额。
- [ ] 无匹配时明确告知，不放宽风险条件。
- [ ] 只推荐不申购，回执数字来自 facts。
- [ ] 演示正常、边界与失败路径，保留截图或可复现命令；用户可见数字来自工具事实包。
- [ ] 新增/调整行为有对应测试，相关原有回归通过；提供完整验收输出，由另一名组员审核。

现有测试起点（不代替新增场景与全量验收）：

```bash
uv run pytest tests/test_tools_wealth_risk.py tests/test_orchestrator_readonly.py
```

交付时在 PR 说明“改了什么 / 实际验证 / 剩余限制”，使用仓库 PR 模板。不能仅凭工具单测通过把对话流程标为已验收。
