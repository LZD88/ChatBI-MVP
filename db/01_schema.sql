CREATE DATABASE IF NOT EXISTS chatbi_mvp
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE chatbi_mvp;

CREATE TABLE IF NOT EXISTS dim_customers (
  customer_id INT PRIMARY KEY,
  customer_name VARCHAR(100) NOT NULL,
  customer_type VARCHAR(50) NOT NULL,
  industry VARCHAR(50) NOT NULL,
  country VARCHAR(50) NOT NULL,
  region VARCHAR(50) NOT NULL,
  INDEX idx_customers_type (customer_type),
  INDEX idx_customers_region (region)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_products (
  product_id INT PRIMARY KEY,
  product_name VARCHAR(100) NOT NULL,
  product_line VARCHAR(50) NOT NULL,
  category VARCHAR(50) NOT NULL,
  tech_route VARCHAR(50) NOT NULL,
  standard_cost DECIMAL(10,2) NOT NULL,
  material_cost DECIMAL(10,2) NOT NULL,
  labor_cost DECIMAL(10,2) NOT NULL,
  INDEX idx_products_line (product_line),
  INDEX idx_products_route (tech_route)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS exchange_rates (
  rate_date DATE NOT NULL,
  currency VARCHAR(10) NOT NULL,
  rate_to_cny DECIMAL(10,4) NOT NULL,
  PRIMARY KEY (rate_date, currency)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sales_orders (
  order_id BIGINT PRIMARY KEY,
  order_no VARCHAR(50) NOT NULL UNIQUE,
  customer_id INT NOT NULL,
  product_id INT NOT NULL,
  region VARCHAR(50) NOT NULL,
  order_date DATE NOT NULL,
  order_status VARCHAR(20) NOT NULL,
  quantity DECIMAL(10,2) NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL,
  discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
  gross_amount DECIMAL(12,2) NOT NULL,
  net_amount DECIMAL(12,2) NOT NULL,
  currency VARCHAR(10) NOT NULL,
  CONSTRAINT chk_order_status CHECK (order_status IN ('completed', 'cancelled', 'pending')),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES dim_customers(customer_id),
  CONSTRAINT fk_orders_product FOREIGN KEY (product_id) REFERENCES dim_products(product_id),
  CONSTRAINT fk_orders_rate FOREIGN KEY (order_date, currency)
    REFERENCES exchange_rates(rate_date, currency),
  INDEX idx_orders_date_status (order_date, order_status),
  INDEX idx_orders_region (region),
  INDEX idx_orders_customer (customer_id),
  INDEX idx_orders_product (product_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS finance_expenses (
  expense_id BIGINT PRIMARY KEY,
  expense_date DATE NOT NULL,
  department VARCHAR(50) NOT NULL,
  rd_expense DECIMAL(12,2) NOT NULL DEFAULT 0,
  selling_expense DECIMAL(12,2) NOT NULL DEFAULT 0,
  admin_expense DECIMAL(12,2) NOT NULL DEFAULT 0,
  finance_expense DECIMAL(12,2) NOT NULL DEFAULT 0,
  marketing_expense DECIMAL(12,2) NOT NULL DEFAULT 0,
  logistics_expense DECIMAL(12,2) NOT NULL DEFAULT 0,
  warranty_expense DECIMAL(12,2) NOT NULL DEFAULT 0,
  INDEX idx_expenses_date (expense_date),
  INDEX idx_expenses_department (department)
) ENGINE=InnoDB;
