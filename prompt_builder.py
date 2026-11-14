"""
Prompt 构造模块

负责将 Schema 信息、Few-shot 示例和用户问题组装为完整 Prompt。
Schema 和示例在此集中维护，便于后续课程中动态扩展。

第7课增强：新增 RULES（业务规则注入层）与 ERROR_GUARDS（错误防护层），
通过 build_prompt 的可选参数控制是否注入，实现 Prompt 策略的灵活切换。
"""


# ==================== Schema 定义 ====================
SCHEMA = """
表：dim_customers（客户维度表）
- customer_id INT 主键
- customer_name VARCHAR(100) 客户名称
- customer_type VARCHAR(50) 客户类型：OEM整车厂 / 储能集成商 / 电网集团 / 工商业用户 / 换电运营商 / 经销商
- industry VARCHAR(50) 客户行业：交通 / 能源 / 工业 / 特种交通
- country VARCHAR(50) 具体国家，如 Germany
- region VARCHAR(50) 大区，如 欧洲、北美

表：dim_products（产品维度表）
- product_id INT 主键
- product_name VARCHAR(100) 产品名称
- product_line VARCHAR(50) 产品线：动力电池-乘用车 / 动力电池-商用车 / 储能系统-电网级 / 储能系统-工商业 / 电池材料与回收
- category VARCHAR(50) 产品分类：高能量密度型 / 超快充型 / 混动专用型 / 低温适配型 / 商用车标准型 / 电网级储能型 / 工商业储能型
- tech_route VARCHAR(50) 技术路线：三元锂 / 磷酸铁锂 / 钠离子 / 固态电池
- standard_cost DECIMAL(10,2) 标准成本
- material_cost DECIMAL(10,2) 材料成本
- labor_cost DECIMAL(10,2) 人工成本

表：sales_orders（销售订单表）
- order_id BIGINT 主键
- order_no VARCHAR(50) 订单编号
- customer_id INT 外键 → dim_customers.customer_id
- product_id INT 外键 → dim_products.product_id
- region VARCHAR(50) 销售区域
- order_date DATE 订单日期
- order_status VARCHAR(20) 订单状态：completed / cancelled / pending
- quantity DECIMAL(10,2) 数量（MWh 或套数）
- unit_price DECIMAL(10,2) 单价（每 MWh 或每套价格，不含税）
- discount_amount DECIMAL(10,2) 折扣金额
- gross_amount DECIMAL(12,2) 含税总额
- net_amount DECIMAL(12,2) 不含税收入（财务口径的销售额）
- currency VARCHAR(10) 币种

表：exchange_rates（汇率表）
- rate_date DATE 日期
- currency VARCHAR(10) 币种
- rate_to_cny DECIMAL(10,4) 兑人民币汇率

表：finance_expenses（费用表）
- expense_id BIGINT 主键
- expense_date DATE 费用日期
- department VARCHAR(50) 部门
- rd_expense DECIMAL(12,2) 研发费用（新能源企业研发投入大）
- selling_expense DECIMAL(12,2) 销售费用
- admin_expense DECIMAL(12,2) 管理费用
- finance_expense DECIMAL(12,2) 财务费用
- marketing_expense DECIMAL(12,2) 市场费用（属于销售费用子项）
- logistics_expense DECIMAL(12,2) 物流费用
- warranty_expense DECIMAL(12,2) 质保费用
"""

SCHEMA += """

表：dim_regions（区域维度）：region_id, region_name, region_manager
表：dim_countries（国家维度）：country_id, country_name, region_id→dim_regions.region_id, iso_code
表：dim_departments（部门维度）：department_id, department_name, department_type, parent_department_id
表：dim_employees（员工维度）：employee_id, employee_name, department_id→dim_departments.department_id, job_title, hire_date, employee_status
表：dim_suppliers（供应商维度）：supplier_id, supplier_name, supplier_category, country, rating, supplier_status
表：dim_warehouses（仓库维度）：warehouse_id, warehouse_name, region, capacity_mwh, warehouse_status
表：dim_sales_channels（销售渠道维度）：channel_id, channel_name, channel_type, commission_rate
表：dim_currencies（币种维度）：currency_code, currency_name, currency_symbol, decimal_places
表：dim_calendar（日历维度）：calendar_date, calendar_year, calendar_quarter, calendar_month, month_name, is_workday
表：dim_cost_centers（成本中心维度）：cost_center_id, cost_center_code, cost_center_name, department_id→dim_departments.department_id, annual_budget
表：purchase_orders（采购订单）：purchase_order_id, purchase_order_no, supplier_id→dim_suppliers.supplier_id, order_date, expected_date, order_status, currency, total_amount
表：purchase_order_items（采购订单明细）：purchase_order_item_id, purchase_order_id→purchase_orders.purchase_order_id, product_id→dim_products.product_id, quantity, unit_cost, received_quantity
表：sales_order_items（销售订单明细）：sales_order_item_id, order_id→sales_orders.order_id, product_id→dim_products.product_id, quantity, unit_price, discount_amount, line_net_amount
表：inventory_balances（库存余额）：inventory_balance_id, warehouse_id→dim_warehouses.warehouse_id, product_id→dim_products.product_id, snapshot_date, quantity_on_hand, quantity_reserved, inventory_value
表：inventory_transactions（库存流水）：inventory_transaction_id, warehouse_id, product_id, transaction_date, transaction_type, quantity, reference_type, reference_id
表：production_orders（生产工单）：production_order_id, production_order_no, product_id→dim_products.product_id, planned_quantity, start_date, due_date, production_status
表：production_outputs（生产产出）：production_output_id, production_order_id→production_orders.production_order_id, output_date, good_quantity, defect_quantity, shift_name
表：quality_inspections（质量检验）：inspection_id, production_order_id, inspection_date, inspected_quantity, passed_quantity, defect_type, inspection_result
表：equipment（设备台账）：equipment_id, equipment_code, equipment_name, equipment_type, department_id, purchase_date, equipment_status
表：equipment_maintenance（设备维护）：maintenance_id, equipment_id→equipment.equipment_id, maintenance_date, maintenance_type, downtime_hours, maintenance_cost, maintenance_result
表：shipments（发运单）：shipment_id, shipment_no, order_id→sales_orders.order_id, warehouse_id, shipment_date, delivery_date, carrier_name, shipment_status, freight_cost
表：shipment_items（发运明细）：shipment_item_id, shipment_id→shipments.shipment_id, sales_order_item_id, shipped_quantity
表：customer_payments（客户回款）：customer_payment_id, customer_id, order_id, payment_date, payment_amount, currency, payment_method, payment_status
表：supplier_payments（供应商付款）：supplier_payment_id, supplier_id, purchase_order_id, payment_date, payment_amount, currency, payment_status
表：budgets（预算执行）：budget_id, cost_center_id→dim_cost_centers.cost_center_id, budget_year, budget_month, budget_category, budget_amount, actual_amount
"""


# ==================== Few-shot 示例 ====================
FEW_SHOT_EXAMPLES = """
示例1：
问题：查询已完成订单的总数量
SQL：SELECT COUNT(*) FROM sales_orders WHERE order_status = 'completed';

示例2：
问题：按客户类型统计订单数量
SQL：SELECT c.customer_type, COUNT(*) AS order_count FROM sales_orders o JOIN dim_customers c ON o.customer_id = c.customer_id WHERE o.order_status = 'completed' GROUP BY c.customer_type;

示例3：
问题：查询2026年第一季度的总费用
SQL：SELECT SUM(rd_expense + selling_expense + admin_expense + finance_expense) AS total_expense FROM finance_expenses WHERE expense_date >= '2026-01-01' AND expense_date < '2026-04-01';

示例4：
问题：查看最近三个月各月销售收入
SQL：SELECT
    DATE_FORMAT(o.order_date, '%Y-%m-01') AS month,
    SUM(o.net_amount * r.rate_to_cny) AS revenue_cny
FROM sales_orders o
JOIN exchange_rates r
  ON o.order_date = r.rate_date
 AND o.currency = r.currency
WHERE o.order_status = 'completed'
  AND o.order_date >= DATE_SUB(CURDATE(), INTERVAL 3 MONTH)
  AND o.order_date < CURDATE() + INTERVAL 1 DAY
GROUP BY DATE_FORMAT(o.order_date, '%Y-%m-01')
ORDER BY month;
"""


# ==================== 规则注入层（第7课新增）====================
RULES = """
【关键业务规则】
1. 收入口径："销售额""收入"均指不含税收入，统一使用 sales_orders.net_amount，禁止使用 gross_amount
2. 销售成本口径：销售语境中的"成本"指实际销售成本，计算公式为 dim_products.material_cost + dim_products.labor_cost；采购、设备维护、运费等明确领域成本必须遵循召回的指标定义
3. 订单统计范围：统计收入、订单量、客单价等指标时，必须过滤 order_status = 'completed'，排除 cancelled 和 pending
4. 汇率转换：涉及多币种收入汇总时，必须通过 order_date 和 currency 关联 exchange_rates 表，使用 rate_to_cny 折算为人民币
5. 时间范围："最近N个月"使用 DATE_SUB(CURDATE(), INTERVAL N MONTH) 作为起始边界；"本月"指当月1日至当前日期。按月输出统一使用 DATE_FORMAT(日期字段, '%Y-%m-01')，以月初日期标识月份。
6. 费用层级：selling_expense 是销售费用总项，包含 marketing_expense、logistics_expense、warranty_expense，汇总时不得重复计算
7. 毛利计算：毛利 = net_amount - (material_cost + labor_cost) * quantity
8. 不要引用 Schema 中不存在的字段，例如 channel
9. 指标优先级：若注入了【指标知识】，其 definition、formula、data_source、depends_on、time_field 和 filters 优先于通用规则
10. 比率指标：统一使用 NULLIF(分母, 0) 防止除零；百分比结果保留小数值，不擅自乘 100，除非用户明确要求百分数
11. 多币种原币指标：指标知识要求按 currency 分组时不得直接跨币种 SUM；只有存在匹配日期汇率时才允许折算后汇总
12. 库存快照：未指定日期时只使用最新 snapshot_date，禁止把不同快照日的库存余额累加
13. Join 去重：一对多明细表参与查询时，先按业务主键聚合再关联，避免重复放大金额、计划量或订单数
"""


# ==================== 错误防护层（第7课新增）====================
ERROR_GUARDS = """
【常见错误防护】
- 字段选择：确认金额字段是 net_amount（不含税）还是 gross_amount（含税），除非明确要求"含税"，否则一律用 net_amount
- Join 遗漏：只要查询涉及"收入"且存在 currency 字段，必须关联 exchange_rates 表做汇率转换
- 过滤遗漏：所有收入类统计必须包含 WHERE order_status = 'completed'
- 时间边界：使用 >= 和 < 组合表示闭开区间；“最近N个月”按 DATE_SUB(CURDATE(), INTERVAL N MONTH) 处理
- 聚合维度：GROUP BY 字段必须与 SELECT 中的非聚合字段完全一致
- 字段合法性：不要输出 Schema 中不存在的字段；如果问题里出现未建模维度，优先回退到产品线、区域、客户、月份等已有维度
- 指标契约：逐项核对召回指标的公式、来源、依赖、时间字段和强制过滤，不得用名称相近的指标替代
- 比率安全：比率分母必须使用 NULLIF(..., 0)，依赖指标必须保持同一过滤范围和聚合粒度
- 快照去重：库存余额等快照事实只能统计一个目标快照日；未指定日期时使用最新快照
- Join 放大：关联多个一对多事实表前分别聚合，禁止明细笛卡尔放大
"""


COMPLEX_QUERY_GUIDANCE = """
【复杂查询规划要求】
0. 复杂度由多目标和多阶段计算决定；不要因为需要 JOIN 就把普通单目标查询复杂化
1. 先在内部确定用户要求的指标、维度、时间窗口、过滤条件和关联路径，再输出 SQL
2. 多阶段计算优先使用 CTE 或子查询表达，但最终只能输出一条只读 SQL
3. 同比、环比、排名、占比和多指标对比必须保证分母、时间粒度与聚合层级一致
4. 不得为了简化而遗漏用户明确要求的任一指标或维度
"""


# ==================== Prompt 构造函数 ====================
def build_prompt(
    user_question: str,
    use_few_shot: bool = True,
    use_rules: bool = False,
    use_guards: bool = False,
    indicator_knowledge: str = "",
    use_schema_linking: bool = False,
    schema_text: str | None = None,
    query_type: str = "simple",
) -> tuple[str, str]:
    """
    构造发送给 LLM 的 Prompt

    Args:
        user_question: 用户的自然语言问题
        use_few_shot: 是否使用 Few-shot 示例
        use_rules: 是否注入业务规则层（第7课新增）
        use_guards: 是否注入错误防护层（第7课新增）
        indicator_knowledge: 指标知识文本块（第9课新增）
        use_schema_linking: 兼容旧调用方的保留参数；Schema 选择现由应用层完成。
        schema_text: 调用方显式选择的 Schema 文本；为空时使用静态全量 Schema。

    Returns:
        (system_message, user_message)
    """
    system_msg = "你是一个专业的 SQL 生成助手，擅长根据业务问题生成标准 MySQL 查询语句。"
    if use_rules or use_guards:
        system_msg = "你是一个专业的 SQL 生成助手，擅长根据业务问题生成标准 MySQL 查询语句。请严格遵守给定的业务规则，避免常见错误。"

    # Schema selection is explicit in the MVP application layer.  The legacy
    # flag is retained for call compatibility but no longer performs a hidden
    # dynamic import or silently swallows retrieval failures.
    resolved_schema_text = schema_text or SCHEMA

    prompt = f"""【数据库Schema】
{resolved_schema_text}
"""
    if use_rules:
        prompt += f"""
{RULES}
"""

    if use_few_shot:
        prompt += f"""
【示例】
{FEW_SHOT_EXAMPLES}
"""

    if use_guards:
        prompt += f"""
{ERROR_GUARDS}
"""

    if query_type == "complex":
        prompt += f"""
{COMPLEX_QUERY_GUIDANCE}
"""

    if indicator_knowledge:
        prompt += f"""
{indicator_knowledge}
"""

    prompt += f"""
【用户问题】
{user_question}

【要求】
1. 只输出 SQL 语句，不需要解释
2. 使用标准 MySQL 语法
3. 确保字段名和表名与 Schema 一致
4. 如果涉及多表查询，使用 JOIN 连接
5. 收入口径统一使用 net_amount，成本口径使用 material_cost + labor_cost
6. 统计销售额时，需要按订单日期的汇率转换为人民币
7. SQL 必须完整闭合，CTE、SELECT、GROUP BY、ORDER BY 不得省略，不要输出截断的半句 SQL
8. SELECT 只返回用户明确请求的维度和指标；计算毛利、利润等派生指标时，不要额外输出收入、成本等中间指标
9. 面向用户展示的所有 SELECT 输出列必须使用简洁、准确的中文别名，例如 warehouse_name AS `仓库名称`、SUM(...) AS `可用库存`；SQL 表名、字段名和内部 CTE 名仍使用 Schema 中的英文名称
10. 禁止输出英文结果别名，例如 available_inventory、gross_profit、order_count；必须分别写成 `可用库存`、`毛利`、`订单数量` 等中文别名
11. 维度必须使用对应业务字段：产品线/业务线对应 dim_products.product_line，技术路线对应 dim_products.tech_route；不得用技术路线代替产品线，也不得只修改中文别名掩盖维度替换
"""
    if use_rules or use_guards:
        prompt += """11. 优先遵循【关键业务规则】和【常见错误防护】中的约束
"""

    prompt += """
请直接输出 SQL：
"""
    return system_msg, prompt
