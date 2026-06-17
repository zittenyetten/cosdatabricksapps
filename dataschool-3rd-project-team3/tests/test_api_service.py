from datetime import datetime

from rbac_rag.api_service import format_api_response


def test_format_api_response_work_success() -> None:
    result = {
        "request_id": "req-1",
        "query_time": datetime(2026, 1, 1, 0, 0, 0),
        "mode": "WORK",
        "status": "SUCCESS",
        "summary": "answer",
        "role": "GENERAL_EMPLOYEE",
        "rbac_enabled": True,
        "post_check": True,
        "sql": "SELECT * FROM cos_adb.silver.events LIMIT 20",
        "columns_returned": ["event_id"],
        "row_count_returned": 1,
        "data": [{"event_id": "E1"}],
        "table_access": [{"table": "cos_adb.silver.events", "result": "SUCCESS"}],
    }

    response = format_api_response(result)

    assert response["answer"] == "answer"
    assert response["generated_sql"] == "SELECT * FROM cos_adb.silver.events LIMIT 20"
    assert response["rows"] == [{"event_id": "E1"}]
    assert response["sources"]["tables"] == ["cos_adb.silver.events"]
    assert response["raw"]["query_time"] == "2026-01-01T00:00:00"


def test_format_api_response_blocks_rows_on_denied() -> None:
    result = {
        "request_id": "req-1",
        "mode": "WORK",
        "status": "DENIED",
        "detail": "denied",
        "role": "GENERAL_EMPLOYEE",
        "rbac_enabled": True,
        "post_check": True,
        "data": [{"secret": "x"}],
        "table_access": [{"table": "cos_adb.gold.secret_table", "result": "DENIED"}],
        "failure_reason": "RBAC_DOMAIN_DENIED",
    }

    response = format_api_response(result)

    assert response["blocked"] is True
    assert response["rows"] == []
    assert response["checks"]["pre_check"] == "BLOCKED"

