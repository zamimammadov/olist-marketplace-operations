CREATE TABLE IF NOT EXISTS batches (
    month TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    loaded_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS batch_files (
    month TEXT NOT NULL REFERENCES batches(month),
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    PRIMARY KEY (month, filename)
);

CREATE TABLE IF NOT EXISTS customers (
    month TEXT NOT NULL REFERENCES batches(month),
    customer_id TEXT NOT NULL,
    customer_unique_id TEXT NOT NULL,
    zip_code_prefix TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (month, customer_id)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY NOT NULL,
    month TEXT NOT NULL REFERENCES batches(month),
    customer_id TEXT NOT NULL,
    order_status TEXT NOT NULL,
    purchased_at TEXT NOT NULL,
    approved_at TEXT,
    delivered_to_carrier_at TEXT,
    delivered_to_customer_at TEXT,
    estimated_delivery_at TEXT,
    FOREIGN KEY (month, customer_id) REFERENCES customers(month, customer_id),
    UNIQUE (month, order_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_month_customer ON orders(month, customer_id);

CREATE TABLE IF NOT EXISTS order_items (
    month TEXT NOT NULL,
    order_id TEXT NOT NULL,
    order_item_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    seller_id TEXT NOT NULL,
    shipping_limit_at TEXT,
    item_value_cents INTEGER NOT NULL,
    freight_cents INTEGER NOT NULL,
    PRIMARY KEY (order_id, order_item_id),
    FOREIGN KEY (month, order_id) REFERENCES orders(month, order_id)
);

CREATE TABLE IF NOT EXISTS order_payments (
    month TEXT NOT NULL,
    order_id TEXT NOT NULL,
    payment_sequential INTEGER NOT NULL,
    payment_type TEXT NOT NULL,
    installments INTEGER NOT NULL,
    payment_value_cents INTEGER NOT NULL,
    PRIMARY KEY (order_id, payment_sequential),
    FOREIGN KEY (month, order_id) REFERENCES orders(month, order_id)
);

CREATE TABLE IF NOT EXISTS load_failures (
    attempt_id INTEGER PRIMARY KEY,
    attempted_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    month TEXT NOT NULL,
    attempted_sha256 TEXT,
    reason TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS order_snapshot AS
WITH items AS (
    SELECT order_id, count(*) AS item_count,
           sum(item_value_cents) AS item_value_cents, sum(freight_cents) AS freight_cents
    FROM order_items
    GROUP BY order_id
), payments AS (
    SELECT order_id, count(*) AS payment_count, sum(payment_value_cents) AS payment_value_cents
    FROM order_payments
    GROUP BY order_id
)
SELECT o.order_id,
       substr(o.purchased_at, 1, 10) AS purchase_date,
       c.state AS customer_state,
       o.order_status,
       substr(o.delivered_to_customer_at, 1, 10) AS delivered_date,
       substr(o.estimated_delivery_at, 1, 10) AS estimated_date,
       coalesce(i.item_count, 0) AS item_count,
       coalesce(p.payment_count, 0) AS payment_count,
       coalesce(i.item_value_cents, 0) AS item_value_cents,
       coalesce(i.freight_cents, 0) AS freight_cents,
       coalesce(p.payment_value_cents, 0) AS payment_value_cents
FROM orders AS o
JOIN customers AS c ON c.month = o.month AND c.customer_id = o.customer_id
LEFT JOIN items AS i ON i.order_id = o.order_id
LEFT JOIN payments AS p ON p.order_id = o.order_id;
