USE chatbi_mvp;

INSERT INTO dim_regions (region_id, region_name, region_manager) VALUES
  (1, '亚太', '陈晓'), (2, '欧洲', 'Anna Müller'), (3, '北美', 'David Lee'),
  (4, '拉美', 'Carlos Silva'), (5, '中东非洲', 'Omar Hassan')
ON DUPLICATE KEY UPDATE region_name=VALUES(region_name), region_manager=VALUES(region_manager);

INSERT INTO dim_countries (country_id, country_name, region_id, iso_code) VALUES
  (1, 'China', 1, 'CN'), (2, 'Germany', 2, 'DE'), (3, 'United States', 3, 'US'),
  (4, 'Brazil', 4, 'BR'), (5, 'United Arab Emirates', 5, 'AE')
ON DUPLICATE KEY UPDATE country_name=VALUES(country_name), region_id=VALUES(region_id), iso_code=VALUES(iso_code);

INSERT INTO dim_departments (department_id, department_name, department_type, parent_department_id) VALUES
  (1, '总部', '管理', NULL), (2, '研发中心', '研发', NULL), (3, '销售中心', '销售', NULL),
  (4, '生产中心', '生产', NULL), (5, '供应链中心', '供应链', NULL), (6, '财务中心', '财务', NULL)
ON DUPLICATE KEY UPDATE department_name=VALUES(department_name), department_type=VALUES(department_type), parent_department_id=VALUES(parent_department_id);

INSERT INTO dim_employees (employee_id, employee_name, department_id, job_title, hire_date, employee_status) VALUES
  (1, '张伟', 2, '电池研发工程师', '2022-03-15', 'active'),
  (2, '李娜', 3, '区域销售经理', '2021-08-01', 'active'),
  (3, '王强', 4, '生产主管', '2020-05-20', 'active'),
  (4, '赵敏', 5, '采购经理', '2023-01-10', 'active'),
  (5, '周静', 6, '财务分析师', '2022-11-07', 'active')
ON DUPLICATE KEY UPDATE employee_name=VALUES(employee_name), department_id=VALUES(department_id), job_title=VALUES(job_title), hire_date=VALUES(hire_date), employee_status=VALUES(employee_status);

INSERT INTO dim_suppliers (supplier_id, supplier_name, supplier_category, country, rating, supplier_status) VALUES
  (1, '华南锂材', '正极材料', 'China', 4.80, 'active'),
  (2, '北方电解液', '电解液', 'China', 4.55, 'active'),
  (3, '莱茵设备', '生产设备', 'Germany', 4.70, 'active'),
  (4, '安泰包装', '包装材料', 'China', 4.20, 'active')
ON DUPLICATE KEY UPDATE supplier_name=VALUES(supplier_name), supplier_category=VALUES(supplier_category), country=VALUES(country), rating=VALUES(rating), supplier_status=VALUES(supplier_status);

INSERT INTO dim_warehouses (warehouse_id, warehouse_name, region, capacity_mwh, warehouse_status) VALUES
  (1, '华东成品仓', '亚太', 5000, 'active'), (2, '欧洲备件仓', '欧洲', 1200, 'active'),
  (3, '北美配送仓', '北美', 1800, 'active'), (4, '华南原料仓', '亚太', 3500, 'active')
ON DUPLICATE KEY UPDATE warehouse_name=VALUES(warehouse_name), region=VALUES(region), capacity_mwh=VALUES(capacity_mwh), warehouse_status=VALUES(warehouse_status);

INSERT INTO dim_sales_channels (channel_id, channel_name, channel_type, commission_rate) VALUES
  (1, '直销', 'direct', 0.0000), (2, '战略经销商', 'dealer', 0.0350),
  (3, '海外代理', 'agent', 0.0500), (4, '项目投标', 'tender', 0.0100)
ON DUPLICATE KEY UPDATE channel_name=VALUES(channel_name), channel_type=VALUES(channel_type), commission_rate=VALUES(commission_rate);

INSERT INTO dim_currencies (currency_code, currency_name, currency_symbol, decimal_places) VALUES
  ('CNY', '人民币', '¥', 2), ('USD', '美元', '$', 2), ('EUR', '欧元', '€', 2), ('BRL', '巴西雷亚尔', 'R$', 2)
ON DUPLICATE KEY UPDATE currency_name=VALUES(currency_name), currency_symbol=VALUES(currency_symbol), decimal_places=VALUES(decimal_places);

INSERT INTO dim_calendar (calendar_date, calendar_year, calendar_quarter, calendar_month, month_name, is_workday) VALUES
  ('2026-01-15', 2026, 1, 1, '一月', TRUE), ('2026-03-20', 2026, 1, 3, '三月', TRUE),
  ('2026-07-10', 2026, 3, 7, '七月', TRUE), ('2026-08-05', 2026, 3, 8, '八月', TRUE),
  ('2026-08-18', 2026, 3, 8, '八月', TRUE), ('2026-09-12', 2026, 3, 9, '九月', FALSE),
  ('2026-09-25', 2026, 3, 9, '九月', TRUE), ('2026-10-08', 2026, 4, 10, '十月', TRUE),
  ('2026-10-22', 2026, 4, 10, '十月', TRUE), ('2026-11-02', 2026, 4, 11, '十一月', TRUE)
ON DUPLICATE KEY UPDATE calendar_year=VALUES(calendar_year), calendar_quarter=VALUES(calendar_quarter), calendar_month=VALUES(calendar_month), month_name=VALUES(month_name), is_workday=VALUES(is_workday);

INSERT INTO dim_cost_centers (cost_center_id, cost_center_code, cost_center_name, department_id, annual_budget) VALUES
  (1, 'CC-RD-001', '电池研发成本中心', 2, 12000000),
  (2, 'CC-SALES-001', '国内销售成本中心', 3, 8000000),
  (3, 'CC-MFG-001', '电池制造成本中心', 4, 25000000),
  (4, 'CC-SCM-001', '供应链成本中心', 5, 6000000)
ON DUPLICATE KEY UPDATE cost_center_code=VALUES(cost_center_code), cost_center_name=VALUES(cost_center_name), department_id=VALUES(department_id), annual_budget=VALUES(annual_budget);

INSERT INTO purchase_orders (purchase_order_id, purchase_order_no, supplier_id, order_date, expected_date, order_status, currency, total_amount) VALUES
  (3001, 'PO-2026-001', 1, '2026-07-01', '2026-07-20', 'received', 'CNY', 560000),
  (3002, 'PO-2026-002', 2, '2026-08-10', '2026-08-30', 'received', 'CNY', 230000),
  (3003, 'PO-2026-003', 3, '2026-09-05', '2026-10-05', 'open', 'EUR', 180000)
ON DUPLICATE KEY UPDATE supplier_id=VALUES(supplier_id), order_date=VALUES(order_date), expected_date=VALUES(expected_date), order_status=VALUES(order_status), currency=VALUES(currency), total_amount=VALUES(total_amount);

INSERT INTO purchase_order_items (purchase_order_item_id, purchase_order_id, product_id, quantity, unit_cost, received_quantity) VALUES
  (3101, 3001, 101, 1000, 560, 1000), (3102, 3002, 103, 500, 460, 500), (3103, 3003, 104, 200, 900, 0)
ON DUPLICATE KEY UPDATE purchase_order_id=VALUES(purchase_order_id), product_id=VALUES(product_id), quantity=VALUES(quantity), unit_cost=VALUES(unit_cost), received_quantity=VALUES(received_quantity);

INSERT INTO sales_order_items (sales_order_item_id, order_id, product_id, quantity, unit_price, discount_amount, line_net_amount) VALUES
  (4001, 1001, 103, 20, 1200, 500, 23500), (4002, 1003, 103, 12, 1350, 300, 15900),
  (4003, 1004, 101, 15, 1100, 200, 16300), (4004, 1006, 103, 18, 1180, 240, 21000),
  (4005, 1007, 104, 9, 1000, 100, 8900), (4006, 1008, 101, 11, 1150, 150, 12500),
  (4007, 1009, 105, 30, 460, 300, 13500), (4008, 1010, 104, 7, 1050, 50, 7300)
ON DUPLICATE KEY UPDATE order_id=VALUES(order_id), product_id=VALUES(product_id), quantity=VALUES(quantity), unit_price=VALUES(unit_price), discount_amount=VALUES(discount_amount), line_net_amount=VALUES(line_net_amount);

INSERT INTO inventory_balances (inventory_balance_id, warehouse_id, product_id, snapshot_date, quantity_on_hand, quantity_reserved, inventory_value) VALUES
  (5001, 1, 101, '2026-11-01', 120, 25, 86400), (5002, 1, 103, '2026-11-01', 80, 18, 70400),
  (5003, 2, 101, '2026-11-01', 35, 10, 25200), (5004, 3, 104, '2026-11-01', 42, 7, 31920),
  (5005, 4, 105, '2026-11-01', 260, 40, 78000)
ON DUPLICATE KEY UPDATE quantity_on_hand=VALUES(quantity_on_hand), quantity_reserved=VALUES(quantity_reserved), inventory_value=VALUES(inventory_value);

INSERT INTO inventory_transactions (inventory_transaction_id, warehouse_id, product_id, transaction_date, transaction_type, quantity, reference_type, reference_id) VALUES
  (5101, 1, 101, '2026-10-08 09:00:00', 'outbound', -11, 'sales_order', 1008),
  (5102, 1, 103, '2026-09-12 11:30:00', 'outbound', -18, 'sales_order', 1006),
  (5103, 4, 101, '2026-07-20 15:00:00', 'inbound', 1000, 'purchase_order', 3001),
  (5104, 4, 103, '2026-08-30 14:00:00', 'inbound', 500, 'purchase_order', 3002)
ON DUPLICATE KEY UPDATE warehouse_id=VALUES(warehouse_id), product_id=VALUES(product_id), transaction_date=VALUES(transaction_date), transaction_type=VALUES(transaction_type), quantity=VALUES(quantity), reference_type=VALUES(reference_type), reference_id=VALUES(reference_id);

INSERT INTO production_orders (production_order_id, production_order_no, product_id, planned_quantity, start_date, due_date, production_status) VALUES
  (6001, 'MO-2026-001', 101, 200, '2026-08-01', '2026-08-20', 'completed'),
  (6002, 'MO-2026-002', 103, 150, '2026-09-01', '2026-09-25', 'completed'),
  (6003, 'MO-2026-003', 104, 100, '2026-10-15', '2026-11-10', 'in_progress')
ON DUPLICATE KEY UPDATE product_id=VALUES(product_id), planned_quantity=VALUES(planned_quantity), start_date=VALUES(start_date), due_date=VALUES(due_date), production_status=VALUES(production_status);

INSERT INTO production_outputs (production_output_id, production_order_id, output_date, good_quantity, defect_quantity, shift_name) VALUES
  (6101, 6001, '2026-08-20', 194, 6, '白班'), (6102, 6002, '2026-09-24', 147, 3, '夜班'),
  (6103, 6003, '2026-11-01', 62, 2, '白班')
ON DUPLICATE KEY UPDATE production_order_id=VALUES(production_order_id), output_date=VALUES(output_date), good_quantity=VALUES(good_quantity), defect_quantity=VALUES(defect_quantity), shift_name=VALUES(shift_name);

INSERT INTO quality_inspections (inspection_id, production_order_id, inspection_date, inspected_quantity, passed_quantity, defect_type, inspection_result) VALUES
  (6201, 6001, '2026-08-20', 200, 194, '外观瑕疵', 'passed'),
  (6202, 6002, '2026-09-24', 150, 147, '容量偏差', 'passed'),
  (6203, 6003, '2026-11-01', 64, 62, '焊点异常', 'conditional')
ON DUPLICATE KEY UPDATE production_order_id=VALUES(production_order_id), inspection_date=VALUES(inspection_date), inspected_quantity=VALUES(inspected_quantity), passed_quantity=VALUES(passed_quantity), defect_type=VALUES(defect_type), inspection_result=VALUES(inspection_result);

INSERT INTO equipment (equipment_id, equipment_code, equipment_name, equipment_type, department_id, purchase_date, equipment_status) VALUES
  (1, 'EQ-COAT-01', '高速涂布机', '涂布设备', 4, '2022-06-01', 'running'),
  (2, 'EQ-WELD-01', '激光焊接机', '焊接设备', 4, '2023-03-15', 'running'),
  (3, 'EQ-TEST-01', '电芯测试柜', '测试设备', 2, '2021-11-20', 'maintenance')
ON DUPLICATE KEY UPDATE equipment_code=VALUES(equipment_code), equipment_name=VALUES(equipment_name), equipment_type=VALUES(equipment_type), department_id=VALUES(department_id), purchase_date=VALUES(purchase_date), equipment_status=VALUES(equipment_status);

INSERT INTO equipment_maintenance (maintenance_id, equipment_id, maintenance_date, maintenance_type, downtime_hours, maintenance_cost, maintenance_result) VALUES
  (7001, 1, '2026-08-12', 'preventive', 4.0, 8500, 'completed'),
  (7002, 2, '2026-09-18', 'corrective', 7.5, 16000, 'completed'),
  (7003, 3, '2026-11-01', 'calibration', 12.0, 6800, 'in_progress')
ON DUPLICATE KEY UPDATE equipment_id=VALUES(equipment_id), maintenance_date=VALUES(maintenance_date), maintenance_type=VALUES(maintenance_type), downtime_hours=VALUES(downtime_hours), maintenance_cost=VALUES(maintenance_cost), maintenance_result=VALUES(maintenance_result);

INSERT INTO shipments (shipment_id, shipment_no, order_id, warehouse_id, shipment_date, delivery_date, carrier_name, shipment_status, freight_cost) VALUES
  (8001, 'SH-2026-001', 1001, 1, '2026-01-16', '2026-01-18', '顺丰供应链', 'delivered', 1200),
  (8002, 'SH-2026-002', 1003, 3, '2026-07-12', '2026-07-17', 'DHL', 'delivered', 3800),
  (8003, 'SH-2026-003', 1008, 2, '2026-10-10', '2026-10-14', 'DB Schenker', 'delivered', 2600),
  (8004, 'SH-2026-004', 1010, 1, '2026-11-03', NULL, 'Maersk', 'in_transit', 4200)
ON DUPLICATE KEY UPDATE order_id=VALUES(order_id), warehouse_id=VALUES(warehouse_id), shipment_date=VALUES(shipment_date), delivery_date=VALUES(delivery_date), carrier_name=VALUES(carrier_name), shipment_status=VALUES(shipment_status), freight_cost=VALUES(freight_cost);

INSERT INTO shipment_items (shipment_item_id, shipment_id, sales_order_item_id, shipped_quantity) VALUES
  (8101, 8001, 4001, 20), (8102, 8002, 4002, 12), (8103, 8003, 4006, 11), (8104, 8004, 4008, 7)
ON DUPLICATE KEY UPDATE shipment_id=VALUES(shipment_id), sales_order_item_id=VALUES(sales_order_item_id), shipped_quantity=VALUES(shipped_quantity);

INSERT INTO customer_payments (customer_payment_id, customer_id, order_id, payment_date, payment_amount, currency, payment_method, payment_status) VALUES
  (9001, 2, 1001, '2026-02-15', 23500, 'CNY', 'bank_transfer', 'received'),
  (9002, 3, 1003, '2026-08-10', 15900, 'USD', 'bank_transfer', 'received'),
  (9003, 1, 1008, '2026-11-08', 12500, 'EUR', 'letter_of_credit', 'received'),
  (9004, 5, 1010, '2026-11-05', 3650, 'USD', 'bank_transfer', 'partial')
ON DUPLICATE KEY UPDATE customer_id=VALUES(customer_id), order_id=VALUES(order_id), payment_date=VALUES(payment_date), payment_amount=VALUES(payment_amount), currency=VALUES(currency), payment_method=VALUES(payment_method), payment_status=VALUES(payment_status);

INSERT INTO supplier_payments (supplier_payment_id, supplier_id, purchase_order_id, payment_date, payment_amount, currency, payment_status) VALUES
  (9101, 1, 3001, '2026-07-25', 560000, 'CNY', 'paid'),
  (9102, 2, 3002, '2026-09-05', 230000, 'CNY', 'paid'),
  (9103, 3, 3003, '2026-10-01', 54000, 'EUR', 'partial')
ON DUPLICATE KEY UPDATE supplier_id=VALUES(supplier_id), purchase_order_id=VALUES(purchase_order_id), payment_date=VALUES(payment_date), payment_amount=VALUES(payment_amount), currency=VALUES(currency), payment_status=VALUES(payment_status);

INSERT INTO budgets (budget_id, cost_center_id, budget_year, budget_month, budget_category, budget_amount, actual_amount) VALUES
  (10001, 1, 2026, 8, '研发材料', 900000, 875000),
  (10002, 2, 2026, 8, '市场推广', 350000, 328000),
  (10003, 3, 2026, 9, '生产制造', 2100000, 1985000),
  (10004, 4, 2026, 9, '物流运输', 420000, 438000)
ON DUPLICATE KEY UPDATE budget_amount=VALUES(budget_amount), actual_amount=VALUES(actual_amount);
