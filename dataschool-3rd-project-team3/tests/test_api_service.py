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


def test_format_api_response_marks_post_check_skipped() -> None:
    result = {
        "request_id": "req-1",
        "mode": "WORK",
        "status": "SUCCESS",
        "summary": "answer",
        "role": "GENERAL_EMPLOYEE",
        "rbac_enabled": True,
        "post_check": False,
        "data": [],
        "table_access": [],
    }

    response = format_api_response(result)

    assert response["checks"]["post_check"] == "SKIPPED"


def test_format_api_response_marks_post_check_blocked() -> None:
    result = {
        "request_id": "req-1",
        "mode": "WORK",
        "status": "DENIED",
        "detail": "blocked by post-check",
        "role": "GENERAL_EMPLOYEE",
        "rbac_enabled": True,
        "post_check": True,
        "data": [{"secret": "x"}],
        "table_access": [{"table": "cos_adb.silver.events", "result": "DENIED"}],
        "failure_reason": "POST_CHECK_FAILED",
    }

    response = format_api_response(result)

    assert response["blocked"] is True
    assert response["rows"] == []
    assert response["checks"]["post_check"] == "BLOCKED"


def test_format_api_response_marks_post_check_skipped_before_execution() -> None:
    result = {
        "request_id": "req-1",
        "mode": "WORK",
        "status": "DENIED",
        "detail": "Only SELECT queries are allowed",
        "role": "GENERAL_EMPLOYEE",
        "rbac_enabled": True,
        "post_check": True,
        "data": None,
        "table_access": [{"table": "cos_adb.silver.events", "result": "DENIED"}],
        "failure_reason": "SQL_VALIDATION_ERROR",
        "execution_status": "BLOCKED",
    }

    response = format_api_response(result)

    assert response["blocked"] is True
    assert response["checks"]["post_check"] == "SKIPPED"


def test_format_api_response_hides_internal_column_validation_detail() -> None:
    result = {
        "request_id": "req-1",
        "mode": "WORK",
        "status": "ERROR",
        "detail": "SQL references unavailable columns: manual_id. Use only these columns: record_id",
        "role": "CS_STAFF",
        "rbac_enabled": True,
        "post_check": True,
        "data": None,
        "table_access": [{"table": "cos_adb.silver.cs_response_manuals", "result": "ERROR"}],
        "failure_reason": "SQL_COLUMN_VALIDATION_ERROR",
        "execution_status": "FAILED",
    }

    response = format_api_response(result)

    assert response["status"] == "ERROR"
    assert "manual_id" not in response["answer"]
    assert "실제 컬럼 구조" in response["answer"]
    assert "manual_id" in response["raw"]["detail"]
    assert response["checks"]["post_check"] == "SKIPPED"
