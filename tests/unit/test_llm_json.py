from src.smart_data.infrastructure.llm.openai_compatible import parse_json_object


def test_parse_json_object_from_fence():
    payload = parse_json_object('```json\n{"operation": "query", "asset_ids": ["GT-001"]}\n```')
    assert payload["operation"] == "query"
    assert payload["asset_ids"] == ["GT-001"]


def test_parse_json_object_with_prefix():
    payload = parse_json_object('结果如下：{"sql": "SELECT 1", "used_metrics": ["T48_AVG"]}')
    assert payload["sql"] == "SELECT 1"


def test_parse_json_object_with_invalid_single_quote_escape():
    payload = parse_json_object('{"sql":"SELECT * FROM t WHERE asset_id = \\\'GT-001\\\'"}')
    assert "GT-001" in payload["sql"]
