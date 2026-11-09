USE chatbi_mvp;

CREATE TABLE IF NOT EXISTS dim_regions (
  region_id INT PRIMARY KEY,
  region_name VARCHAR(50) NOT NULL UNIQUE,
  region_manager VARCHAR(100) NOT NULL,
  INDEX idx_regions_manager (region_manager)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_countries (
  country_id INT PRIMARY KEY,
  country_name VARCHAR(50) NOT NULL UNIQUE,
  region_id INT NOT NULL,
  iso_code CHAR(2) NOT NULL UNIQUE,
  CONSTRAINT fk_countries_region FOREIGN KEY (region_id) REFERENCES dim_regions(region_id),
  INDEX idx_countries_region (region_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_departments (
  department_id INT PRIMARY KEY,
  department_name VARCHAR(50) NOT NULL UNIQUE,
  department_type VARCHAR(30) NOT NULL,
  parent_department_id INT NULL,
  CONSTRAINT fk_departments_parent FOREIGN KEY (parent_department_id) REFERENCES dim_departments(department_id),
  INDEX idx_departments_type (department_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_employees (
  employee_id INT PRIMARY KEY,
  employee_name VARCHAR(100) NOT NULL,
  department_id INT NOT NULL,
  job_title VARCHAR(50) NOT NULL,
  hire_date DATE NOT NULL,
  employee_status VARCHAR(20) NOT NULL,
  CONSTRAINT fk_employees_department FOREIGN KEY (department_id) REFERENCES dim_departments(department_id),
  INDEX idx_employees_department (department_id),
  INDEX idx_employees_status (employee_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_suppliers (
  supplier_id INT PRIMARY KEY,
  supplier_name VARCHAR(100) NOT NULL,
  supplier_category VARCHAR(50) NOT NULL,
  country VARCHAR(50) NOT NULL,
  rating DECIMAL(3,2) NOT NULL,
  supplier_status VARCHAR(20) NOT NULL,
  INDEX idx_suppliers_category (supplier_category),
  INDEX idx_suppliers_status (supplier_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_warehouses (
  warehouse_id INT PRIMARY KEY,
  warehouse_name VARCHAR(100) NOT NULL,
  region VARCHAR(50) NOT NULL,
  capacity_mwh DECIMAL(12,2) NOT NULL,
  warehouse_status VARCHAR(20) NOT NULL,
  INDEX idx_warehouses_region (region)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_sales_channels (
  channel_id INT PRIMARY KEY,
  channel_name VARCHAR(50) NOT NULL UNIQUE,
  channel_type VARCHAR(30) NOT NULL,
  commission_rate DECIMAL(5,4) NOT NULL,
  INDEX idx_channels_type (channel_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_currencies (
  currency_code VARCHAR(10) PRIMARY KEY,
  currency_name VARCHAR(50) NOT NULL,
  currency_symbol VARCHAR(10) NOT NULL,
  decimal_places TINYINT NOT NULL DEFAULT 2
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_calendar (
  calendar_date DATE PRIMARY KEY,
  calendar_year SMALLINT NOT NULL,
  calendar_quarter TINYINT NOT NULL,
  calendar_month TINYINT NOT NULL,
  month_name VARCHAR(20) NOT NULL,
  is_workday BOOLEAN NOT NULL,
  INDEX idx_calendar_period (calendar_year, calendar_quarter, calendar_month)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_cost_centers (
  cost_center_id INT PRIMARY KEY,
  cost_center_code VARCHAR(20) NOT NULL UNIQUE,
  cost_center_name VARCHAR(100) NOT NULL,
  department_id INT NOT NULL,
  annual_budget DECIMAL(14,2) NOT NULL,
  CONSTRAINT fk_cost_centers_department FOREIGN KEY (department_id) REFERENCES dim_departments(department_id),
  INDEX idx_cost_centers_department (department_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS purchase_orders (
  purchase_order_id BIGINT PRIMARY KEY,
  purchase_order_no VARCHAR(50) NOT NULL UNIQUE,
  supplier_id INT NOT NULL,
  order_date DATE NOT NULL,
  expected_date DATE NOT NULL,
  order_status VARCHAR(20) NOT NULL,
  currency VARCHAR(10) NOT NULL,
  total_amount DECIMAL(14,2) NOT NULL,
  CONSTRAINT fk_purchase_orders_supplier FOREIGN KEY (supplier_id) REFERENCES dim_suppliers(supplier_id),
  INDEX idx_purchase_orders_date_status (order_date, order_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS purchase_order_items (
  purchase_order_item_id BIGINT PRIMARY KEY,
  purchase_order_id BIGINT NOT NULL,
  product_id INT NOT NULL,
  quantity DECIMAL(12,2) NOT NULL,
  unit_cost DECIMAL(12,2) NOT NULL,
  received_quantity DECIMAL(12,2) NOT NULL DEFAULT 0,
  CONSTRAINT fk_purchase_items_order FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(purchase_order_id),
  CONSTRAINT fk_purchase_items_product FOREIGN KEY (product_id) REFERENCES dim_products(product_id),
  INDEX idx_purchase_items_order (purchase_order_id),
  INDEX idx_purchase_items_product (product_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sales_order_items (
  sales_order_item_id BIGINT PRIMARY KEY,
  order_id BIGINT NOT NULL,
  product_id INT NOT NULL,
  quantity DECIMAL(12,2) NOT NULL,
  unit_price DECIMAL(12,2) NOT NULL,
  discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  line_net_amount DECIMAL(14,2) NOT NULL,
  CONSTRAINT fk_sales_items_order FOREIGN KEY (order_id) REFERENCES sales_orders(order_id),
  CONSTRAINT fk_sales_items_product FOREIGN KEY (product_id) REFERENCES dim_products(product_id),
  INDEX idx_sales_items_order (order_id),
  INDEX idx_sales_items_product (product_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS inventory_balances (
  inventory_balance_id BIGINT PRIMARY KEY,
  warehouse_id INT NOT NULL,
  product_id INT NOT NULL,
  snapshot_date DATE NOT NULL,
  quantity_on_hand DECIMAL(14,2) NOT NULL,
  quantity_reserved DECIMAL(14,2) NOT NULL DEFAULT 0,
  inventory_value DECIMAL(14,2) NOT NULL,
  CONSTRAINT fk_inventory_balance_warehouse FOREIGN KEY (warehouse_id) REFERENCES dim_warehouses(warehouse_id),
  CONSTRAINT fk_inventory_balance_product FOREIGN KEY (product_id) REFERENCES dim_products(product_id),
  UNIQUE KEY uk_inventory_snapshot (warehouse_id, product_id, snapshot_date),
  INDEX idx_inventory_balance_date (snapshot_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS inventory_transactions (
  inventory_transaction_id BIGINT PRIMARY KEY,
  warehouse_id INT NOT NULL,
  product_id INT NOT NULL,
  transaction_date DATETIME NOT NULL,
  transaction_type VARCHAR(20) NOT NULL,
  quantity DECIMAL(14,2) NOT NULL,
  reference_type VARCHAR(30) NOT NULL,
  reference_id BIGINT NOT NULL,
  CONSTRAINT fk_inventory_tx_warehouse FOREIGN KEY (warehouse_id) REFERENCES dim_warehouses(warehouse_id),
  CONSTRAINT fk_inventory_tx_product FOREIGN KEY (product_id) REFERENCES dim_products(product_id),
  INDEX idx_inventory_tx_date_type (transaction_date, transaction_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS production_orders (
  production_order_id BIGINT PRIMARY KEY,
  production_order_no VARCHAR(50) NOT NULL UNIQUE,
  product_id INT NOT NULL,
  planned_quantity DECIMAL(12,2) NOT NULL,
  start_date DATE NOT NULL,
  due_date DATE NOT NULL,
  production_status VARCHAR(20) NOT NULL,
  CONSTRAINT fk_production_orders_product FOREIGN KEY (product_id) REFERENCES dim_products(product_id),
  INDEX idx_production_orders_date_status (start_date, production_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS production_outputs (
  production_output_id BIGINT PRIMARY KEY,
  production_order_id BIGINT NOT NULL,
  output_date DATE NOT NULL,
  good_quantity DECIMAL(12,2) NOT NULL,
  defect_quantity DECIMAL(12,2) NOT NULL,
  shift_name VARCHAR(20) NOT NULL,
  CONSTRAINT fk_production_outputs_order FOREIGN KEY (production_order_id) REFERENCES production_orders(production_order_id),
  INDEX idx_production_outputs_date (output_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS quality_inspections (
  inspection_id BIGINT PRIMARY KEY,
  production_order_id BIGINT NOT NULL,
  inspection_date DATE NOT NULL,
  inspected_quantity DECIMAL(12,2) NOT NULL,
  passed_quantity DECIMAL(12,2) NOT NULL,
  defect_type VARCHAR(50) NULL,
  inspection_result VARCHAR(20) NOT NULL,
  CONSTRAINT fk_quality_order FOREIGN KEY (production_order_id) REFERENCES production_orders(production_order_id),
  INDEX idx_quality_date_result (inspection_date, inspection_result)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS equipment (
  equipment_id INT PRIMARY KEY,
  equipment_code VARCHAR(30) NOT NULL UNIQUE,
  equipment_name VARCHAR(100) NOT NULL,
  equipment_type VARCHAR(50) NOT NULL,
  department_id INT NOT NULL,
  purchase_date DATE NOT NULL,
  equipment_status VARCHAR(20) NOT NULL,
  CONSTRAINT fk_equipment_department FOREIGN KEY (department_id) REFERENCES dim_departments(department_id),
  INDEX idx_equipment_type_status (equipment_type, equipment_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS equipment_maintenance (
  maintenance_id BIGINT PRIMARY KEY,
  equipment_id INT NOT NULL,
  maintenance_date DATE NOT NULL,
  maintenance_type VARCHAR(30) NOT NULL,
  downtime_hours DECIMAL(8,2) NOT NULL,
  maintenance_cost DECIMAL(12,2) NOT NULL,
  maintenance_result VARCHAR(30) NOT NULL,
  CONSTRAINT fk_maintenance_equipment FOREIGN KEY (equipment_id) REFERENCES equipment(equipment_id),
  INDEX idx_maintenance_date (maintenance_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS shipments (
  shipment_id BIGINT PRIMARY KEY,
  shipment_no VARCHAR(50) NOT NULL UNIQUE,
  order_id BIGINT NOT NULL,
  warehouse_id INT NOT NULL,
  shipment_date DATE NOT NULL,
  delivery_date DATE NULL,
  carrier_name VARCHAR(100) NOT NULL,
  shipment_status VARCHAR(20) NOT NULL,
  freight_cost DECIMAL(12,2) NOT NULL,
  CONSTRAINT fk_shipments_order FOREIGN KEY (order_id) REFERENCES sales_orders(order_id),
  CONSTRAINT fk_shipments_warehouse FOREIGN KEY (warehouse_id) REFERENCES dim_warehouses(warehouse_id),
  INDEX idx_shipments_date_status (shipment_date, shipment_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS shipment_items (
  shipment_item_id BIGINT PRIMARY KEY,
  shipment_id BIGINT NOT NULL,
  sales_order_item_id BIGINT NOT NULL,
  shipped_quantity DECIMAL(12,2) NOT NULL,
  CONSTRAINT fk_shipment_items_shipment FOREIGN KEY (shipment_id) REFERENCES shipments(shipment_id),
  CONSTRAINT fk_shipment_items_sales_item FOREIGN KEY (sales_order_item_id) REFERENCES sales_order_items(sales_order_item_id),
  INDEX idx_shipment_items_shipment (shipment_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS customer_payments (
  customer_payment_id BIGINT PRIMARY KEY,
  customer_id INT NOT NULL,
  order_id BIGINT NOT NULL,
  payment_date DATE NOT NULL,
  payment_amount DECIMAL(14,2) NOT NULL,
  currency VARCHAR(10) NOT NULL,
  payment_method VARCHAR(30) NOT NULL,
  payment_status VARCHAR(20) NOT NULL,
  CONSTRAINT fk_customer_payments_customer FOREIGN KEY (customer_id) REFERENCES dim_customers(customer_id),
  CONSTRAINT fk_customer_payments_order FOREIGN KEY (order_id) REFERENCES sales_orders(order_id),
  INDEX idx_customer_payments_date_status (payment_date, payment_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS supplier_payments (
  supplier_payment_id BIGINT PRIMARY KEY,
  supplier_id INT NOT NULL,
  purchase_order_id BIGINT NOT NULL,
  payment_date DATE NOT NULL,
  payment_amount DECIMAL(14,2) NOT NULL,
  currency VARCHAR(10) NOT NULL,
  payment_status VARCHAR(20) NOT NULL,
  CONSTRAINT fk_supplier_payments_supplier FOREIGN KEY (supplier_id) REFERENCES dim_suppliers(supplier_id),
  CONSTRAINT fk_supplier_payments_order FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(purchase_order_id),
  INDEX idx_supplier_payments_date_status (payment_date, payment_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS budgets (
  budget_id BIGINT PRIMARY KEY,
  cost_center_id INT NOT NULL,
  budget_year SMALLINT NOT NULL,
  budget_month TINYINT NOT NULL,
  budget_category VARCHAR(50) NOT NULL,
  budget_amount DECIMAL(14,2) NOT NULL,
  actual_amount DECIMAL(14,2) NOT NULL,
  CONSTRAINT fk_budgets_cost_center FOREIGN KEY (cost_center_id) REFERENCES dim_cost_centers(cost_center_id),
  UNIQUE KEY uk_budget_period_category (cost_center_id, budget_year, budget_month, budget_category)
) ENGINE=InnoDB;
