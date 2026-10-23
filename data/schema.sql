CREATE TABLE user (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL,
  kyc_level TEXT NOT NULL DEFAULT 'L1'            -- L1/L2/L3 实名等级
);
CREATE TABLE account (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  type TEXT NOT NULL,                             -- savings | credit
  balance INTEGER NOT NULL,                       -- 单位：分（禁止浮点）
  available INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE txn (                                 -- 交易流水（"transaction" 是 SQL 保留字）
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, ts TEXT NOT NULL,   -- ISO8601
  amount INTEGER NOT NULL,                        -- 正=入账 负=出账，单位分
  direction TEXT NOT NULL,                        -- in | out
  counterparty TEXT,                              -- 对手方名称
  payee_id TEXT,                                  -- v2：收款人主键（新转账才回填；种子流水为 NULL）
                                                  -- 有它才能按「收款人」而不是按「名字」算白名单额度
                                                  -- —— 同名的两个李四否则会合并计算
  category TEXT,                                  -- 餐饮/交通/电商/订阅/转账/工资...
  channel TEXT,                                   -- 卡/二维码/转账/代扣
  memo TEXT,                                      -- 用户备注（★不可信文本）
  balance_after INTEGER NOT NULL
);
CREATE TABLE card (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, account_id TEXT NOT NULL,
  card_no_mask TEXT NOT NULL,                     -- 6222 **** **** 0001
  type TEXT NOT NULL, credit_limit INTEGER, single_limit INTEGER, daily_limit INTEGER,
  status TEXT NOT NULL DEFAULT 'normal'           -- normal | locked | lost | frozen
);
CREATE TABLE subscription (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, merchant TEXT NOT NULL,
  amount INTEGER NOT NULL, cycle TEXT NOT NULL,   -- monthly | yearly
  next_charge_date TEXT NOT NULL, source_txn_id TEXT,
  status TEXT NOT NULL DEFAULT 'active'           -- active | cancelled | paused
);
CREATE TABLE wealth_product (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, risk_level TEXT NOT NULL, -- R1..R5
  term_days INTEGER NOT NULL, expected_yield REAL NOT NULL, min_amount INTEGER NOT NULL
);
CREATE TABLE holding (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, product_id TEXT NOT NULL,
  amount INTEGER NOT NULL, purchase_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'held'
);
CREATE TABLE payee (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, phone TEXT,
  bank TEXT, is_whitelist INTEGER NOT NULL DEFAULT 0, last_used_ts TEXT,
  -- v2（转账域串联 · SPEC-CHANGE）：标签 / 白名单专属额度 / 黑名单
  tags TEXT,                                      -- 逗号分隔，可空（如 "妈妈,家人"）
  whitelist_quota INTEGER,                        -- 白名单免验证码额度，单位分；NULL=无额度
  whitelist_expire TEXT,                          -- 白名单有效期（ISO 结束日期）；到期自动降级为普通收款人；NULL=长期
  note TEXT,                                      -- 备注（用户自己写的说明，可空；不是风控字段）
  card_no TEXT,                                  -- 银行卡号**脱敏形态**（如 **** **** **** 8888）；完整号绝不入库
  blacklist INTEGER NOT NULL DEFAULT 0,           -- 1=黑名单；与 is_whitelist 互斥
  blacklist_reason TEXT                           -- 拉黑原因，可空
);
CREATE TABLE audit_log (
  id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, session_id TEXT NOT NULL,
  ts TEXT NOT NULL, actor TEXT NOT NULL,          -- user | agent | system
  intent TEXT, tool TEXT, params_json TEXT,       -- 已脱敏
  risk_level TEXT, permission_tier TEXT,          -- L0..L3
  result TEXT,                                    -- success | rejected | pending_confirm | error
  error_code TEXT
);
CREATE TABLE risk_event (
  id TEXT PRIMARY KEY, trace_id TEXT, ts TEXT, user_id TEXT,
  factor TEXT,                                    -- night | geo | device | velocity | amount_jump | new_payee | unauthorized_resource
  detail TEXT, action_taken TEXT                  -- downgrade | block | to_human
);
CREATE TABLE idempotency (                        -- 卡 14b：幂等快照落库（token 主键，重启后重放仍返回同结果）
  token TEXT PRIMARY KEY, tool TEXT, user_id TEXT NOT NULL,
  result_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE rate_limit (                         -- 卡 14b：写操作限流（滚动 60 秒窗口，按行计数；只读不写此表）
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, tool TEXT, ts TEXT NOT NULL
);
CREATE TABLE scheduled_transfer (                 -- F16：定时转账预约（Q1–Q10 人类已批；惰性结算）
  id TEXT PRIMARY KEY,                            -- sch_<uuid12>
  user_id TEXT NOT NULL, payee_id TEXT NOT NULL,
  amount INTEGER NOT NULL,                        -- 单位：分（禁止浮点）
  execute_at TEXT NOT NULL,                       -- ISO8601，**本地时 naive**（Q2：不引入 tz-aware）
  status TEXT NOT NULL,                           -- pending | success | failed | cancelled
  tier TEXT NOT NULL,                             -- **预约时刻冻结**（Q5：到期只重检硬约束，不重新定档）
  token TEXT,                                     -- 生成它的 preview_token（追溯用）
  created_at TEXT NOT NULL,
  settled_at TEXT,                                -- 结算时刻；pending 时为 NULL
  txn_id TEXT,                                    -- 成功后回填
  fail_reason TEXT,                               -- 失败原因码（INSUFFICIENT_FUNDS 等）
  repeat_cycle TEXT,                              -- 循环预约：weekly | monthly；NULL = 一次性（T34 设/T35 续期）
  repeat_until TEXT                               -- 循环结束日（ISO 日期）；NULL = 一直循环（2026-10-24 人类规格「周期结束条件」）
);
CREATE TABLE gift_order (                         -- F20 追加：送礼订单（T16 锁款成功后由 T38 落一条）
  id TEXT PRIMARY KEY,                            -- ord_<uuid12>
  user_id TEXT NOT NULL,
  contact TEXT NOT NULL,                          -- 收货人
  gift_date TEXT NOT NULL,                        -- 送达日期（ISO 日期）
  budget INTEGER NOT NULL,                        -- 预算，单位分
  total INTEGER NOT NULL,                         -- 实际合计，单位分
  items_json TEXT NOT NULL,                       -- 清单快照 [{item_id,name,price}]
  status TEXT NOT NULL,                           -- created | paid | cancelled | failed
  plan_id TEXT, lock_id TEXT,                     -- T16 的 plan/lock（追溯用）
  created_at TEXT NOT NULL
);
CREATE TABLE aa_request (                         -- F22 追加：AA 收款请求（T41 落 / T42 查 / T43 已付 / T44 撤销）
  id TEXT PRIMARY KEY,                            -- aa_<uuid12>
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,                            -- 事由（如「周末聚餐」）
  total INTEGER NOT NULL,                         -- 合计，单位分
  split_mode TEXT NOT NULL,                       -- equal | custom
  status TEXT NOT NULL,                           -- active | done | expired | cancelled
  created_at TEXT NOT NULL,
  expire_at TEXT NOT NULL                         -- 创建时刻 + 24h（ISO；到点惰性判 expired）
);
CREATE TABLE aa_share (                           -- AA 里每个收款人一份（每人已付状态）
  id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  payee_id TEXT NOT NULL,
  payee_name TEXT NOT NULL,                       -- 快照（收款人改名/删除也能查）
  amount INTEGER NOT NULL,                        -- 该人应收，单位分
  status TEXT NOT NULL,                           -- pending | paid
  paid_at TEXT
);
