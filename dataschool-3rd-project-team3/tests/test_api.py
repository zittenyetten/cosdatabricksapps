import json

from fastapi.testclient import TestClient

from api import main


class FakeService:
    def chat(self, *, event_callback=None, **kwargs):
        if event_callback is not None:
            event_callback("accepted", {"role_id": kwargs["role_id"]})
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


def test_chat_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(main, "get_service", lambda: FakeService())
    client = TestClient(main.app)

    response = client.post(
        "/v1/chat",
        json={"question": "show data", "role_id": "GENERAL_EMPLOYEE"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "ok"


def test_chat_stream_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(main, "get_service", lambda: FakeService())
    client = TestClient(main.app)

    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"question": "show data", "role_id": "GENERAL_EMPLOYEE"},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert "event: accepted" in body
    assert "event: final" in body
    assert json.loads(body.split("data: ")[-1].strip())["answer"] == "ok"

