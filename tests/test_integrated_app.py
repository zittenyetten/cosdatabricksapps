import json

from fastapi.testclient import TestClient

from app import main


class FakeRagService:
    def __init__(self, response: dict | None = None) -> None:
        self.calls = []
        self.response = response

    def chat(self, *, event_callback=None, **kwargs):
        self.calls.append(kwargs)
        if event_callback is not None:
            event_callback(
                "accepted",
                {
                    "role_id": kwargs["role_id"],
                    "mode": kwargs["mode"],
                    "post_check": kwargs["post_check"],
                },
            )
            event_callback("intent", {"mode": "WORK"})
        if self.response is not None:
            return self.response
        return {
            "request_id": "req-1",
            "mode": "WORK",
            "status": "SUCCESS",
            "answer": "ok",
            "blocked": False,
            "role_id": kwargs["role_id"],
            "generated_sql": "SELECT 1 LIMIT 20",
            "columns": ["value"],
            "rows": [{"value": 1}],
            "row_count": 1,
            "sources": {"tables": ["cos_adb.silver.events"], "documents": []},
            "checks": {
                "rbac_enabled": True,
                "pre_check": "PASS",
                "post_check": "PASS" if kwargs["post_check"] else "SKIPPED",
            },
            "raw": {},
        }


def install_fake_service(monkeypatch, response: dict | None = None) -> FakeRagService:
    service = FakeRagService(response)
    monkeypatch.setattr(main, "get_rag_service", lambda: service)
    return service


def blocked_table_response() -> dict:
    return {
        "request_id": "req-blocked",
        "mode": "WORK",
        "status": "DENIED",
        "answer": "SQL references non-allowed tables: cos_adb.silver.cs_response_manual",
        "blocked": True,
        "role_id": "CS_STAFF",
        "generated_sql": "SELECT * FROM cos_adb.silver.cs_response_manual LIMIT 20",
        "columns": [],
        "rows": [],
        "row_count": 0,
        "sources": {"tables": [], "documents": []},
        "checks": {"rbac_enabled": True, "pre_check": "PASS", "post_check": "SKIPPED"},
        "raw": {
            "failure_reason": "SQL_VALIDATION_ERROR",
            "detail": "SQL references non-allowed tables: cos_adb.silver.cs_response_manual",
            "sql": "SELECT * FROM cos_adb.silver.cs_response_manual LIMIT 20",
            "table_access": [
                {"table": "cos_adb.silver.cs_response_manual", "result": "DENIED"}
            ],
        },
    }


def test_app_imports_and_health() -> None:
    client = TestClient(main.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_v1_chat_uses_in_process_rag(monkeypatch) -> None:
    install_fake_service(monkeypatch)
    client = TestClient(main.app)

    response = client.post(
        "/v1/chat",
        json={"question": "show data", "role_id": "GENERAL_EMPLOYEE"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "ok"


def test_v1_chat_stream_returns_events(monkeypatch) -> None:
    install_fake_service(monkeypatch)
    client = TestClient(main.app)

    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"question": "show data", "role_id": "GENERAL_EMPLOYEE"},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert "event: accepted" in body
    assert "event: intent" in body
    assert "event: final" in body
    assert json.loads(body.split("data: ")[-1].strip())["answer"] == "ok"


def test_ui_chat_response_shape(monkeypatch) -> None:
    install_fake_service(monkeypatch)
    client = TestClient(main.app)

    response = client.post(
        "/api/chat",
        json={"query": "/work show data", "role_id": "GENERAL_EMPLOYEE"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["backend"] == "in_process_rag"
    assert payload["effective_identity"]["role_id"] == "GENERAL_EMPLOYEE"
    assert payload["guard_status"] == "SUCCESS"
    assert payload["answer"] == "ok"
    assert payload["sql_log"] == {}


def test_ui_stream_final_is_ui_shape(monkeypatch) -> None:
    install_fake_service(monkeypatch)
    client = TestClient(main.app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"query": "/work show data", "role_id": "GENERAL_EMPLOYEE"},
    ) as response:
        body = response.read().decode("utf-8")

    final_payload = json.loads(body.split("data: ")[-1].strip())
    assert response.status_code == 200
    assert final_payload["backend"] == "in_process_rag"
    assert final_payload["effective_identity"]["role_id"] == "GENERAL_EMPLOYEE"


def test_admin_stream_final_is_ui_shape(monkeypatch) -> None:
    install_fake_service(monkeypatch)
    client = TestClient(main.app)

    with client.stream(
        "POST",
        "/api/admin/simulate/stream",
        json={
            "query": "/work show data",
            "role_id": "QA_MANAGER",
            "department_name": "QA",
            "security_clearance": "RESTRICTED",
        },
    ) as response:
        body = response.read().decode("utf-8")

    final_payload = json.loads(body.split("data: ")[-1].strip())
    assert response.status_code == 200
    assert final_payload["backend"] == "in_process_rag"
    assert final_payload["effective_identity"]["role_id"] == "QA_MANAGER"


def test_admin_stream_respects_post_check_disabled(monkeypatch) -> None:
    service = install_fake_service(monkeypatch)
    client = TestClient(main.app)

    with client.stream(
        "POST",
        "/api/admin/simulate/stream",
        json={
            "query": "/work show data",
            "role_id": "QA_MANAGER",
            "department_name": "QA",
            "security_clearance": "RESTRICTED",
            "post_check_enabled": False,
        },
    ) as response:
        body = response.read().decode("utf-8")

    final_payload = json.loads(body.split("data: ")[-1].strip())
    assert response.status_code == 200
    assert service.calls[-1]["post_check"] is False
    assert final_payload["checks"]["post_check"] == "SKIPPED"


def test_execute_rag_chat_coerces_string_false_options(monkeypatch) -> None:
    service = install_fake_service(monkeypatch)

    main.execute_rag_chat(
        {
            "query": "/work show data",
            "role_id": "QA_MANAGER",
            "rbac_enabled": "false",
            "post_check_enabled": "false",
        }
    )

    assert service.calls[-1]["rbac_enabled"] is False
    assert service.calls[-1]["post_check"] is False


def test_public_chat_redacts_internal_table_names(monkeypatch) -> None:
    install_fake_service(monkeypatch, blocked_table_response())
    client = TestClient(main.app)

    response = client.post(
        "/api/chat",
        json={"query": "/work show data", "role_id": "CS_STAFF"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert "cos_adb" not in payload["answer"]
    assert "SQL references" not in payload["answer"]
    assert payload["raw"] == {"redacted": True}
    assert payload["sql_log"] == {}


def test_admin_simulation_keeps_internal_table_names(monkeypatch) -> None:
    install_fake_service(monkeypatch, blocked_table_response())
    client = TestClient(main.app)

    response = client.post(
        "/api/admin/simulate",
        json={
            "query": "/work show data",
            "role_id": "CS_STAFF",
            "department_name": "CS",
            "security_clearance": "CONFIDENTIAL",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert "cos_adb.silver.cs_response_manual" in payload["answer"]
    assert "cos_adb.silver.cs_response_manual" in json.dumps(payload["raw"])


def test_public_chat_stream_redacts_internal_table_names(monkeypatch) -> None:
    install_fake_service(monkeypatch, blocked_table_response())
    client = TestClient(main.app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"query": "/work show data", "role_id": "CS_STAFF"},
    ) as response:
        body = response.read().decode("utf-8")

    final_payload = json.loads(body.split("data: ")[-1].strip())
    assert response.status_code == 200
    assert "cos_adb" not in final_payload["answer"]
    assert final_payload["raw"] == {"redacted": True}
