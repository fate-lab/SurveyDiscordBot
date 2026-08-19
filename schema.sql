-- THE LAB — схема базы данных D1 для автодоната
-- Применяется командой: wrangler d1 execute thelab-donate --file=./schema.sql --remote

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,                 -- наш собственный order_id (uuid), он же order_id в NOWPayments
  player_nick TEXT NOT NULL,           -- никнейм игрока в Minecraft
  product TEXT NOT NULL,               -- vip | vipplus | mvp | mvpplus | mvpplusplus
  months INTEGER NOT NULL,             -- 1 | 3 | 6 | 12
  amount_usd REAL NOT NULL,            -- итоговая цена в USD с учётом скидки
  np_payment_id TEXT,                  -- payment_id / invoice id от NOWPayments (заполняется по IPN)
  status TEXT NOT NULL DEFAULT 'pending', -- pending | paid | delivered | failed | expired
  created_at INTEGER NOT NULL,
  paid_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_np_payment_id ON orders(np_payment_id);

CREATE TABLE IF NOT EXISTS delivery_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id TEXT NOT NULL,
  command TEXT NOT NULL,               -- готовая консольная команда, например: lp user Notch parent addtemp vip 30d accumulate
  status TEXT NOT NULL DEFAULT 'pending', -- pending | done | error
  created_at INTEGER NOT NULL,
  done_at INTEGER,
  FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON delivery_queue(status);

-- Таблица под обработанные webhook id для идемпотентности (NOWPayments может слать один и тот же IPN несколько раз)
CREATE TABLE IF NOT EXISTS processed_ipn (
  np_payment_id TEXT PRIMARY KEY,
  payment_status TEXT NOT NULL,
  received_at INTEGER NOT NULL
);
