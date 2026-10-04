# SPEC CHANGE F01 模拟银行卡绑定

用户已确认模拟绑卡流程并授权实现。新增接口与数据表仅用于此模拟流程；原 tools.schemas、T18 list_cards、classifier.IntentOut 和既有 card/account 表不改变。

新增工具在 tools/card_binding.py 中使用独立入参模型及既有 ToolResult：

| 接口 | 成功 data | 权限 |
| --- | --- | --- |
| preview_binding(card_no, password, session_id) | token、card_id、card_no_mask、already_bound | L1 校验，不绑定 |
| confirm_binding(token, session_id) | card_id、card_no_mask、already_bound | L1，明确确认后绑定 |
| list_bound_cards(status=None) | items、total_count；items 包含原卡片字段及 balance_yuan | L0 |
| card_detail(card_id) | 卡片字段及 balance_yuan | L0，仅已绑定卡 |
| reveal_card_number(card_id, password, session_id) | card_no | 逐卡密码重新验证，仅临时显示 |

完整卡号仅受保护工具 data 返回；普通 facts 永不包含完整卡号和密码。审计仅记录 card_id、动作与结果。余额展示串来自工具对绑定账户整数分的格式化。

agent_card_secret 存储 card_id、完整合成卡号、随机盐与密码哈希。agent_card_binding 存储 user_id、card_id、bound_at，组合主键去重。agent_card_request 存储一次性确认令牌、用户、会话、卡片、到期时间及已确认状态。agent_card_attempt 存储用户/卡号哈希键、失败次数与锁定截止时间。

预览和确认均重新检查卡片与账户归属。绑卡不改卡状态、额度或账户余额；允许绑定挂失等状态卡用于展示，但不解锁。密码为六位模拟数字密码；五次错误暂停五分钟；确认令牌五分钟到期。用户切换后旧令牌不能确认，旧卡号展示必须清除。已有数据库只补建表和缺失合成凭证，不重置、不自动绑定。

Web 入口为编排层注入可选意图解析器：完整匹配“卡片查询”等明确清单指令时返回 card_query，其他表达仍调用原分类器。解析位于原注入检测和在途写操作处理之后；默认调用方行为不变。卡片工具执行、数字校验、回执和单次审计仍走原状态机，无模型密钥时也能使用明确清单指令。
