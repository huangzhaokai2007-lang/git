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
  bank TEXT, is_whitelist INTEGER NOT NULL DEFAULT 0, last_used_ts TEXT
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
