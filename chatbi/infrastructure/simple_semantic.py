"""Chroma-backed semantic pipeline for simple BI queries."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from chatbi.domain.schema import SchemaSelection, SimpleSemanticSelection
from chatbi.domain.security import DEFAULT_SCHEMA
from chatbi.settings import Settings, get_settings
from indicator_knowledge import IndicatorKnowledge


TABLE_DESCRIPTIONS: dict[str, str] = {
    "dim_customers": "客户维度，客户名称、类型、行业、国家、地区，用于客户和区域分析",
    "dim_products": "产品维度，产品名称、产品线、品类、技术路线、材料成本和人工成本",
    "sales_orders": "销售订单事实，订单日期、状态、数量、价格、收入、币种、客户和产品",
    "exchange_rates": "汇率，按日期和币种记录人民币兑换汇率，用于多币种金额换算",
    "finance_expenses": "期间费用，研发、销售、管理、财务费用及部门费用",
    "dim_regions": "销售区域和大区负责人维度",
    "dim_countries": "国家、ISO代码及所属区域维度",
    "dim_departments": "部门组织架构、部门类型和上级部门",
    "dim_employees": "员工、岗位、入职日期、在职状态和所属部门",
    "dim_suppliers": "供应商、供应商类别、国家、评级和合作状态",
    "dim_warehouses": "仓库、所在区域、容量和仓库状态",
    "dim_sales_channels": "销售渠道、渠道类型和佣金率",
    "dim_currencies": "币种名称、代码、符号和小数位",
    "dim_calendar": "日期、年、季度、月份、工作日的日历维度",
    "dim_cost_centers": "成本中心、所属部门和年度预算",
    "purchase_orders": "采购订单，供应商、采购日期、交期、状态、币种和采购总金额",
    "purchase_order_items": "采购订单明细，采购产品、数量、单价和收货数量",
    "sales_order_items": "销售订单明细，产品、数量、单价、折扣和行收入",
    "inventory_balances": "库存余额快照，仓库产品、现存量、预留量和库存金额",
    "inventory_transactions": "库存出入库流水，仓库产品、时间、类型、数量和来源单据",
    "production_orders": "生产工单，产品、计划数量、开工交期和生产状态",
    "production_outputs": "生产产出，良品数量、不良数量、班次和产出日期",
    "quality_inspections": "质量检验，检验数量、合格数量、缺陷类型和检验结果",
    "equipment": "设备台账，设备编码、类型、部门、购置日期和运行状态",
    "equipment_maintenance": "设备维护，维护类型、停机时长、维护成本和结果",
    "shipments": "销售发运，订单、仓库、承运商、运费、发货和到货状态",
    "shipment_items": "发运明细，发运单、销售订单行和发货数量",
    "customer_payments": "客户回款，应收订单、付款日期、金额、币种、方式和回款状态",
    "supplier_payments": "供应商付款，应付采购单、付款日期、金额、币种和付款状态",
    "budgets": "预算执行，成本中心、年月、预算类别、预算金额和实际金额",
}

FIELD_TERMS: dict[str, str] = {
    "net_amount": "不含税收入 销售额 营收",
    "gross_amount": "含税金额 含税收入",
    "material_cost": "材料成本 原料成本",
    "labor_cost": "人工成本",
    "quantity": "数量 销量",
    "region": "地区 区域 大区 市场",
    "product_line": "产品线 业务线",
    "order_status": "订单状态 完成 取消 待处理",
    "payment_amount": "付款金额 回款金额",
    "budget_amount": "预算金额",
    "actual_amount": "实际金额 预算执行",
    "good_quantity": "良品数量 合格产量",
    "defect_quantity": "不良数量 缺陷数量",
    "passed_quantity": "检验合格数量",
    "downtime_hours": "停机时长",
    "freight_cost": "运费 物流成本",
    "total_amount": "总金额 采购金额",
}

# High-precision business aliases complement vector recall. These are kept
# deliberately specific: generic words such as "订单" or "金额" would pull in
# unrelated fact tables and make the inferred join tree unstable.
TABLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dim_customers": ("客户维度", "客户名称", "客户类型"),
    "dim_products": ("产品维度", "产品线", "产品品类"),
    "sales_orders": ("销售订单", "销售单"),
    "exchange_rates": ("汇率", "币种换算", "人民币换算"),
    "finance_expenses": ("期间费用", "研发费用", "管理费用", "财务费用"),
    "dim_suppliers": ("供应商", "供方"),
    "dim_warehouses": ("仓库", "库房"),
    "purchase_orders": ("采购订单", "采购单", "采购总金额", "采购金额"),
    "purchase_order_items": ("采购订单明细", "采购明细", "收货数量"),
    "sales_order_items": ("销售订单明细", "销售明细", "折扣金额"),
    "inventory_balances": ("库存余额", "库存快照", "现存量", "预留量"),
    "inventory_transactions": ("库存流水", "出入库", "库存交易"),
    "production_orders": ("生产工单", "生产订单"),
    "production_outputs": ("生产产出", "良品数量", "不良数量"),
    "quality_inspections": ("质量检验", "质检", "缺陷类型"),
    "equipment_maintenance": ("设备维护", "设备维修", "停机时长"),
    "shipments": ("发运", "运费", "承运商"),
    "customer_payments": ("客户回款", "客户付款", "应收回款"),
    "supplier_payments": ("供应商付款", "采购付款", "应付付款"),
    "budgets": ("预算执行", "预算金额", "实际金额"),
}

# left_table, left_field, right_table, right_field
RELATIONSHIPS: tuple[tuple[str, str, str, str], ...] = (
    ("sales_orders", "customer_id", "dim_customers", "customer_id"),
    ("sales_orders", "product_id", "dim_products", "product_id"),
    ("sales_orders", "order_date,currency", "exchange_rates", "rate_date,currency"),
    ("dim_countries", "region_id", "dim_regions", "region_id"),
    ("dim_employees", "department_id", "dim_departments", "department_id"),
    ("dim_cost_centers", "department_id", "dim_departments", "department_id"),
    ("purchase_orders", "supplier_id", "dim_suppliers", "supplier_id"),
    ("purchase_order_items", "purchase_order_id", "purchase_orders", "purchase_order_id"),
    ("purchase_order_items", "product_id", "dim_products", "product_id"),
    ("sales_order_items", "order_id", "sales_orders", "order_id"),
    ("sales_order_items", "product_id", "dim_products", "product_id"),
    ("inventory_balances", "warehouse_id", "dim_warehouses", "warehouse_id"),
    ("inventory_balances", "product_id", "dim_products", "product_id"),
    ("inventory_transactions", "warehouse_id", "dim_warehouses", "warehouse_id"),
    ("inventory_transactions", "product_id", "dim_products", "product_id"),
    ("production_orders", "product_id", "dim_products", "product_id"),
    ("production_outputs", "production_order_id", "production_orders", "production_order_id"),
    ("quality_inspections", "production_order_id", "production_orders", "production_order_id"),
    ("equipment", "department_id", "dim_departments", "department_id"),
    ("equipment_maintenance", "equipment_id", "equipment", "equipment_id"),
    ("shipments", "order_id", "sales_orders", "order_id"),
    ("shipments", "warehouse_id", "dim_warehouses", "warehouse_id"),
    ("shipment_items", "shipment_id", "shipments", "shipment_id"),
    ("shipment_items", "sales_order_item_id", "sales_order_items", "sales_order_item_id"),
    ("customer_payments", "customer_id", "dim_customers", "customer_id"),
    ("customer_payments", "order_id", "sales_orders", "order_id"),
    ("supplier_payments", "supplier_id", "dim_suppliers", "supplier_id"),
    ("supplier_payments", "purchase_order_id", "purchase_orders", "purchase_order_id"),
    ("budgets", "cost_center_id", "dim_cost_centers", "cost_center_id"),
)


class LocalFeatureEncoder:
    """Deterministic lexical embeddings for Chinese/English BI metadata."""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def encode(self, text: str) -> list[float]:
        normalized = re.sub(r"\s+", "", text.lower())
        tokens = set(re.findall(r"[a-z0-9_]+", normalized))
        tokens.update(normalized[index:index + 2] for index in range(max(len(normalized) - 1, 0)))
        vector = [0.0] * self.dimensions
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % self.dimensions
            vector[index] += 1.0
        length = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / length for value in vector]

    def encode_many(self, texts: Iterable[str]) -> list[list[float]]:
        return [self.encode(text) for text in texts]


class ChromaSimpleSemanticPipeline:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.encoder = LocalFeatureEncoder()
        self.indicator_knowledge = IndicatorKnowledge()
        # Chroma is imported lazily and accessed over HTTP. The server runs in
        # an isolated Linux VM because native Chroma terminates this Windows
        # process before Python can handle the failure.
        import chromadb

        self.client: Any = chromadb.HttpClient(
            host=self.settings.simple_chroma_host,
            port=self.settings.simple_chroma_port,
            ssl=self.settings.simple_chroma_ssl,
        )
        self.indicators: Any = self.client.get_or_create_collection("indicators", metadata={"hnsw:space": "cosine"})
        self.tables: Any = self.client.get_or_create_collection("tables", metadata={"hnsw:space": "cosine"})
        self.fields: Any = self.client.get_or_create_collection("fields", metadata={"hnsw:space": "cosine"})
        self._ensure_index()

    def resolve(self, question: str) -> SimpleSemanticSelection:
        indicator_names = self._retrieve_indicators(question)
        indicator_text = self._indicator_text(indicator_names)
        table_names = self._retrieve_tables(question, indicator_names)
        field_names = self._retrieve_fields(question, table_names, indicator_names)
        expanded_tables, join_clauses = self._infer_joins(table_names)
        schema_text = self._build_schema(expanded_tables, field_names, join_clauses)
        return SimpleSemanticSelection(
            schema=SchemaSelection(schema_text, "semantic", tuple(expanded_tables), "chroma"),
            indicator_text=indicator_text,
            indicators=tuple(indicator_names),
            fields=tuple(field_names),
            joins=tuple(join_clauses),
            mode="chroma",
        )

    def _ensure_index(self) -> None:
        manifest = self.settings.simple_chroma_dir / "manifest.json"
        valid = False
        if manifest.is_file():
            try:
                valid = json.loads(manifest.read_text(encoding="utf-8")).get("version") == self.settings.simple_schema_index_version
            except (OSError, ValueError):
                valid = False
        expected = (len(self.indicator_knowledge.indicators), len(DEFAULT_SCHEMA), sum(map(len, DEFAULT_SCHEMA.values())))
        if valid and (self.indicators.count(), self.tables.count(), self.fields.count()) == expected:
            return
        for collection in (self.indicators, self.tables, self.fields):
            existing = collection.get(include=[]).get("ids", [])
            if existing:
                collection.delete(ids=existing)
        indicator_docs, indicator_ids, indicator_meta = [], [], []
        for name, item in self.indicator_knowledge.indicators.items():
            text = " ".join([
                name,
                *item.get("aliases", []),
                item["definition"],
                item["formula"],
                item["data_source"],
                item["time_field"],
                *item.get("depends_on", []),
                *item.get("filters", []),
            ])
            indicator_ids.append(f"indicator:{name}")
            indicator_docs.append(text)
            indicator_meta.append({"name": name})
        self.indicators.add(ids=indicator_ids, documents=indicator_docs,
                            embeddings=self.encoder.encode_many(indicator_docs), metadatas=indicator_meta)
        table_ids = [f"table:{name}" for name in DEFAULT_SCHEMA]
        table_docs = [f"{name} {TABLE_DESCRIPTIONS[name]} 字段 {' '.join(sorted(DEFAULT_SCHEMA[name]))}" for name in DEFAULT_SCHEMA]
        self.tables.add(ids=table_ids, documents=table_docs, embeddings=self.encoder.encode_many(table_docs),
                        metadatas=[{"table": name} for name in DEFAULT_SCHEMA])
        field_ids, field_docs, field_meta = [], [], []
        for table, fields in DEFAULT_SCHEMA.items():
            for field in sorted(fields):
                field_ids.append(f"field:{table}.{field}")
                field_docs.append(f"{table}.{field} {TABLE_DESCRIPTIONS[table]} {FIELD_TERMS.get(field, field.replace('_', ' '))}")
                field_meta.append({"table": table, "field": field})
        self.fields.add(ids=field_ids, documents=field_docs, embeddings=self.encoder.encode_many(field_docs), metadatas=field_meta)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"version": self.settings.simple_schema_index_version,
                                        "indicators": expected[0], "tables": expected[1], "fields": expected[2]},
                                       ensure_ascii=False, indent=2), encoding="utf-8")

    def _retrieve_indicators(self, question: str) -> list[str]:
        exact = self.indicator_knowledge.detect_indicators(question)
        # Governed names and aliases are authoritative. Do not broaden an
        # exact metric such as "毛利" into the related but different "利润".
        if exact:
            return exact
        result = self.indicators.query(query_embeddings=[self.encoder.encode(question)], n_results=min(3, self.indicators.count()))
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        # A permissive cutoff creates false indicator matches for ordinary
        # business nouns (for example, procurement amount being treated as
        # profit). Exact aliases always win; semantic additions must be close.
        ranked = [
            str(meta["name"])
            for meta, distance in zip(metadatas, distances)
            if meta and distance <= self.settings.simple_indicator_max_distance
        ]
        return list(dict.fromkeys(ranked))

    def _retrieve_tables(self, question: str, indicators: list[str]) -> list[str]:
        exact_tables = [
            table
            for table, keywords in TABLE_KEYWORDS.items()
            if any(keyword.lower() in question.lower() for keyword in keywords)
        ]
        indicator_tables: list[str] = []
        for name in indicators:
            source = self.indicator_knowledge.indicators[name]["data_source"]
            indicator_tables.extend(
                table for table in DEFAULT_SCHEMA if re.search(rf"\b{re.escape(table)}\b", source)
            )
        # High-confidence lexical matches and governed indicator definitions
        # are sufficient for a compact schema. Vector table recall is the
        # fallback for questions that do not contain a known business alias.
        if exact_tables or indicator_tables:
            return list(dict.fromkeys([*exact_tables, *indicator_tables]))

        indicator_context = " ".join(self.indicator_knowledge.get_indicator_text(name) for name in indicators)
        result = self.tables.query(query_embeddings=[self.encoder.encode(f"{question} {indicator_context}")],
                                   n_results=min(self.settings.simple_table_top_k, self.tables.count()))
        tables = [str(meta["table"]) for meta in result.get("metadatas", [[]])[0] if meta]
        return list(dict.fromkeys(tables))

    def _retrieve_fields(self, question: str, tables: list[str], indicators: list[str]) -> list[str]:
        where: dict[str, Any] | None = {"table": {"$in": tables}} if tables else None
        result = self.fields.query(query_embeddings=[self.encoder.encode(question)],
                                   n_results=min(self.settings.simple_field_top_k, self.fields.count()), where=where)
        fields = [f"{meta['table']}.{meta['field']}" for meta in result.get("metadatas", [[]])[0] if meta]
        knowledge = " ".join(self.indicator_knowledge.get_indicator_text(name) for name in indicators)
        for table in tables:
            for field in DEFAULT_SCHEMA[table]:
                if re.search(rf"\b{re.escape(field)}\b", knowledge):
                    fields.append(f"{table}.{field}")
        return list(dict.fromkeys(fields))

    def _infer_joins(self, requested: list[str]) -> tuple[list[str], list[str]]:
        if len(requested) < 2:
            return requested, []
        adjacency: dict[str, list[tuple[str, tuple[str, str, str, str]]]] = {table: [] for table in DEFAULT_SCHEMA}
        for edge in RELATIONSHIPS:
            left, _, right, _ = edge
            adjacency[left].append((right, edge))
            adjacency[right].append((left, edge))
        selected = [requested[0]]
        edges: list[tuple[str, str, str, str]] = []
        connected = {requested[0]}
        for target in requested[1:]:
            if target in connected:
                continue
            queue: deque[tuple[str, list[tuple[str, str, str, str]]]] = deque(
                (node, []) for node in connected
            )
            visited = set(connected)
            path: list[tuple[str, str, str, str]] | None = None
            while queue:
                node, current = queue.popleft()
                if node == target:
                    path = current
                    break
                for neighbor, edge in adjacency[node]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, [*current, edge]))
            if path is None:
                if target not in selected:
                    selected.append(target)
                continue
            for edge in path:
                if edge not in edges:
                    edges.append(edge)
                for table in (edge[0], edge[2]):
                    if table not in selected:
                        selected.append(table)
                    connected.add(table)
        joins = [self._format_join(*edge) for edge in edges]
        return selected, joins

    @staticmethod
    def _format_join(left: str, left_fields: str, right: str, right_fields: str) -> str:
        pairs = zip(left_fields.split(","), right_fields.split(","))
        condition = " AND ".join(f"{left}.{lf} = {right}.{rf}" for lf, rf in pairs)
        return f"关联：{condition}"

    def _build_schema(self, tables: list[str], fields: list[str], joins: list[str]) -> str:
        selected_by_table: dict[str, set[str]] = {table: set() for table in tables}
        for value in fields:
            table, field = value.split(".", 1)
            if table in selected_by_table:
                selected_by_table[table].add(field)
        for left, left_fields, right, right_fields in RELATIONSHIPS:
            if left in selected_by_table and right in selected_by_table:
                selected_by_table[left].update(left_fields.split(","))
                selected_by_table[right].update(right_fields.split(","))
        lines = ["【Chroma 动态精简 Schema】"]
        for table in tables:
            selected = selected_by_table[table] or set(DEFAULT_SCHEMA[table])
            lines.append(f"表：{table}（{TABLE_DESCRIPTIONS[table]}）")
            lines.append(f"- 字段：{', '.join(sorted(selected))}")
        lines.extend(joins)
        return "\n".join(lines)

    def _indicator_text(self, names: list[str]) -> str:
        return self.indicator_knowledge.build_knowledge_for_names(
            names,
            title="【Chroma 指标知识召回】",
        )
