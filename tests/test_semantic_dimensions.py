from chatbi.infrastructure.simple_semantic import ChromaSimpleSemanticPipeline
from indicator_knowledge import IndicatorKnowledge


def test_product_line_field_survives_unrelated_vector_recall() -> None:
    class Fields:
        def query(self, **kwargs):
            return {"metadatas": [[{"table": "dim_products", "field": "tech_route"}]]}

        def count(self):
            return 1

    semantic = ChromaSimpleSemanticPipeline.__new__(ChromaSimpleSemanticPipeline)
    from chatbi.settings import Settings
    from chatbi.infrastructure.simple_semantic import LocalFeatureEncoder
    semantic.settings = Settings(_env_file=None)
    semantic.encoder = LocalFeatureEncoder()
    semantic.fields = Fields()
    semantic.indicator_knowledge = IndicatorKnowledge()
    fields = semantic._retrieve_fields("各产品线的毛利是多少", ["dim_products"], ["毛利"])
    assert "dim_products.product_line" in fields
    schema = semantic._build_schema(["dim_products"], fields, [])
    assert "dim_products.product_line：产品线" in schema
    assert "dim_products.tech_route：技术路线" in schema
