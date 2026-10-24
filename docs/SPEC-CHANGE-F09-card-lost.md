# F09 挂失补卡增量契约

2026-10-24 用户已确认实施卡片级永久挂失与补卡申请，以及截图风格的详细说明。

- 不修改 `data/schema.sql`、公开 T12 签名与返回结构或核心卡状态枚举。
- 私有表 `agent_card_lost_request` 保存 token、user_id、session_id、card_id、status、expires_at、execute_at、verified_at、application_id、snapshot。状态为 preview/pending/success/cancelled/failed；补卡业务状态独立显示 not_submitted/pending_review，不表示已发卡。
- 部分唯一索引确保每卡最多一个 pending/success 申请；核心 card.status=lost 仍为不可解挂终态。
- 向导私有预览返回脱敏资料、演示费用、预计时间和模拟网点；submit 验证模拟 PIN 与 OTP，保存验证时间而不保存凭证。默认费用零元、预计十五天是显式演示配置，不是银行政策或服务承诺。
- 六十秒执行前撤销符合现有 L3；到期执行与成功审计在同一事务，复核标记不要求模拟不存在的审核端，也不代表已审核通过。
- 原卡仅限制卡片功能；账户余额、其他卡及独立账户转账不受挂失冻结影响。
- Streamlit 服务用标准库后台线程定期结算；无新增依赖。记录查询同样触发到期重检，服务停止期间不承诺准时执行。
- 仅用户拥有且绑定的正常或临时锁定借记卡支持线上办理；不改变底层 T12 对 frozen 的历史契约，但 UI 私有流程不接受冻结卡。
