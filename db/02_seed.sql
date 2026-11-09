USE chatbi_mvp;

INSERT INTO dim_customers
  (customer_id, customer_name, customer_type, industry, country, region)
VALUES
  (1, '莱茵汽车', 'OEM整车厂', '交通', 'Germany', '欧洲'),
  (2, '北辰电网', '电网集团', '能源', 'China', '亚太'),
  (3, '星河储能', '储能集成商', '能源', 'United States', '北美'),
  (4, '远航工业', '工商业用户', '工业', 'Brazil', '拉美'),
  (5, '绿洲能源', '工商业用户', '能源', 'United Arab Emirates', '中东非洲')
ON DUPLICATE KEY UPDATE
  customer_name=VALUES(customer_name), customer_type=VALUES(customer_type),
  industry=VALUES(industry), country=VALUES(country), region=VALUES(region);

INSERT INTO dim_products
  (product_id, product_name, product_line, category, tech_route,
   standard_cost, material_cost, labor_cost)
VALUES
  (101, '乘用车高密度电池包', '动力电池-乘用车', '高能量密度型', '三元锂', 720.00, 560.00, 90.00),
  (102, '商用车快充电池包', '动力电池-商用车', '商用车标准型', '磷酸铁锂', 610.00, 460.00, 80.00),
  (103, '电网级液冷储能柜', '储能系统-电网级', '电网级储能型', '磷酸铁锂', 880.00, 690.00, 100.00),
  (104, '工商业储能一体柜', '储能系统-工商业', '工商业储能型', '钠离子', 760.00, 570.00, 95.00),
  (105, '电池回收材料包', '电池材料与回收', '低温适配型', '磷酸铁锂', 300.00, 210.00, 35.00)
ON DUPLICATE KEY UPDATE
  product_name=VALUES(product_name), product_line=VALUES(product_line),
  category=VALUES(category), tech_route=VALUES(tech_route),
  standard_cost=VALUES(standard_cost), material_cost=VALUES(material_cost),
  labor_cost=VALUES(labor_cost);

INSERT INTO exchange_rates (rate_date, currency, rate_to_cny)
VALUES
  ('2026-01-15', 'CNY', 1.0000), ('2026-03-20', 'CNY', 1.0000),
  ('2026-07-10', 'USD', 7.1800), ('2026-08-05', 'EUR', 7.8500),
  ('2026-08-18', 'USD', 7.1600), ('2026-09-12', 'CNY', 1.0000),
  ('2026-09-25', 'BRL', 1.3100), ('2026-10-08', 'EUR', 7.9200),
  ('2026-10-22', 'CNY', 1.0000), ('2026-11-02', 'USD', 7.1200)
ON DUPLICATE KEY UPDATE rate_to_cny=VALUES(rate_to_cny);

INSERT INTO sales_orders
  (order_id, order_no, customer_id, product_id, region, order_date, order_status,
   quantity, unit_price, discount_amount, gross_amount, net_amount, currency)
VALUES
  (1001, 'SO-2026-001', 2, 103, '亚太', '2026-01-15', 'completed', 20, 1200, 500, 26550, 23500, 'CNY'),
  (1002, 'SO-2026-002', 2, 102, '亚太', '2026-03-20', 'cancelled', 10, 900, 0, 10170, 9000, 'CNY'),
  (1003, 'SO-2026-003', 3, 103, '北美', '2026-07-10', 'completed', 12, 1350, 300, 17910, 15900, 'USD'),
  (1004, 'SO-2026-004', 1, 101, '欧洲', '2026-08-05', 'completed', 15, 1100, 200, 18418, 16300, 'EUR'),
  (1005, 'SO-2026-005', 3, 104, '北美', '2026-08-18', 'pending', 8, 980, 0, 8859, 7840, 'USD'),
  (1006, 'SO-2026-006', 2, 103, '亚太', '2026-09-12', 'completed', 18, 1180, 240, 23729, 21000, 'CNY'),
  (1007, 'SO-2026-007', 4, 104, '拉美', '2026-09-25', 'completed', 9, 1000, 100, 10057, 8900, 'BRL'),
  (1008, 'SO-2026-008', 1, 101, '欧洲', '2026-10-08', 'completed', 11, 1150, 150, 14125, 12500, 'EUR'),
  (1009, 'SO-2026-009', 2, 105, '亚太', '2026-10-22', 'completed', 30, 460, 300, 15255, 13500, 'CNY'),
  (1010, 'SO-2026-010', 5, 104, '中东非洲', '2026-11-02', 'completed', 7, 1050, 50, 8249, 7300, 'USD')
ON DUPLICATE KEY UPDATE
  customer_id=VALUES(customer_id), product_id=VALUES(product_id), region=VALUES(region),
  order_date=VALUES(order_date), order_status=VALUES(order_status), quantity=VALUES(quantity),
  unit_price=VALUES(unit_price), discount_amount=VALUES(discount_amount),
  gross_amount=VALUES(gross_amount), net_amount=VALUES(net_amount), currency=VALUES(currency);

INSERT INTO finance_expenses
  (expense_id, expense_date, department, rd_expense, selling_expense, admin_expense,
   finance_expense, marketing_expense, logistics_expense, warranty_expense)
VALUES
  (2001, '2026-01-31', '总部', 500000, 220000, 160000, 70000, 80000, 90000, 50000),
  (2002, '2026-03-31', '总部', 530000, 235000, 165000, 68000, 85000, 95000, 55000),
  (2003, '2026-08-31', '欧洲事业部', 180000, 120000, 70000, 25000, 45000, 50000, 25000),
  (2004, '2026-09-30', '亚太事业部', 210000, 140000, 80000, 30000, 50000, 60000, 30000),
  (2005, '2026-10-31', '北美事业部', 195000, 135000, 76000, 28000, 48000, 57000, 30000)
ON DUPLICATE KEY UPDATE
  expense_date=VALUES(expense_date), department=VALUES(department),
  rd_expense=VALUES(rd_expense), selling_expense=VALUES(selling_expense),
  admin_expense=VALUES(admin_expense), finance_expense=VALUES(finance_expense),
  marketing_expense=VALUES(marketing_expense), logistics_expense=VALUES(logistics_expense),
  warranty_expense=VALUES(warranty_expense);
