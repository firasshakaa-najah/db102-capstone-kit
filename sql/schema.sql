-- DB-102 capstone: multi-tenant e-commerce platform (Shopify-like)
-- Target: MySQL 8.4 LTS, InnoDB, utf8mb4
--
-- TENANCY DECISION (instructor reference): ONE shared schema, every tenant-scoped
-- row carries tenant_id. A tenant is a merchant account; a tenant owns one or more
-- stores. tenant_id is denormalized onto orders / order_items / payments / events
-- even though it is derivable through store_id, because every query, every index
-- and (in week 10) every shard key starts with "which tenant?".
--
-- DELIBERATE OMISSIONS (week 3 needs slow queries to exist; week 4 fixes them):
--   * no index on orders(order_number), orders(status), orders(created_at)
--   * no index on events at all beyond the primary key (append-only log, no FKs)
--   * no index on customers(last_name), payments(status)
-- InnoDB still creates one index per FOREIGN KEY it enforces (it has to). Run
-- SHOW INDEX FROM orders after loading and you will find indexes you never wrote.

CREATE DATABASE IF NOT EXISTS shopdb
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;
USE shopdb;

-- Re-runnable: drop children first
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS stores;
DROP TABLE IF EXISTS tenants;

CREATE TABLE tenants (
  id            INT UNSIGNED     NOT NULL AUTO_INCREMENT,
  name          VARCHAR(120)     NOT NULL,
  plan          ENUM('basic','pro','enterprise') NOT NULL DEFAULT 'basic',
  country_code  CHAR(2)          NOT NULL,
  created_at    DATETIME         NOT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB;

CREATE TABLE stores (
  id            INT UNSIGNED     NOT NULL AUTO_INCREMENT,
  tenant_id     INT UNSIGNED     NOT NULL,
  name          VARCHAR(120)     NOT NULL,
  slug          VARCHAR(120)     NOT NULL,
  currency      CHAR(3)          NOT NULL DEFAULT 'USD',
  created_at    DATETIME         NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_stores_slug (slug),
  CONSTRAINT fk_stores_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
) ENGINE=InnoDB;

CREATE TABLE products (
  id            BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  tenant_id     INT UNSIGNED     NOT NULL,
  store_id      INT UNSIGNED     NOT NULL,
  sku           VARCHAR(40)      NOT NULL,
  title         VARCHAR(200)     NOT NULL,
  category      VARCHAR(60)      NOT NULL,
  price         DECIMAL(10,2)    NOT NULL,
  stock_qty     INT              NOT NULL DEFAULT 0,
  is_active     TINYINT(1)       NOT NULL DEFAULT 1,
  created_at    DATETIME         NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_products_store_sku (store_id, sku),
  CONSTRAINT fk_products_store FOREIGN KEY (store_id) REFERENCES stores (id)
) ENGINE=InnoDB;

CREATE TABLE customers (
  id            BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  tenant_id     INT UNSIGNED     NOT NULL,
  store_id      INT UNSIGNED     NOT NULL,
  email         VARCHAR(190)     NOT NULL,
  first_name    VARCHAR(60)      NOT NULL,
  last_name     VARCHAR(60)      NOT NULL,
  phone         VARCHAR(30)      NULL,
  city          VARCHAR(80)      NULL,
  country_code  CHAR(2)          NOT NULL,
  created_at    DATETIME         NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_customers_store_email (store_id, email),
  CONSTRAINT fk_customers_store FOREIGN KEY (store_id) REFERENCES stores (id)
) ENGINE=InnoDB;

CREATE TABLE orders (
  id               BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  tenant_id        INT UNSIGNED     NOT NULL,
  store_id         INT UNSIGNED     NOT NULL,
  customer_id      BIGINT UNSIGNED  NOT NULL,
  order_number     VARCHAR(24)      NOT NULL,          -- 'S042-00001234'; unique by construction, NOT indexed (yet)
  status           ENUM('pending','paid','fulfilled','shipped','delivered','cancelled','refunded') NOT NULL,
  channel          ENUM('web','mobile','pos','api')    NOT NULL,
  currency         CHAR(3)          NOT NULL,
  subtotal         DECIMAL(12,2)    NOT NULL,
  shipping_fee     DECIMAL(10,2)    NOT NULL DEFAULT 0,
  discount         DECIMAL(10,2)    NOT NULL DEFAULT 0,
  total            DECIMAL(12,2)    NOT NULL,
  item_count       SMALLINT UNSIGNED NOT NULL,
  shipping_city    VARCHAR(80)      NULL,
  shipping_country CHAR(2)          NOT NULL,
  created_at       DATETIME         NOT NULL,
  updated_at       DATETIME         NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT fk_orders_store    FOREIGN KEY (store_id)    REFERENCES stores (id),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (id)
) ENGINE=InnoDB;

CREATE TABLE order_items (
  id            BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  tenant_id     INT UNSIGNED     NOT NULL,
  order_id      BIGINT UNSIGNED  NOT NULL,
  product_id    BIGINT UNSIGNED  NOT NULL,
  quantity      SMALLINT UNSIGNED NOT NULL,
  unit_price    DECIMAL(10,2)    NOT NULL,
  line_total    DECIMAL(12,2)    NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT fk_items_order   FOREIGN KEY (order_id)   REFERENCES orders (id),
  CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES products (id)
) ENGINE=InnoDB;

CREATE TABLE payments (
  id            BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  tenant_id     INT UNSIGNED     NOT NULL,
  order_id      BIGINT UNSIGNED  NOT NULL,
  provider      ENUM('card','paypal','cod','bank_transfer','wallet') NOT NULL,
  status        ENUM('authorized','captured','failed','refunded')   NOT NULL,
  amount        DECIMAL(12,2)    NOT NULL,
  currency      CHAR(3)          NOT NULL,
  provider_ref  VARCHAR(64)      NULL,
  created_at    DATETIME         NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders (id)
) ENGINE=InnoDB;

-- Append-only clickstream. No foreign keys and no secondary indexes: on purpose.
-- (In production this table is usually the first thing to leave the OLTP database;
--  in week 11 it moves to Elasticsearch, in week 14 to the warehouse.)
CREATE TABLE events (
  id            BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  tenant_id     INT UNSIGNED     NOT NULL,
  store_id      INT UNSIGNED     NOT NULL,
  customer_id   BIGINT UNSIGNED  NULL,
  session_id    CHAR(32)         NOT NULL,
  event_type    ENUM('page_view','product_view','search','add_to_cart','remove_from_cart','checkout_started','order_placed') NOT NULL,
  product_id    BIGINT UNSIGNED  NULL,
  order_id      BIGINT UNSIGNED  NULL,
  device        ENUM('desktop','mobile','tablet','app') NOT NULL,
  country_code  CHAR(2)          NOT NULL,
  properties    JSON             NULL,
  occurred_at   DATETIME(3)      NOT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB;
