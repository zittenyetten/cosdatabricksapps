import json

from fastapi.testclient import TestClient

from app import main


class FakeRagService:
    def chat(self, *, event_callback=None, **kwargs):
        if event_callback is not None:
            event_callback("accepted", {"role_id": kwargs["role_id"], "mode": kwargs["mode"]})
            event_callback("intent", {"mode": "WORK"})
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
            "checks": {"rbac_enabled": True, "pre_check": "PASS", "post_check": "PASS"},
            "raw": {},
        }


def install_fake_service(monkeypatch) -> None:
    monkeypatch.setattr(main, "get_rag_service", lambda: FakeRagService())


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
    assert payload["sql_log"]["generated_sql"] == "SELECT 1 LIMIT 20"


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

